// SPDX-FileCopyrightText: 2026 Kenta IDA <fuga@fugafuga.org>
// SPDX-License-Identifier: BSL-1.0
//
// See spotpear_lcd.hpp. Init sequence mirrors the hardware-verified lcd_test
// app, except mirror: none (MADCTL 0x08).

#include "spotpear_lcd.hpp"

#if CONFIG_STACKCHAN_BOARD_SPOTPEAR_128CAM

#include <algorithm>
#include <cstring>

#include <driver/spi_master.h>
#include <esp_heap_caps.h>
#include <esp_lcd_gc9a01.h>
#include <esp_lcd_panel_io.h>
#include <esp_lcd_panel_ops.h>
#include <esp_log.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

#include "board/spotpear_128cam_pins.hpp"

namespace stackchan::board {

namespace {
constexpr const char* kTag = "spotpear_lcd";
namespace pins = spotpear_128cam;
constexpr spi_host_device_t kHost = SPI3_HOST; // Factory SPI host.

// Factory post-init writes (factory dump): first bytes 0x18, as M5GFX's
// Panel_GC9A01 table; the vendor esp_lcd_gc9a01 table uses 0x38.
constexpr std::uint8_t kReg62[] = {0x18, 0x0D, 0x71, 0xED, 0x70, 0x70,
                                   0x18, 0x0F, 0x71, 0xEF, 0x70, 0x70};
constexpr std::uint8_t kReg63[] = {0x18, 0x11, 0x71, 0xF1, 0x70, 0x70,
                                   0x18, 0x13, 0x71, 0xF3, 0x70, 0x70};

bool on_color_done(esp_lcd_panel_io_handle_t, esp_lcd_panel_io_event_data_t*,
                   void* ctx)
{
    BaseType_t hp = pdFALSE;
    xSemaphoreGiveFromISR(static_cast<SemaphoreHandle_t>(ctx), &hp);
    return hp == pdTRUE;
}
} // namespace

Panel_SpotPearEspLcd::Panel_SpotPearEspLcd()
{
    _write_depth = lgfx::color_depth_t::rgb565_2Byte;
    _read_depth = lgfx::color_depth_t::rgb565_2Byte;
}

lgfx::color_depth_t Panel_SpotPearEspLcd::setColorDepth(lgfx::color_depth_t)
{
    // Frame buffer is fixed RGB565, stored big-endian (lgfx swap565), which is
    // exactly the byte order the GC9A01A expects on the wire.
    _write_depth = lgfx::color_depth_t::rgb565_2Byte;
    _read_depth = lgfx::color_depth_t::rgb565_2Byte;
    return _write_depth;
}

void Panel_SpotPearEspLcd::setInvert(bool invert)
{
    // Hardware colour inversion is part of the panel's native polarity
    // (factory esp_lcd_panel_invert_color(true)); the lgfx-level "invert"
    // flips it relative to that.
    _invert = invert;
    if (panel_) {
        esp_lcd_panel_invert_color(static_cast<esp_lcd_panel_handle_t>(panel_), !invert);
    }
}

void Panel_SpotPearEspLcd::setSleep(bool flg)
{
    if (panel_) esp_lcd_panel_disp_sleep(static_cast<esp_lcd_panel_handle_t>(panel_), flg);
}

void Panel_SpotPearEspLcd::setPowerSave(bool flg)
{
    if (panel_) esp_lcd_panel_disp_on_off(static_cast<esp_lcd_panel_handle_t>(panel_), !flg);
}

bool Panel_SpotPearEspLcd::init_frame_buffer()
{
    const int w = _cfg.panel_width;
    const int h = _cfg.panel_height;
    const size_t line_bytes = static_cast<size_t>(w) * 2;
    auto lines = static_cast<std::uint8_t**>(
        heap_caps_malloc(h * sizeof(std::uint8_t*), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
    frame_ = static_cast<std::uint8_t*>(
        heap_caps_malloc(line_bytes * h, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!frame_) {
        frame_ = static_cast<std::uint8_t*>(
            heap_caps_malloc(line_bytes * h, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
    }
    band_ = static_cast<std::uint8_t*>(heap_caps_malloc(
        line_bytes * kBandLines, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL));
    if (!lines || !frame_ || !band_) {
        ESP_LOGE(kTag, "frame buffer alloc failed (lines=%p frame=%p band=%p)",
                 lines, frame_, band_);
        return false;
    }
    std::memset(frame_, 0, line_bytes * h);
    for (int y = 0; y < h; ++y) lines[y] = frame_ + line_bytes * y;
    _lines_buffer = lines;
    return true;
}

bool Panel_SpotPearEspLcd::init_esp_lcd()
{
    auto done = xSemaphoreCreateBinary();
    auto lock = xSemaphoreCreateMutex();
    if (!done || !lock) return false;
    done_ = done;
    lock_ = lock;

    spi_bus_config_t buscfg = {};
    buscfg.mosi_io_num = pins::lcd_mosi;
    buscfg.miso_io_num = -1;
    buscfg.sclk_io_num = pins::lcd_sclk;
    buscfg.quadwp_io_num = -1;
    buscfg.quadhd_io_num = -1;
    buscfg.max_transfer_sz = pins::lcd_width * pins::lcd_height * 2; // factory: 240*240*2
    esp_err_t err = spi_bus_initialize(kHost, &buscfg, SPI_DMA_CH_AUTO);
    if (err != ESP_OK) {
        ESP_LOGE(kTag, "spi_bus_initialize: %s", esp_err_to_name(err));
        return false;
    }

    esp_lcd_panel_io_handle_t io = nullptr;
    esp_lcd_panel_io_spi_config_t iocfg = {};
    iocfg.cs_gpio_num = pins::lcd_cs;
    iocfg.dc_gpio_num = pins::lcd_dc;
    iocfg.spi_mode = 0;
    iocfg.pclk_hz = pins::lcd_spi_freq_hz;
    iocfg.trans_queue_depth = 10;
    iocfg.on_color_trans_done = on_color_done;
    iocfg.user_ctx = done;
    iocfg.lcd_cmd_bits = 8;
    iocfg.lcd_param_bits = 8;
    err = esp_lcd_new_panel_io_spi(static_cast<esp_lcd_spi_bus_handle_t>(kHost), &iocfg, &io);
    if (err != ESP_OK) {
        ESP_LOGE(kTag, "esp_lcd_new_panel_io_spi: %s", esp_err_to_name(err));
        return false;
    }
    io_ = io;

    esp_lcd_panel_handle_t panel = nullptr;
    esp_lcd_panel_dev_config_t pcfg = {};
    pcfg.reset_gpio_num = pins::lcd_rst; // -1 -> SWRESET (0x01) + 20 ms
    pcfg.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_BGR;
    pcfg.bits_per_pixel = 16;
    err = esp_lcd_new_panel_gc9a01(io, &pcfg, &panel);
    if (err != ESP_OK) {
        ESP_LOGE(kTag, "esp_lcd_new_panel_gc9a01: %s", esp_err_to_name(err));
        return false;
    }
    bool ok = esp_lcd_panel_reset(panel) == ESP_OK
           && esp_lcd_panel_init(panel) == ESP_OK
           && esp_lcd_panel_invert_color(panel, true) == ESP_OK
           && esp_lcd_panel_mirror(panel, false, false) == ESP_OK
           && esp_lcd_panel_swap_xy(panel, false) == ESP_OK
           && esp_lcd_panel_disp_on_off(panel, true) == ESP_OK
           && esp_lcd_panel_io_tx_param(io, 0x62, kReg62, sizeof(kReg62)) == ESP_OK
           && esp_lcd_panel_io_tx_param(io, 0x63, kReg63, sizeof(kReg63)) == ESP_OK
           && esp_lcd_panel_io_tx_param(io, 0x36, &kMadctl, 1) == ESP_OK;
    if (!ok) {
        ESP_LOGE(kTag, "GC9A01 init sequence failed");
        return false;
    }
    panel_ = panel;
    return true;
}

bool Panel_SpotPearEspLcd::init(bool use_reset)
{
    if (!_lines_buffer && !init_frame_buffer()) return false;
    if (!panel_ && !init_esp_lcd()) return false;
    // pin_cs/pin_rst are -1 in the lgfx config, so this only sets up the
    // lgfx bookkeeping and the backlight (Light_PWM, brightness 0).
    if (!lgfx::Panel_FrameBufferBase::init(use_reset)) return false;
    _auto_display = true; // push dirty rect on every outermost endWrite()
    return true;
}

void Panel_SpotPearEspLcd::push_rect(int x0, int y0, int x1, int y1)
{
    auto panel = static_cast<esp_lcd_panel_handle_t>(panel_);
    auto done = static_cast<SemaphoreHandle_t>(done_);
    const int w = x1 - x0 + 1;
    const size_t row_bytes = static_cast<size_t>(w) * 2;
    for (int yb = y0; yb <= y1; yb += kBandLines) {
        const int lines = std::min(kBandLines, y1 - yb + 1);
        for (int i = 0; i < lines; ++i) {
            std::memcpy(band_ + row_bytes * i, _lines_buffer[yb + i] + x0 * 2, row_bytes);
        }
        esp_err_t err = esp_lcd_panel_draw_bitmap(panel, x0, yb, x1 + 1, yb + lines, band_);
        if (err != ESP_OK) {
            ESP_LOGE(kTag, "draw_bitmap y=%d: %s", yb, esp_err_to_name(err));
            return;
        }
        // band_ is reused: wait until the DMA has finished reading it.
        if (xSemaphoreTake(done, pdMS_TO_TICKS(500)) != pdTRUE) {
            ESP_LOGE(kTag, "color transfer timeout y=%d", yb);
            return;
        }
    }
}

void Panel_SpotPearEspLcd::display(uint_fast16_t x, uint_fast16_t y, uint_fast16_t w,
                                   uint_fast16_t h)
{
    if (0 < w && 0 < h) {
        uint_fast8_t r = _internal_rotation;
        if (r) {
            if ((1u << r) & 0b10010110) { y = _height - (y + h); }
            if (r & 2) { x = _width - (x + w); }
            if (r & 1) { std::swap(x, y); std::swap(w, h); }
        }
        _range_mod.left = std::min<int_fast16_t>(_range_mod.left, x);
        _range_mod.right = std::max<int_fast16_t>(_range_mod.right, x + w - 1);
        _range_mod.top = std::min<int_fast16_t>(_range_mod.top, y);
        _range_mod.bottom = std::max<int_fast16_t>(_range_mod.bottom, y + h - 1);
    }
    if (_range_mod.empty() || !panel_) return;

    const int x0 = std::max<int>(0, _range_mod.left);
    const int y0 = std::max<int>(0, _range_mod.top);
    const int x1 = std::min<int>(_cfg.panel_width - 1, _range_mod.right);
    const int y1 = std::min<int>(_cfg.panel_height - 1, _range_mod.bottom);
    _range_mod.top = INT16_MAX;
    _range_mod.left = INT16_MAX;
    _range_mod.right = 0;
    _range_mod.bottom = 0;
    if (x0 > x1 || y0 > y1) return;

    auto lock = static_cast<SemaphoreHandle_t>(lock_);
    xSemaphoreTake(lock, portMAX_DELAY);
    push_rect(x0, y0, x1, y1);
    xSemaphoreGive(lock);
}

} // namespace stackchan::board

#endif // CONFIG_STACKCHAN_BOARD_SPOTPEAR_128CAM
