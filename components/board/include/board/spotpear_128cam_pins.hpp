// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: BSL-1.0

#pragma once

#include <driver/gpio.h>

namespace stackchan::board::spotpear_128cam {
// [S] SpotPear ESP32S3-1.28inch-AI-CAM schematic, A1 (2026-05-23).
// Full source links and assembly caveats: docs/hardware.md, SpotPear pin map.
inline constexpr gpio_num_t lcd_sclk = GPIO_NUM_4; // verified [S] LCD_SCL
inline constexpr gpio_num_t lcd_mosi = GPIO_NUM_2; // verified [S] LCD_SDA
inline constexpr gpio_num_t lcd_cs = GPIO_NUM_5; // verified [S]
inline constexpr gpio_num_t lcd_dc = GPIO_NUM_47; // verified [S]
inline constexpr gpio_num_t lcd_backlight = GPIO_NUM_42; // verified pin [S]; polarity guessed, R78/R79 assembly-option
inline constexpr gpio_num_t i2c_sda = GPIO_NUM_15; // verified [S] codecs + camera SCCB
inline constexpr gpio_num_t i2c_scl = GPIO_NUM_14; // verified [S]
inline constexpr gpio_num_t i2s_mclk = GPIO_NUM_16; // verified [S]
inline constexpr gpio_num_t i2s_bclk = GPIO_NUM_9; // verified [S]
inline constexpr gpio_num_t i2s_ws = GPIO_NUM_45; // verified [S]
inline constexpr gpio_num_t i2s_dout = GPIO_NUM_8; // verified [S] ESP to ES8311
inline constexpr gpio_num_t i2s_din = GPIO_NUM_10; // verified [S] ES7210 SDOUT1 via R57
inline constexpr gpio_num_t pa_enable = GPIO_NUM_46; // verified [S]; active high per amp datasheet
inline constexpr gpio_num_t boot_button = GPIO_NUM_0; // verified [S]; active low
inline constexpr gpio_num_t battery_adc = GPIO_NUM_1; // verified pin [S]; divider/curve unverified, TODO(ticket 14)
inline constexpr int lcd_miso = -1; // verified [S]: write-only SPI
inline constexpr int lcd_rst = -1; // verified [S]: RC reset, not box variant GPIO38
inline constexpr int lcd_width = 240; // guessed for CAM, verified for sibling box
inline constexpr int lcd_height = 240; // guessed for CAM, verified for sibling box
inline constexpr int es8311_address = 0x18; // verified [S]: CE low
inline constexpr int es7210_address = 0x40; // verified [S]: AD0/AD1 low
// GPIO48 LED/camera D7 is an assembly-option [S]; treat LED as absent.
} // namespace stackchan::board::spotpear_128cam
