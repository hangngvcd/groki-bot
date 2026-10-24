// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: BSL-1.0

#include <array>
#include <cstdio>
#include <cstring>
#include <limits>
#include "conversation/boot_button.hpp"
#include "conversation/control_dispatch.hpp"
#include "conversation/companion_control.hpp"

using namespace stackchan::conversation;
using stackchan::avatar::VoiceState;
namespace {
int failures = 0;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "line %d: %s\n", __LINE__, #x); ++failures; } } while (false)
void arm(BootButton& b, std::uint32_t t = 0) {
    CHECK(b.update(false, t, DeviceMode::Idle) == ButtonAction::None);
    CHECK(b.update(false, t + 99, DeviceMode::Idle) == ButtonAction::None);
    CHECK(b.update(false, t + 100, DeviceMode::Idle) == ButtonAction::None);
}
ButtonAction short_press(BootButton& b, DeviceMode mode, std::uint32_t t = 200) {
    CHECK(b.update(true, t, mode) == ButtonAction::None);
    CHECK(b.update(true, t + 30, mode) == ButtonAction::None);
    CHECK(b.update(false, t + 100, mode) == ButtonAction::None);
    return b.update(false, t + 130, mode);
}
void gestures() {
    constexpr std::array modes{DeviceMode::Idle, DeviceMode::Resting, DeviceMode::Listening,
        DeviceMode::Thinking, DeviceMode::Speaking, DeviceMode::Disconnected, DeviceMode::Provisioning};
    constexpr std::array actions{ButtonAction::Listen, ButtonAction::Listen, ButtonAction::None,
        ButtonAction::Abort, ButtonAction::Abort, ButtonAction::Retry, ButtonAction::None};
    for (unsigned i = 0; i < modes.size(); ++i) {
        BootButton b; arm(b);
        CHECK(short_press(b, modes[i]) == actions[i]);
        CHECK(b.update(true, 1000, modes[i]) == ButtonAction::None);
        CHECK(b.update(true, 1030, modes[i]) == ButtonAction::None);
        CHECK(b.update(true, 5999, modes[i]) == ButtonAction::None);
        CHECK(b.update(true, 6000, modes[i]) == (modes[i] == DeviceMode::Provisioning
            ? ButtonAction::ExitSoftAp : ButtonAction::EnterSoftAp));
        CHECK(b.update(true, 9000, modes[i]) == ButtonAction::None);
        CHECK(b.update(false, 9010, modes[i]) == ButtonAction::None);
        CHECK(b.update(false, 9040, modes[i]) == ButtonAction::None);
    }
}
void boot_and_bounce() {
    BootButton b;
    for (auto t : {0u, 30u, 100u, 5000u, 9000u})
        CHECK(b.update(true, t, DeviceMode::Idle) == ButtonAction::None);
    CHECK(b.update(false, 10000, DeviceMode::Idle) == ButtonAction::None);
    CHECK(b.update(false, 10099, DeviceMode::Idle) == ButtonAction::None);
    CHECK(b.update(true, 10100, DeviceMode::Idle) == ButtonAction::None); // arm interrupted
    CHECK(b.update(false, 10200, DeviceMode::Idle) == ButtonAction::None);
    CHECK(b.update(false, 10300, DeviceMode::Idle) == ButtonAction::None);
    CHECK(short_press(b, DeviceMode::Idle, 10400) == ButtonAction::Listen);
    BootButton c; arm(c);
    CHECK(c.update(true, 200, DeviceMode::Idle) == ButtonAction::None);
    CHECK(c.update(false, 229, DeviceMode::Idle) == ButtonAction::None);
    CHECK(c.update(false, 300, DeviceMode::Idle) == ButtonAction::None);
    CHECK(c.update(true, 400, DeviceMode::Idle) == ButtonAction::None);
    CHECK(c.update(true, 430, DeviceMode::Idle) == ButtonAction::None);
    CHECK(c.update(false, 450, DeviceMode::Idle) == ButtonAction::None);
    CHECK(c.update(true, 479, DeviceMode::Idle) == ButtonAction::None);
    CHECK(c.update(false, 500, DeviceMode::Idle) == ButtonAction::None);
    CHECK(c.update(false, 529, DeviceMode::Speaking) == ButtonAction::None);
    CHECK(c.update(false, 530, DeviceMode::Speaking) == ButtonAction::Abort); // mode at release
    BootButton wrap;
    constexpr auto start = std::numeric_limits<std::uint32_t>::max() - 150;
    arm(wrap, start);
    CHECK(short_press(wrap, DeviceMode::Idle, start + 200) == ButtonAction::Listen);
}
void mapping_and_seam() {
    using S = ButtonConvStatus;
    CHECK(device_mode_for(S::Talking, VoiceState::Thinking, false, false) == DeviceMode::Thinking);
    CHECK(device_mode_for(S::Talking, VoiceState::Idle, false, false) == DeviceMode::Speaking);
    CHECK(device_mode_for(S::Listening, VoiceState::Listening, true, false) == DeviceMode::Listening);
    CHECK(device_mode_for(S::Listening, VoiceState::Idle, true, false) == DeviceMode::Resting);
    CHECK(device_mode_for(S::Listening, VoiceState::Idle, false, false) == DeviceMode::Idle);
    for (auto s : {S::WaitingWifi, S::Connecting, S::Reconnecting, S::Error}) {
        CHECK(device_mode_for(s, VoiceState::Speaking, false, false) == DeviceMode::Disconnected);
        CHECK(device_mode_for(s, VoiceState::Speaking, false, true) == DeviceMode::Provisioning);
    }
    VoiceState voice = VoiceState::Idle;
    auto feed = [&](const char* json) {
        parse_control(json, std::strlen(json), [](const auto&) {},
            [&](const char* type, const cJSON* root) {
                if (auto v = companion_voice_state(type, root)) voice = *v;
            }, [](const char*) { CHECK(false); });
    };
    BootButton b; arm(b);
    feed(R"({"type":"tts","state":"start"})");
    auto mode = device_mode_for(S::Listening, voice, false, false);
    CHECK(mode == DeviceMode::Speaking);
    CHECK(short_press(b, mode) == ButtonAction::Abort);
    feed(R"({"type":"tts","state":"stop"})");
    mode = device_mode_for(S::Listening, voice, false, false);
    CHECK(mode == DeviceMode::Idle);
    CHECK(short_press(b, mode, 1000) == ButtonAction::Listen);
}
}
int main() {
    gestures(); boot_and_bounce(); mapping_and_seam();
    return failures ? 1 : 0;
}
