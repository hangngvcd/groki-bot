// SPDX-FileCopyrightText: 2026 Kenta IDA <fuga@fugafuga.org>
// SPDX-License-Identifier: BSL-1.0
//
// SpotPear ESP32-S3 1.28" CAM display backend (spotpear128cam only).
//
// The M5GFX Bus_SPI + Panel_GC9A01 path (commit 631a166) left the GC9A01A
// showing power-on static on real hardware, i.e. no command ever took effect.
// The stand-alone esp_lcd test app (lcd_test, hardware-verified) drives the
// same panel correctly, so this backend uses exactly that path:
//   esp_lcd SPI3 @ 40 MHz mode 0, CS 5, DC 47, no RST pin (SWRESET),
//   espressif/esp_lcd_gc9a01, BGR, invert on, factory 0x62/0x63, NO mirror
//   (MADCTL 0x08; hardware-verified, factory mirror X shows mirrored text).
//
// The panel is an lgfx::Panel_FrameBufferBase, so M5.Display (M5GFX) keeps its
// full drawing API: every draw lands in a 240x240 RGB565 frame buffer and the
// dirty rectangle is pushed with esp_lcd_panel_draw_bitmap in DMA bands at
// endWrite()/display() (auto-display).
#pragma once

#include <sdkconfig.h>

#if CONFIG_STACKCHAN_BOARD_SPOTPEAR_128CAM

#include <cstdint>

#include <M5GFX.h>
#include <lgfx/v1/panel/Panel_FrameBufferBase.hpp>

namespace stackchan::board {

class Panel_SpotPearEspLcd : public lgfx::Panel_FrameBufferBase {
public:
    static constexpr int kBandLines = 24;
    static constexpr std::uint8_t kMadctl = 0x08; // BGR only, no mirror/swap.

    Panel_SpotPearEspLcd();

    bool init(bool use_reset) override;
    lgfx::color_depth_t setColorDepth(lgfx::color_depth_t depth) override;
    void setInvert(bool invert) override;
    void setSleep(bool flg) override;
    void setPowerSave(bool flg) override;
    void display(uint_fast16_t x, uint_fast16_t y, uint_fast16_t w,
                 uint_fast16_t h) override;

    bool esp_lcd_ready() const noexcept { return panel_ != nullptr; }

private:
    bool init_frame_buffer();
    bool init_esp_lcd();
    void push_rect(int x0, int y0, int x1, int y1); // inclusive bounds

    void* io_ = nullptr;    // esp_lcd_panel_io_handle_t
    void* panel_ = nullptr; // esp_lcd_panel_handle_t
    void* done_ = nullptr;  // SemaphoreHandle_t (color transfer done)
    void* lock_ = nullptr;  // SemaphoreHandle_t (push mutex)
    std::uint8_t* frame_ = nullptr;
    std::uint8_t* band_ = nullptr; // internal DMA bounce buffer
};

} // namespace stackchan::board

#endif // CONFIG_STACKCHAN_BOARD_SPOTPEAR_128CAM
