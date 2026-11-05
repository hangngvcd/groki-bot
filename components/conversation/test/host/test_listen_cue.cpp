// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0
// Ticket 15f: status-LED wake cue + synthesized wake chime.

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>

#include "conversation/listen_cue.hpp"

using namespace stackchan::conversation;

namespace {
int failures = 0;
#define CHECK(condition)                                                        \
    do {                                                                        \
        if (!(condition)) {                                                     \
            std::fprintf(stderr, "line %d: %s\n", __LINE__, #condition);        \
            ++failures;                                                         \
        }                                                                       \
    } while (false)

void test_classify() {
    // Exact gateway colours (wake_gate.LISTENING_LED_RGB, _begin_tts blue,
    // device_emotion.DEAD_LED_RGB, off).
    CHECK(classify_status_led(0, 180, 180) == StatusLed::Listening);
    CHECK(classify_status_led(0, 80, 180) == StatusLed::Speaking);
    CHECK(classify_status_led(0, 0, 0) == StatusLed::Off);
    CHECK(classify_status_led(255, 0, 0) == StatusLed::Error);
    CHECK(classify_status_led(0, 0, 255) == StatusLed::Speaking);
    CHECK(classify_status_led(255, 255, 255) == StatusLed::Other);
    CHECK(classify_status_led(0, 255, 0) == StatusLed::Other);
}

void test_wake_sequence() {
    WakeCue cue;
    // Wake: cyan -> chime + listening.
    auto s = cue.on_led(StatusLed::Listening);
    CHECK(s.fresh_wake && s.listening && !s.closed);
    // Duplicate cyan: no second chime.
    s = cue.on_led(StatusLed::Listening);
    CHECK(!s.fresh_wake && s.listening);
    // Reply: blue, then post-tts cyan -> listening again, no chime.
    s = cue.on_led(StatusLed::Speaking);
    CHECK(!s.fresh_wake && !s.listening && !s.closed);
    s = cue.on_led(StatusLed::Listening);
    CHECK(!s.fresh_wake && s.listening);
    // Gate closes.
    s = cue.on_led(StatusLed::Off);
    CHECK(s.closed && !cue.open);
    s = cue.on_led(StatusLed::Off);
    CHECK(!s.closed);
    // Next wake chimes again.
    CHECK(cue.on_led(StatusLed::Listening).fresh_wake);
    // Dead session closes; reset clears.
    CHECK(cue.on_led(StatusLed::Error).closed);
    cue.on_led(StatusLed::Speaking);
    cue.reset();
    CHECK(cue.on_led(StatusLed::Listening).fresh_wake);
}

void test_chime() {
    for (std::uint32_t rate : {8000u, 16000u, 24000u}) {
        const auto n = wake_chime_samples(rate);
        CHECK(n == rate * kChimeMs / 1000);
        std::vector<std::int16_t> full(n + 100);
        render_wake_chime(full.data(), 0, full.size(), rate);
        int peak = 0;
        double sum_sq = 0;
        for (std::size_t i = 0; i < n; ++i) {
            peak = std::max(peak, std::abs(int(full[i])));
            sum_sq += double(full[i]) * full[i];
        }
        const int limit = int(kChimePeak * 32767.0f) + 1;
        CHECK(peak <= limit);
        CHECK(peak >= limit * 9 / 10);   // normalised close to the target
        CHECK(sum_sq / n > 1e5);         // audible
        for (std::size_t i = n; i < full.size(); ++i) CHECK(full[i] == 0);
        // Starts and ends near silence (no click).
        CHECK(std::abs(int(full[0])) < 200);
        CHECK(std::abs(int(full[n - 1])) < 200);
        // Segmented rendering == one-shot rendering.
        std::vector<std::int16_t> seg(full.size());
        for (std::size_t off = 0; off < seg.size(); off += 777)
            render_wake_chime(seg.data() + off, off, std::min<std::size_t>(777, seg.size() - off), rate);
        CHECK(seg == full);
    }
    CHECK(kChimeMs >= 150 && kChimeMs <= 250);
}
} // namespace

int main() {
    test_classify();
    test_wake_sequence();
    test_chime();
    if (const char* path = std::getenv("CHIME_WAV")) {
        const std::uint32_t rate = 24000;
        std::vector<std::int16_t> pcm(wake_chime_samples(rate));
        render_wake_chime(pcm.data(), 0, pcm.size(), rate);
        if (FILE* f = std::fopen(path, "wb")) {
            auto u32 = [&](std::uint32_t v) { std::fwrite(&v, 4, 1, f); };
            auto u16 = [&](std::uint16_t v) { std::fwrite(&v, 2, 1, f); };
            const std::uint32_t bytes = pcm.size() * 2;
            std::fwrite("RIFF", 1, 4, f); u32(36 + bytes); std::fwrite("WAVEfmt ", 1, 8, f);
            u32(16); u16(1); u16(1); u32(rate); u32(rate * 2); u16(2); u16(16);
            std::fwrite("data", 1, 4, f); u32(bytes); std::fwrite(pcm.data(), 2, pcm.size(), f);
            std::fclose(f);
        }
    }
    if (failures) { std::fprintf(stderr, "%d failure(s)\n", failures); return 1; }
    std::puts("listen_cue: ok");
    return 0;
}
