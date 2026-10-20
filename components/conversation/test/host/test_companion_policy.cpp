// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: BSL-1.0

#include <array>
#include <cstdio>
#include <cstring>
#include <limits>
#include <string>

#include "avatar/companion_policy.hpp"
#include "conversation/companion_control.hpp"
#include "conversation/control_dispatch.hpp"

namespace {

using stackchan::avatar::CompanionPolicy;
using stackchan::avatar::Expression;
using stackchan::avatar::VoiceState;
using stackchan::conversation::ControlCapabilities;

int failures = 0;
#define CHECK(condition)                                                                                               \
    do {                                                                                                               \
        if (!(condition)) {                                                                                            \
            std::fprintf(stderr, "line %d: %s\n", __LINE__, #condition);                                               \
            ++failures;                                                                                                \
        }                                                                                                              \
    } while (false)

// Drives the production JSON dispatcher and face milestone decoder. Sinks
// stand in for the runtime event queue / SharedState / render-task bridge.
struct Companion {
    CompanionPolicy face;
    ControlCapabilities hardware{false, false};
    int heads = 0;
    int leds = 0;
    int errors = 0;
    bool balloon_shown = false;

    explicit Companion(std::uint32_t sleep_ms = CompanionPolicy::kSleepAfterMs) : face{sleep_ms} {}

    Expression frame(const char* json, std::uint32_t now_ms) {
        stackchan::conversation::parse_control(
            json, std::strlen(json), [&](const auto&) { ++heads; },
            [&](const char* type, const cJSON* root) {
                if (auto voice = stackchan::conversation::companion_voice_state(type, root)) {
                    face.set_voice_state(*voice);
                }
                if (std::strcmp(type, "llm") == 0) {
                    const auto* emotion = cJSON_GetObjectItemCaseSensitive(root, "emotion");
                    face.set_base(CompanionPolicy::parse_emotion(cJSON_IsString(emotion) ? emotion->valuestring : ""));
                } else if (std::strcmp(type, "led") == 0) {
                    ++leds;
                } else if (std::strcmp(type, "stt") == 0 || std::strcmp(type, "tts") == 0) {
                    const auto* text = cJSON_GetObjectItemCaseSensitive(root, "text");
                    balloon_shown = cJSON_IsString(text) && CompanionPolicy::show_balloon();
                }
            },
            [&](const char*) { ++errors; }, hardware);
        return face.resolve(now_ms);
    }
};

void turn_sequence() {
    Companion c;
    CHECK(c.face.resolve(100) == Expression::Neutral);
    CHECK(c.face.voice_state() == VoiceState::Idle);
    CHECK(c.frame(R"({"type":"listen","state":"start"})", 200) == Expression::Listening);
    CHECK(c.face.voice_state() == VoiceState::Listening);
    CHECK(c.frame(R"({"type":"listen","state":"stop"})", 300) == Expression::Thinking);
    CHECK(c.face.voice_state() == VoiceState::Thinking);
    CHECK(c.frame(R"({"type":"stt","text":"hello"})", 310) == Expression::Thinking);
    CHECK(!c.balloon_shown);
    CHECK(c.frame(R"({"type":"llm","emotion":"Happy"})", 400) == Expression::Thinking);
    CHECK(c.frame(R"({"type":"tts","state":"start"})", 500) == Expression::Happy);
    CHECK(c.face.voice_state() == VoiceState::Speaking);
    CHECK(c.frame(R"({"type":"tts","state":"sentence_start","text":"hi"})", 600) == Expression::Happy);
    CHECK(!c.balloon_shown);
    CHECK(c.face.resolve(900000) == Expression::Happy); // active speech never decays
    CHECK(c.frame(R"({"type":"tts","state":"stop"})", 900100) == Expression::Neutral);
    CHECK(c.face.voice_state() == VoiceState::Idle);
    CHECK(c.errors == 0);
    // STT also ends an utterance without an explicit listen-stop frame.
    CHECK(c.frame(R"({"type":"listen","state":"detect"})", 900200) == Expression::Listening);
    CHECK(c.frame(R"({"type":"stt","text":"next question"})", 900300) == Expression::Thinking);
    CHECK(c.frame(R"({"type":"tts","state":"start"})", 900400) == Expression::Happy);
    CHECK(c.frame(R"({"type":"llm","emotion":"Sad"})", 900500) == Expression::Sad);
    CHECK(c.frame(R"({"type":"tts","state":"stop"})", 900600) == Expression::Neutral);
}

void decay_and_activity() {
    Companion c;
    CHECK(c.face.resolve(100) == Expression::Neutral);
    for (std::uint32_t t = 100; t < 300100; t += 1000)
        CHECK(c.face.resolve(t) == Expression::Neutral);
    CHECK(c.face.resolve(300099) == Expression::Neutral);
    CHECK(c.face.resolve(300100) == Expression::Sleepy);
    CHECK(c.frame(R"({"type":"listen"})", 300200) == Expression::Listening);
    CHECK(c.face.resolve(900200) == Expression::Listening);
    CHECK(c.frame(R"({"type":"listen","state":"stop"})", 900300) == Expression::Thinking);
    CHECK(c.face.resolve(1500300) == Expression::Thinking);

    Companion short_sleep{500};
    CHECK(short_sleep.face.resolve(10) == Expression::Neutral);
    CHECK(short_sleep.face.resolve(509) == Expression::Neutral);
    CHECK(short_sleep.face.resolve(510) == Expression::Sleepy);
    short_sleep.face.note_activity();
    CHECK(short_sleep.face.resolve(600) == Expression::Neutral);
    CHECK(short_sleep.face.resolve(1100) == Expression::Sleepy);
    // Repeated identical emotions are still messages/activity.
    CHECK(short_sleep.frame(R"({"type":"llm","emotion":"Neutral"})", 1200) == Expression::Neutral);
    CHECK(short_sleep.frame(R"({"type":"llm","emotion":"Neutral"})", 1600) == Expression::Neutral);
    CHECK(short_sleep.face.resolve(2099) == Expression::Neutral);
    CHECK(short_sleep.face.resolve(2100) == Expression::Sleepy);

    Companion wrap{500};
    constexpr auto start = std::numeric_limits<std::uint32_t>::max() - 200;
    CHECK(wrap.face.resolve(start) == Expression::Neutral);
    CHECK(wrap.face.resolve(start + 499u) == Expression::Neutral);
    CHECK(wrap.face.resolve(start + 500u) == Expression::Sleepy);
}

void emotions() {
    Companion c;
    c.frame(R"({"type":"tts","state":"start"})", 0);
    const std::array allowed{
        std::pair{"Neutral", Expression::Neutral},     std::pair{"Happy", Expression::Happy},
        std::pair{"Excited", Expression::Excited},     std::pair{"Curious", Expression::Curious},
        std::pair{"Thinking", Expression::Thinking},   std::pair{"Affection", Expression::Affection},
        std::pair{"Surprised", Expression::Surprised}, std::pair{"Sad", Expression::Sad},
        std::pair{"happy", Expression::Happy},         std::pair{"affection", Expression::Affection},
    };
    for (const auto& [name, expected] : allowed) {
        const auto json = std::string{R"({"type":"llm","emotion":")"} + name + R"("})";
        CHECK(c.frame(json.c_str(), 100) == expected);
    }
    for (const char* name : {"Angry", "Bored", "Doubt", "Sleepy", "Dizzy", "Confused", "Listening", "unknown", "angry",
                             "bored", "doubt", "laughing", "", "happy-ish"}) {
        c.frame(R"({"type":"llm","emotion":"Happy"})", 200);
        const auto json = std::string{R"({"type":"llm","emotion":")"} + name + R"("})";
        CHECK(c.frame(json.c_str(), 300) == Expression::Neutral);
    }
    CHECK(c.frame(R"({"type":"llm","emotion":42})", 400) == Expression::Neutral);
    // Tool/MCP expression writes also pass the firmware whitelist.
    for (const auto e : {Expression::Angry, Expression::Bored, Expression::Doubt, Expression::Sleepy, Expression::Dizzy,
                         Expression::Confused, Expression::Listening, static_cast<Expression>(255)}) {
        c.face.set_base(e);
        CHECK(c.face.resolve(500) == Expression::Neutral);
    }
}

void absent_hardware() {
    Companion c{1000};
    c.frame(R"({"type":"tts","state":"start"})", 0);
    c.frame(R"({"type":"llm","emotion":"Happy"})", 1);
    for (const char* json :
         {R"({"type":"head","yaw":30,"pitch":10,"speed":200})", R"({"type":"head","yaw":"bad"})", R"({"type":"head"})",
          R"({"type":"led","r":255,"g":0,"b":0})", R"({"type":"led","r":"bad"})", R"({"type":"led"})"}) {
        CHECK(c.frame(json, 10) == Expression::Happy);
        CHECK(c.face.voice_state() == VoiceState::Speaking);
    }
    CHECK(c.heads == 0 && c.leds == 0 && c.errors == 0);
    c.frame(R"({"type":"tts","state":"stop"})", 100);
    c.frame(R"({"type":"head"})", 999);
    c.frame(R"({"type":"led"})", 1000);
    CHECK(c.face.resolve(1100) == Expression::Sleepy); // ignored commands aren't activity
    CHECK(c.errors == 0);

    // Capabilities are independent; existing profiles retain dispatch.
    c.hardware = {true, false};
    c.frame(R"({"type":"head","yaw":1,"pitch":2})", 1200);
    c.frame(R"({"type":"led"})", 1201);
    CHECK(c.heads == 1 && c.leds == 0 && c.errors == 0);
    c.hardware = {false, true};
    c.frame(R"({"type":"head"})", 1202);
    c.frame(R"({"type":"led","r":1,"g":2,"b":3})", 1203);
    CHECK(c.heads == 1 && c.leds == 1 && c.errors == 0);
}

void connection_and_overlays() {
    Companion c;
    c.frame(R"({"type":"listen","state":"start"})", 0);
    c.face.set_connected(false);
    CHECK(c.face.resolve(10) == Expression::Dizzy);
    CHECK(c.face.voice_state() == VoiceState::Idle);
    CHECK(c.face.resolve(3009) == Expression::Dizzy);
    CHECK(c.face.resolve(3010) == Expression::Neutral);
    c.face.set_connected(false); // retry failure doesn't restart the hold
    CHECK(c.face.resolve(10009) == Expression::Neutral);
    CHECK(c.face.resolve(10010) == Expression::Dizzy);
    c.face.set_connected(true);
    CHECK(c.face.resolve(10011) == Expression::Neutral);
    CHECK(c.face.voice_state() == VoiceState::Idle);
    CHECK(c.face.resolve(20010) == Expression::Neutral);
    // An interactive wake overlay must yield when speech ends.
    c.face.set_overlay(Expression::Listening, 3000);
    CHECK(c.face.resolve(20020) == Expression::Listening);
    CHECK(c.frame(R"({"type":"stt","text":"hello"})", 20030) == Expression::Thinking);
    CHECK(!c.balloon_shown);
    CHECK(c.frame(R"({"type":"tts","state":"start"})", 20040) == Expression::Neutral);
    CHECK(c.frame(R"({"type":"tts","state":"stop"})", 20050) == Expression::Neutral);
}

} // namespace

int main() {
    turn_sequence();
    decay_and_activity();
    emotions();
    absent_hardware();
    connection_and_overlays();
    if (failures)
        std::fprintf(stderr, "%d companion policy checks failed\n", failures);
    else
        std::puts("companion policy checks passed");
    return failures ? 1 : 0;
}
