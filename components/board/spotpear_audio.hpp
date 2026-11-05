// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: BSL-1.0

#pragma once

namespace stackchan::board {

// SpotPear ESP32S3-1.28inch-AI-CAM audio bring-up (ES8311 DAC + NS4150B PA,
// ES7210 mic ADC). The board skips M5.begin(), so nothing else configures
// M5.Speaker / M5.Mic, the internal I2C bus or the codecs. This wires the
// SpotPear pins into M5.Speaker / M5.Mic (same playback / capture path every
// other board uses) and registers enable callbacks that program the codecs
// and drive the PA. Logs codec probe results. Safe to call once after the
// display is up.
void spotpear_audio_begin() noexcept;

} // namespace stackchan::board
