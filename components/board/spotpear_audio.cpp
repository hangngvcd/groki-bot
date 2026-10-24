// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: BSL-1.0

// SpotPear ESP32S3-1.28inch-AI-CAM audio over M5Unified's Speaker/Mic classes.
//
// Sources:
//  [S] SpotPear schematic: ES8311 (CE low → 0x18), ES7210 (AD0/AD1 low → 0x40),
//      I2C SDA15/SCL14, I2S MCLK16/BCLK9/LRCK45/DOUT8/DIN10 (ES7210 SDOUT1 via
//      R57), NS4150B PA CTRL on GPIO46. ES7210 MIC1/MIC2 carry the two
//      electret mics (MICBIAS12); MIC3 is the AEC reference loop; MIC4 unused.
//  [F] Factory firmware (xiaozhi sp-esp32-s3-1.28-cam, ota0 disassembly):
//      BoxAudioCodec(i2c, 24000, 24000, mclk=16, bclk=9, ws=45, dout=8,
//      din=10, pa=-1, es8311=0x30(8-bit)=0x18, es7210=0x80(8-bit)=0x40,
//      input_reference=true) with input gain 30.0 dB (0x41F00000) and
//      xiaozhi's default output volume 70.
//  [M] M5Unified register sequences this mirrors: ES8311 = Atomic ECHO BASE
//      (_speaker_enabled_cb_atomic_echo: same ES8311 + NS4150B chain, MCLK
//      derived from BCLK); ES7210 = CoreS3 (_microphone_enabled_cb_cores3:
//      standard I2S 16-bit on SDOUT1 = MIC1 L / MIC2 R).
//
// Deviations from factory (deliberate): the factory runs one duplex TDM
// channel through esp_codec_dev at 24 kHz with AEC reference; the app here is
// half-duplex M5.Speaker/M5.Mic like every other board, so the ES7210 runs in
// standard I2S mode (no reference channel) and the speaker mixer runs at
// M5.Speaker's 48 kHz. PA is driven from GPIO46 (factory passes pa=-1).

#include "spotpear_audio.hpp"

#include "board/spotpear_128cam_pins.hpp"

#include <M5Unified.h>
#include <driver/gpio.h>
#include <esp_log.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

namespace stackchan::board {
namespace {

constexpr const char* kTag = "spotpear_audio";
namespace pins = spotpear_128cam;

constexpr std::uint32_t kI2cFreq = 400000;
constexpr std::uint8_t kEs8311 = static_cast<std::uint8_t>(pins::es8311_address);
constexpr std::uint8_t kEs7210 = static_cast<std::uint8_t>(pins::es7210_address);
constexpr i2s_port_t kI2sPort = I2S_NUM_1; // Shared, half-duplex (CoreS3 pattern).
// ES7210 MIC1/MIC2 PGA: 0x10 = enable, low nibble = gain step (0x0A = 30 dB).
// 30 dB matches the factory BoxAudioCodec input gain [F].
constexpr std::uint8_t kEs7210MicGain = 0x1A;
// ES8311 DAC digital volume: 0.5 dB steps, 0x00 = -95.5 dB, 0xBF = 0 dB,
// 0xFF = +32 dB. Ticket 15c: the old 0xFF (+32 dB, the M5Unified ECHO BASE
// value) drove the NS4150B into hard clipping — very loud on 鈴鈴 and the
// prime suspect for the USB brownout reboot loop. 0 dB is the factory
// ceiling (xiaozhi's volume 100); the user / parent volume (default 40 %)
// scales M5.Speaker's master volume below it.
constexpr std::uint8_t kEs8311DacVolume = 0xD7;  // +12 dB: 0 dB was far too quiet on the NS4150B (parent, 22:09)
// Soft start: the DAC volume ramps up after the PA is enabled so the amp
// never sees a full-scale step (pop + inrush on USB power).
constexpr std::uint8_t kDacRampStep = 0x18;
constexpr TickType_t kDacRampDelay = pdMS_TO_TICKS(2);
constexpr TickType_t kPaSettle = pdMS_TO_TICKS(5);

struct Reg {
    std::uint8_t reg;
    std::uint8_t value;
};

void write_regs(std::uint8_t addr, const Reg* regs, std::size_t n)
{
    for (std::size_t i = 0; i < n; ++i) {
        bool ok = false;
        for (int retry = 0; retry < 3 && !ok; ++retry) {
            ok = M5.In_I2C.writeRegister8(addr, regs[i].reg, regs[i].value, kI2cFreq);
            if (!ok) vTaskDelay(1);
        }
        if (!ok) {
            ESP_LOGW(kTag, "I2C write 0x%02X reg 0x%02X=0x%02X failed", addr, regs[i].reg, regs[i].value);
        }
    }
}

bool speaker_enable_cb(void*, bool enabled)
{
    if (enabled) {
        static constexpr Reg on[] = {
            {0x00, 0x80}, // RESET: CSM power on, slave mode
            {0x01, 0xB5}, // CLK_MANAGER1: MCLK from BCLK (SCLK), clocks on
            {0x02, 0x18}, // CLK_MANAGER2: MULT_PRE = x8 (BCLK 32fs -> 256fs)
            {0x09, 0x0C}, // SDP_IN: I2S, 16-bit (M5.Speaker slots are 16-bit)
            {0x0A, 0x4C}, // SDP_OUT: muted (ES8311 ADC never drives the bus), 16-bit
            {0x0D, 0x01}, // SYSTEM: power up analog circuitry
            {0x12, 0x00}, // SYSTEM: power up DAC
            {0x13, 0x10}, // SYSTEM: enable HP/line output drive
            {0x32, 0x00}, // DAC volume muted; ramped up below (soft start)
            {0x37, 0x08}, // DAC: bypass equalizer
        };
        write_regs(kEs8311, on, sizeof(on) / sizeof(on[0]));
        gpio_set_level(pins::pa_enable, 1);
        vTaskDelay(kPaSettle);
        for (unsigned v = kDacRampStep; v < kEs8311DacVolume; v += kDacRampStep) {
            const Reg step{0x32, static_cast<std::uint8_t>(v)};
            write_regs(kEs8311, &step, 1);
            vTaskDelay(kDacRampDelay);
        }
        static constexpr Reg full{0x32, kEs8311DacVolume};
        write_regs(kEs8311, &full, 1);
    } else {
        gpio_set_level(pins::pa_enable, 0);
        static constexpr Reg off[] = {
            {0x32, 0x00}, // DAC volume -95.5 dB (mute) while the bus belongs to the mic
        };
        write_regs(kEs8311, off, sizeof(off) / sizeof(off[0]));
    }
    return true;
}

bool mic_enable_cb(void*, bool enabled)
{
    static constexpr Reg reset[] = {{0x00, 0xFF}};
    write_regs(kEs7210, reset, 1);
    if (enabled) {
        static constexpr Reg on[] = {
            {0x00, 0x41}, // RESET_CTL
            {0x01, 0x1F}, // CLK_ON_OFF
            {0x06, 0x00}, // DIGITAL_PDN
            {0x07, 0x20}, // ADC_OSR
            {0x08, 0x10}, // MODE_CFG
            {0x09, 0x30}, // TCT0_CHPINI
            {0x0A, 0x30}, // TCT1_CHPINI
            {0x20, 0x0A}, // ADC34_HPF2
            {0x21, 0x2A}, // ADC34_HPF1
            {0x22, 0x0A}, // ADC12_HPF2
            {0x23, 0x2A}, // ADC12_HPF1
            {0x02, 0xC1}, // MAINCLK
            {0x04, 0x01}, // LRCK_DIVH
            {0x05, 0x00}, // LRCK_DIVL
            {0x11, 0x60}, // SDP_INTERFACE1: I2S, 16-bit (normal, not TDM)
            {0x40, 0x42}, // ANALOG_SYS
            {0x41, 0x70}, // MICBIAS12
            {0x42, 0x70}, // MICBIAS34
            {0x43, kEs7210MicGain}, // MIC1_GAIN
            {0x44, kEs7210MicGain}, // MIC2_GAIN
            {0x45, 0x00}, // MIC3_GAIN (AEC reference, unused)
            {0x46, 0x00}, // MIC4_GAIN (unused)
            {0x47, 0x00}, {0x48, 0x00}, {0x49, 0x00}, {0x4A, 0x00}, // MICx_LP
            {0x4B, 0x00}, // MIC12_PDN: powered
            {0x4C, 0xFF}, // MIC34_PDN: powered down
            {0x01, 0x14}, // CLK_ON_OFF
        };
        write_regs(kEs7210, on, sizeof(on) / sizeof(on[0]));
    }
    return true;
}

bool read_id(std::uint8_t addr, std::uint8_t reg_hi, std::uint8_t reg_lo, std::uint8_t& hi, std::uint8_t& lo)
{
    return M5.In_I2C.readRegister(addr, reg_hi, &hi, 1, kI2cFreq) &&
           M5.In_I2C.readRegister(addr, reg_lo, &lo, 1, kI2cFreq);
}

// setCallback() is protected (only M5Unified's _begin_audio calls it). Taking
// the member pointer through a derived class is the legal way to reach it
// without M5.begin(); the pointer type is the base class member.
struct SpeakerCallbackAccess : m5::Speaker_Class {
    static constexpr auto set = &SpeakerCallbackAccess::setCallback;
};
struct MicCallbackAccess : m5::Mic_Class {
    static constexpr auto set = &MicCallbackAccess::setCallback;
};

void pa_force_low() noexcept
{
    gpio_reset_pin(pins::pa_enable);
    gpio_set_direction(pins::pa_enable, GPIO_MODE_OUTPUT);
    gpio_pulldown_en(pins::pa_enable);
    gpio_set_level(pins::pa_enable, 0);
}

// Ticket 15c: drive the NS4150B PA enable LOW as early as the app can — this
// static initialiser runs in do_global_ctors, before app_main / display init —
// so the amp never amplifies I2S/codec noise while the board boots. It stays
// low except inside speaker_enable_cb(true) (actual playback).
[[maybe_unused]] const bool g_pa_low_at_boot = (pa_force_low(), true);

} // namespace

void spotpear_audio_begin() noexcept
{
    // PA off until the first playback (avoids a pop while the codec is unset).
    pa_force_low();

    if (!M5.In_I2C.begin(I2C_NUM_1, pins::i2c_sda, pins::i2c_scl)) {
        ESP_LOGE(kTag, "I2C begin failed (port 1 sda=%d scl=%d)", static_cast<int>(pins::i2c_sda),
                 static_cast<int>(pins::i2c_scl));
    }

    std::uint8_t hi = 0, lo = 0;
    if (M5.In_I2C.scanID(kEs8311, 100000) && read_id(kEs8311, 0xFD, 0xFE, hi, lo)) {
        ESP_LOGI(kTag, "ES8311 detected at 0x%02X (chip id %02X%02X, expect 8311)", kEs8311, hi, lo);
    } else {
        ESP_LOGE(kTag, "ES8311 NOT found at 0x%02X (I2C sda=%d scl=%d)", kEs8311,
                 static_cast<int>(pins::i2c_sda), static_cast<int>(pins::i2c_scl));
    }
    if (M5.In_I2C.scanID(kEs7210, 100000) && read_id(kEs7210, 0x3D, 0x3E, hi, lo)) {
        ESP_LOGI(kTag, "ES7210 detected at 0x%02X (chip id %02X%02X, expect 7210)", kEs7210, hi, lo);
    } else {
        ESP_LOGE(kTag, "ES7210 NOT found at 0x%02X (I2C sda=%d scl=%d)", kEs7210,
                 static_cast<int>(pins::i2c_sda), static_cast<int>(pins::i2c_scl));
    }

    auto spk = M5.Speaker.config();
    spk.pin_mck = I2S_PIN_NO_CHANGE; // ES8311 derives MCLK from BCLK.
    spk.pin_bck = pins::i2s_bclk;
    spk.pin_ws = pins::i2s_ws;
    spk.pin_data_out = pins::i2s_dout;
    spk.magnification = 1; // ECHO BASE (ES8311 + NS4150B) value.
    spk.i2s_port = kI2sPort;
    (M5.Speaker.*SpeakerCallbackAccess::set)(nullptr, speaker_enable_cb);
    M5.Speaker.config(spk);

    auto mic = M5.Mic.config();
    mic.pin_mck = pins::i2s_mclk; // ES7210 needs MCLK.
    mic.pin_bck = pins::i2s_bclk;
    mic.pin_ws = pins::i2s_ws;
    mic.pin_data_in = pins::i2s_din;
    mic.magnification = 2;   // CoreS3 ES7210 values.
    mic.over_sampling = 1;
    mic.input_channel = m5::input_channel_t::input_stereo; // SDOUT1 = MIC1 L / MIC2 R
    mic.i2s_port = kI2sPort;
    (M5.Mic.*MicCallbackAccess::set)(nullptr, mic_enable_cb);
    M5.Mic.config(mic);

    ESP_LOGI(kTag,
             "audio: I2S%d std 16-bit mclk=%d bclk=%d ws=%d dout=%d din=%d pa=%d(active-high); "
             "spk mixer %lu Hz (ES8311 MCLK=BCLKx8, DAC vol 0x%02X, mag %u); "
             "mic ES7210 MIC1/MIC2 I2S gain 30 dB (0x%02X, factory 30.0), MIC3/4 off",
             static_cast<int>(kI2sPort), static_cast<int>(pins::i2s_mclk), static_cast<int>(pins::i2s_bclk),
             static_cast<int>(pins::i2s_ws), static_cast<int>(pins::i2s_dout), static_cast<int>(pins::i2s_din),
             static_cast<int>(pins::pa_enable), static_cast<unsigned long>(spk.sample_rate), kEs8311DacVolume,
             static_cast<unsigned>(spk.magnification), kEs7210MicGain);
}

} // namespace stackchan::board
