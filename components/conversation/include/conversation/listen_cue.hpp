// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0
//
// Ticket 15f: "listening / talking / sleeping" cue for the round companion
// (SpotPear 1.28-CAM, no LED). Pure helpers shared by firmware and host tests:
//
//  * classify_status_led(): the gateway already drives a status light over the
//    XiaoZhi websocket ({"type":"led","r","g","b"} via ws_direct, since
//    features.mcp=false). Its colours are the wake-gate state:
//      LISTENING_LED_RGB (0,180,180) cyan  - wake gate open, listening
//      speaking          (0, 80,180) blue  - a reply is being spoken
//      (0,0,0)                        off   - gate closed / idle
//      DEAD_LED_RGB      (255,0,0)    red   - session dead
//    The firmware reuses those messages as the wake signal instead of adding
//    a new protocol message.
//  * WakeCue: edge detector turning that LED stream into "fresh wake" (play
//    the chime) vs "back to listening after a reply" (no chime).
//  * render_wake_chime(): a soft two-tone "叮" synthesised in code.

#pragma once

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>

namespace stackchan::conversation {

enum class StatusLed : std::uint8_t { Off, Listening, Speaking, Error, Other };

constexpr StatusLed classify_status_led(std::uint8_t r, std::uint8_t g, std::uint8_t b) noexcept {
    if (r == 0 && g == 0 && b == 0) return StatusLed::Off;
    if (r >= 128 && g < 64 && b < 64) return StatusLed::Error;
    if (r < 64 && b >= 96) {
        // Cyan: green roughly as strong as blue. Blue: green clearly weaker.
        if (g * 4 >= b * 3) return StatusLed::Listening;
        return StatusLed::Speaking;
    }
    return StatusLed::Other;
}

// Tracks whether the gateway's wake window is open and spots a fresh wake.
struct WakeCue {
    bool open = false;     // gateway wake gate open (cyan or blue seen since last off)

    struct Step {
        bool fresh_wake = false;   // closed -> listening: play the chime
        bool listening = false;    // show the listening face/ring now
        bool closed = false;       // gate closed: drop the listening face
    };

    Step on_led(StatusLed led) noexcept {
        Step s;
        switch (led) {
        case StatusLed::Listening:
            s.fresh_wake = !open;
            s.listening = true;
            open = true;
            break;
        case StatusLed::Speaking:
            open = true;
            break;
        case StatusLed::Off:
        case StatusLed::Error:
            s.closed = open;
            open = false;
            break;
        case StatusLed::Other:
            break;
        }
        return s;
    }
    void reset() noexcept { open = false; }
};

// Two struck bell partials, E6 then A6 (a rising fourth), each with a 4 ms
// attack and an exponential decay plus a quiet octave overtone; a 20 ms
// raised-cosine fade at the end. kChimeMs long, peak amplitude
// kChimePeak of full scale (no sample can exceed it -> never clips in the
// source; the M5 mixer + ES8311 path still has >= 6 dB headroom at 100 %).
inline constexpr std::uint32_t kChimeMs = 220;
inline constexpr float kChimePeak = 0.35f;

inline std::size_t wake_chime_samples(std::uint32_t rate) noexcept {
    return static_cast<std::size_t>(rate) * kChimeMs / 1000;
}

namespace detail {
inline float chime_raw(float t) noexcept {
    constexpr float kTwoPi = 6.28318530718f;
    auto note = [&](float start, float f, float tau) {
        const float u = t - start;
        if (u < 0) return 0.0f;
        const float attack = std::min(1.0f, u / 0.004f);
        const float env = attack * std::exp(-u / tau);
        return env * (std::sin(kTwoPi * f * u) + 0.18f * std::exp(-u / 0.025f) * std::sin(kTwoPi * 2 * f * u));
    };
    float v = note(0.000f, 1318.5f, 0.055f) + 0.9f * note(0.070f, 1760.0f, 0.065f);
    const float total = kChimeMs / 1000.0f;
    const float fade = 0.020f;
    if (t > total - fade) {
        const float x = std::clamp((total - t) / fade, 0.0f, 1.0f);
        v *= 0.5f - 0.5f * std::cos(3.14159265f * x);
    }
    return v;
}
// Peak of chime_raw (sampled densely once); used to normalise.
inline float chime_raw_peak() noexcept {
    static const float peak = [] {
        float p = 1e-6f;
        for (int i = 0; i < 48000 * static_cast<int>(kChimeMs) / 1000; ++i)
            p = std::max(p, std::fabs(chime_raw(i / 48000.0f)));
        return p;
    }();
    return peak;
}
} // namespace detail

// Fill out[0..n) with chime samples [offset, offset+n) at `rate` Hz. Samples
// past the end are zero. Integer PCM16, |x| <= kChimePeak * 32767 (+1 lsb).
inline void render_wake_chime(std::int16_t* out, std::size_t offset, std::size_t n, std::uint32_t rate) noexcept {
    const std::size_t total = wake_chime_samples(rate);
    const float scale = kChimePeak * 32767.0f / detail::chime_raw_peak();
    for (std::size_t i = 0; i < n; ++i) {
        const std::size_t k = offset + i;
        if (k >= total) { out[i] = 0; continue; }
        const float v = detail::chime_raw(static_cast<float>(k) / static_cast<float>(rate)) * scale;
        out[i] = static_cast<std::int16_t>(std::lround(std::clamp(v, -32767.0f, 32767.0f)));
    }
}

} // namespace stackchan::conversation
