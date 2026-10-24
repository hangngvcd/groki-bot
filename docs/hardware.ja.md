[English](hardware.md) | [中文](hardware.zh-CN.md) | [日本語](hardware.ja.md)

# 対応ボード

| ボード | 略号 | 表示 | 主な特徴 |
|---|---|---|---|
| CoreS3 + Stack-chan ベース | `cores3` | 320×240 IPS + touch | 標準。サーボ 2 軸、頭タッチ センサー、INA226 電池計 |
| CoreS3 + Takao Base | `cores3` | 同上 | Port A 半二重サーボ、サーボ電源/電池計なし |
| AtomS3R + Atomic ECHO BASE (アトムニャン) | `atoms3r` | 128×128 LCD | サーボなし、ES8311 音声、BtnA で UI / AP |
| AtomS3 (PSRAM なし) + ECHO BASE | `atoms3` | 128×128 LCD | 軽量プロファイル、会話/RTP 非対応 |
| M5 StopWatch (C152) | `stopwatch` | 466×466 円形 AMOLED + touch | サーボなし、touch で視線追従、ES8311 音声 |
| SpotPear ESP32-S3 1.28-CAM | `spotpear128cam` | 240x240 round GC9A01A | Skeleton: builds in CI; hardware bring-up in ticket 14; no servos/touch/LED; camera off |

ビルドは `make build BOARD=<略号>` (デフォルト `cores3`)。
ボードは起動時に検出され、`set_board_kind()` で UI / 機能のグレーアウトに反映されます。

## ハードウェア (CoreS3 + Stack-chan ベースの場合)

- M5Stack CoreS3 (ESP32-S3、8 MB Quad-SPI PSRAM、16 MB Flash)
- Stack-chan ベース (PY32 IO Expander @ 0x6F、SCS0009 ×2)
  - 内部 I²C: AXP2101 (0x34) / Touch (0x38) ほか M5Unified が管理
  - PY32 Pin 0: サーボ VM 電源 EN (ON 後 200 ms 待ってバス使用)
  - サーボ バス (SCS0009): UART1, TX GPIO 6 / RX GPIO 7, 1 Mbps, 8 N 1
    - Yaw  ID = 1, zero_pos = 460
    - Pitch ID = 2, zero_pos = 620
  - 1 step ≈ 0.3125° (`deg = (raw - zero) * 5 / 16`)
  - 頭タッチ センサー Si12T @ 0x68 (なでなで / barge-in、3 ゾーン)
- 他ボードのピン配置は各 `sdkconfig.defaults.<board>` と
  `components/board/board.cpp` を参照

## SpotPear ESP32-S3 1.28-CAM pin map

- [ ] Parent confirmation: pending

See the [pin map and source confidence labels](hardware.md#spotpear-esp32-s3-128-cam-pin-map). Audio, BOOT input and battery calibration await ticket 14.
