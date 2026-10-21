[English](hardware.md) | [中文](hardware.zh-CN.md) | [日本語](hardware.ja.md)

# Hardware



### Standard unit: CoreS3 + Stack-chan base

![I2C devices, display and UART1 dual-servo wiring between CoreS3 and the Stack-chan base](images/hardware.svg)

| Item | Spec |
|---|---|
| SoC | ESP32-S3R8 |
| PSRAM | 8 MB, in-package Quad SPI, 80 MHz |
| Flash | 16 MB external SPI (W25Q128 package) |
| Display | 320×240 IPS touch screen |
| Servos | SCS0009 ×2 (yaw / pitch) |
| Servo bus | UART1, TX GPIO 6 / RX GPIO 7, 1 Mbps, 8N1 |
| Servo ID / zero | Yaw ID 1, zero 460; pitch ID 2, zero 620 |
| Step | 1 step ≈ 0.3125° (`deg = (raw - zero) * 5 / 16`) |
| Default soft limits | Yaw ±40°, pitch -10° to +25° |
| Head touch | Si12T, I²C 0x68, three zones (front / middle / back) |
| Battery gauge | INA226, I²C 0x41 |
| IO expander | PY32, I²C 0x6F; pin 0 controls servo VM power (wait 200 ms after enabling before using the bus) |
| PMIC | AXP2101, I²C 0x34 (managed by M5Unified) |
| LCD touch | I²C 0x38 (managed by M5Unified) |

Takao Base runs the same `cores3` firmware: servos use Port A (TX GPIO 2 / RX GPIO 1, half duplex with echo cancellation), with no servo power control and no INA226.

### Other supported boards

| Board | Build slug | Display | Notes |
|---|---|---|---|
| CoreS3 + Stack-chan base | `cores3` | 320×240 IPS + touch | Default. Two servo axes, head touch, INA226 |
| CoreS3 + Takao Base | `cores3` | Same | Half-duplex servos on Port A; no servo power control or battery gauge |
| AtomS3R + Atomic ECHO BASE | `atoms3r` | 128×128 LCD | No servos. 8 MB Octal PSRAM, 8 MB flash, ES8311, BtnA toggles UI / AP |
| AtomS3 (no PSRAM) + ECHO BASE | `atoms3` | 128×128 LCD | Slim profile. No conversation, no BLE audio, no RTP |
| M5 StopWatch (C152) | `stopwatch` | 466×466 round AMOLED + touch | No servos. 8 MB Octal PSRAM, 16 MB flash, gaze follows touch, ES8311 |
| SpotPear ESP32-S3 1.28-CAM | `spotpear128cam` | 240×240 round GC9A01A | Skeleton, selected at compile time (`CONFIG_STACKCHAN_BOARD_SPOTPEAR_128CAM`, no autodetect). Builds in CI; hardware bring-up in ticket 14; no servos/touch/LED; camera off; companion face policy on |

The board is detected at boot and reported through `set_board_kind()` to the UI and feature switches. Build with `make build BOARD=<slug>` (default `cores3`). PSRAM mode is fixed at link time per board, so firmware images cannot be swapped between boards.

## Technical specs

| Item | Spec |
|---|---|
| Framework / language | ESP-IDF 5.5 (verified on 5.5.4; 5.4.2 also compiles) / C++20 |
| Target chip | `esp32s3` |
| Face rendering | M5GFX. Full-screen buffer when PSRAM is present; 17 ms budget per frame; full-screen overlays yield the CPU every 33 ms |
| Expressions | 15: Neutral, Happy, Sad, Angry, Doubt, Sleepy, Listening, Thinking, Excited, Curious, Confused, Surprised, Dizzy, Affection, Bored |
| Eye-ring compositing | 48-point contour per eye, blended point by point with four weight sets (`expression`, `expression_from` and two hold layers); ring pool rotates about every 340 ms; blink, one-eye wink and openness squash vertically around the centroid after blending |
| Avatar DSL | `.avdsl` source compiled to `.avbc` bytecode, hot-swappable over BLE or Wi-Fi. The default face is `assets/grok_face.avdsl` |
| Lip sync | 16 kHz mono, 256-point radix-2 FFT; speech-band log energy plus spectral flux; EWMA noise floor follows the environment |
| Servo motion | Trapezoidal velocity `PathGenerator`, torque on only while moving; limits stored in NVS |
| Volume | 0 to 200%, adjustable live from BLE, Wi-Fi or the on-device UI |
| OTA | Dual slots with boot rollback. CoreS3 / StopWatch 4 MiB per slot (`partitions_16mb.csv`); AtomS3R 0x350000 per slot (`partitions_8mb.csv`); AtomS3 3 MiB per slot (`partitions.csv`) |
| BLE | NimBLE, Just Works pairing; application-layer X25519 + AES-256-GCM; optional password |
| Wi-Fi settings | After connecting, HTTP on port 80 serves `settings_wifi.html`, mDNS `stackchan-XXXXXX.local` |
| SoftAP | SSID `Stackchan-XXXXXX` with WPA2; the LCD shows a Wi-Fi QR code; DNS hijack plus an HTTP 404 catch-all opens the captive portal |

## SpotPear ESP32-S3 1.28-CAM pin map

- [ ] Parent confirmation: pending (compare against the board in hand; nothing here has been tested on hardware)

Confidence: **verified** = read from the SpotPear schematic and, where noted, matching the factory image or the Xiaozhi sibling board; **assembly option** = the schematic shows 0R/NC alternatives, the populated one is unknown; **guessed** = not verifiable without hardware.

Sources:
- [S] SpotPear schematic ESP32S3-1.28inch-AI-CAM.pdf (rev A1, 2026-05-23), https://cdn.static.spotpear.cn/uploads/picture/learn/ESP32/ESP32S3-1.28-CAM/ESP32S3-1.28inch-AI-CAM.pdf (linked from the product wiki https://spotpear.cn/wiki/ESP32-S3-1.28-inch-Camera-LCD-Display-Deepseek.html)
- [F] SpotPear factory firmware esp32-s3-1.28-cam-ov5640-CN.bin (16 MiB merged image): strings show xiaozhi board dir main/boards/sp-esp32-s3-1.28-cam, class Spotpear_ESP32_S3_1_28_CAM, BoxAudioCodec (ES8311 + ES7210), esp_lcd_gc9a01, SD card; bootloader header = 16 MB flash, DIO 80 MHz; partition table: ota_0 0x20000 size 0x3f0000, ota_1 0x410000 size 0x3f0000, assets 0x800000 0x600000, wallpaper 0xe00000 0x200000. The board source itself is NOT in upstream 78/xiaozhi-esp32 (only sp-esp32-s3-1.28-box, a sibling board without camera); SpotPear distributes it via a Baidu pan zip only.
- [X] upstream 78/xiaozhi-esp32 main/boards/spotpear/sp-esp32-s3-1.28-box/config.h (commit aab98ab) — sibling board, same audio + display pins except LCD reset.

| Function | GPIO | Confidence | Source/notes |
|---|---|---|---|
| Module | ESP32-S3R8 (8 MB Octal PSRAM in package) | verified | [S] "ESP32S3-R8"; GPIO33-37 marked no-connect (used by octal PSRAM) |
| Flash | 16 MB, DIO 80 MHz | verified | [F] bootloader header; [S] footprint lists W25Q256 (could be 32 MB part) — use 16 MB |
| LCD GC9A01A SPI SCLK | 4 | verified | [S] LCD_SCL=GP4; [X] same |
| LCD MOSI | 2 | verified | [S] LCD_SDA=GP2; [X] same |
| LCD CS | 5 | verified | [S]; [X] |
| LCD DC | 47 | verified | [S]; [X] |
| LCD RST | none (RC power-on reset, 10k + 100nF) | verified from schematic | [S]; box variant uses GPIO38, but on CAM GPIO38 is camera D6 |
| LCD backlight | 42 (PWM) | pin verified, polarity guessed | [S] LCD_BL=GP42 drives PMOS APM2307 either directly (R78, active-low) or via NPN (R79, active-high); assembly option. [X] box uses invert=true. Guess: inverted (active low). |
| Panel geometry | 240x240, offset 0,0, round; xiaozhi box mirrors X | size verified, orientation guessed | [X]; skeleton uses rotation 0 without mirroring, check orientation on hardware (ticket 14) |
| Touch | connector has TP_*; TP_SDA/TP_SCL only via 0R/NC options to the shared I2C, TP_INT/TP_RST not routed to MCU | treat as no touch | [S] |
| I2C (codecs + camera SCCB) SDA | 15 | verified | [S] SDA=GP15 (10k pull-up); [X] |
| I2C SCL | 14 | verified | [S]; [X] |
| ES8311 (DAC/speaker) addr | 0x18 (CE low) | verified | [S] CE pulled to AGND via 10k |
| ES7210 (mic ADC, 2 mics + ref) addr | 0x40 (AD0/AD1 low) | verified | [S]; [F] BoxAudioCodec |
| I2S MCLK | 16 | verified | [S]; [X] |
| I2S BCLK/SCLK | 9 | verified | [S]; [X] |
| I2S LRCK/WS | 45 | verified | [S]; [X] |
| I2S DOUT (ESP -> ES8311 DSDIN) | 8 | verified | [S]; [X] |
| I2S DIN (ES7210 SDOUT1 -> ESP) | 10 | verified | [S] via R57 51R (ES8311 ASDOUT path R70 is NC) |
| Speaker amp NS4150B enable (PA_CTRL) | 46, active high | verified pin, polarity from amp datasheet | [S] via R35 0R; [X] same. The skeleton does not call `M5.begin()` (which would drive GPIO46 high as a Capsule power-hold); PA control is ticket 14 |
| BOOT button | 0, active low, 10k pull-up | verified | [S]; [X] |
| Power key K1 | SAM8108 latch, not on a GPIO | verified | [S] |
| Battery voltage ADC | 1 (ADC1_CH0) via divider | pin verified, divider ratio/curve unverified | [S] VBAT_ADC=GP1; [X] box uses ADC1_CH0 + raw ADC table 1980..2480 |
| Charging status | not routed to MCU on CAM (MP2636 CHG only) | likely | [S]; box uses GPIO41 — GPIO41 on CAM goes to the TF-card area, unclear |
| WS2812 LED | 48 via R63 0R/NC option (shared with camera D7 option) | assembly-dependent; treat as absent | [S] |
| TF card (SPI) | CS 13, CLK 17, MOSI 18, MISO 21 | verified | [S] |
| USB D-/D+ | 19/20 | verified | [S] |
| UART0 TX/RX | 43/44 (also camera VSYNC/PCLK options) | verified | [S] |
| Camera DVP (OV5640/OV2640/GC0308) D0..D7 | 3,6,7,11,40,39,38,48 | D0-D6 verified, D7 assembly option | [S] |
| Camera XCLK / PWDN | 12 (both drawn on GP12 via R82/R80) | assembly-dependent | [S] |
| Camera HREF / VSYNC / PCLK | 46 / 43 / 44 via 0R/NC options, alternatives route to SD pins | assembly-dependent, unverified | [S] — camera is OFF in this firmware anyway |
| Camera SCCB | shares I2C 15/14 | verified | [S] |

### SpotPear round face layout

Only the SpotPear128Cam profile enables `round_face_layout`: eye-ring points
and the VM share `round_face_layout_scale(canvas)` through `DrawContext.layout_scale`:
`min(width, height) / 240 * 0.85`. The nominal body diameter is 170 px on the
240×240 panel. A zero layout scale retains automatic `design_scale(canvas)`.
The larger green sprout follows the head, sways with breathing, and lifts during
speech in every expression. Sleepy zzz and affection hearts hug the upper-right
of the head; render tests enforce a 4 px circular safe-area margin. Other board profiles keep the default face.

Render expressions, companion states, a contact sheet and a CoreS3 comparison locally:

```sh
cmake -S tools/face_render -B build-face-render -G Ninja && cmake --build build-face-render && ctest --test-dir build-face-render && build-face-render/face_render out/
```

Hardware visual sign-off remains for tickets 14/15 on-board.
