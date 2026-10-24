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
ButtonAction single(BootButton& b, DeviceMode mode, std::uint32_t t = 200) {
    const auto result = short_press(b, mode, t);
    if (mode != DeviceMode::Idle && mode != DeviceMode::Resting && mode != DeviceMode::ShortBreak)
        return result;
    CHECK(result == ButtonAction::None);
    // Window is measured from the raw release edge (t + 100): 350 ms later.
    CHECK(b.update(false, t + 449, mode) == ButtonAction::None);
    return b.update(false, t + 450, mode);
}
void gestures() {
    constexpr std::array modes{DeviceMode::Idle, DeviceMode::Resting, DeviceMode::Listening,
        DeviceMode::Thinking, DeviceMode::Speaking, DeviceMode::Disconnected, DeviceMode::Provisioning, DeviceMode::ShortBreak};
    constexpr std::array actions{ButtonAction::Listen, ButtonAction::Listen, ButtonAction::None,
        ButtonAction::Abort, ButtonAction::Abort, ButtonAction::Retry, ButtonAction::None, ButtonAction::Listen};
    for (unsigned i = 0; i < modes.size(); ++i) {
        BootButton b; arm(b);
        CHECK(single(b, modes[i]) == actions[i]);
        BootButton d; arm(d);
        const bool capable = modes[i] == DeviceMode::Idle || modes[i] == DeviceMode::Resting ||
            modes[i] == DeviceMode::ShortBreak;
        CHECK(short_press(d, modes[i]) == (capable ? ButtonAction::None : actions[i]));
        CHECK(short_press(d, modes[i], 400) == (capable
            ? (modes[i] == DeviceMode::ShortBreak ? ButtonAction::EndBreak : ButtonAction::StartBreak)
            : actions[i]));
        CHECK(d.update(false, 1000, modes[i]) == ButtonAction::None);
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
    CHECK(single(b, DeviceMode::Idle, 10400) == ButtonAction::Listen);
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
    CHECK(single(wrap, DeviceMode::Idle, start + 200) == ButtonAction::Listen);
}
void double_boundaries_and_timer() {
    for (auto mode : {DeviceMode::Idle, DeviceMode::Speaking, DeviceMode::Provisioning}) {
        BootButton b; arm(b);
        CHECK(!b.take_click());
        CHECK(b.update(true, 200, mode) == ButtonAction::None);
        CHECK(!b.take_click());
        CHECK(b.update(true, 230, mode) == ButtonAction::None);
        CHECK(b.take_click() == (mode != DeviceMode::Provisioning));
        CHECK(!b.take_click());
    }
    BootButton gap; arm(gap);
    CHECK(short_press(gap, DeviceMode::Idle) == ButtonAction::None); // raw release 300
    CHECK(gap.update(true, 651, DeviceMode::Idle) == ButtonAction::Listen); // raw gap 351
    CHECK(gap.update(true, 681, DeviceMode::Idle) == ButtonAction::None);
    CHECK(gap.update(false, 751, DeviceMode::Idle) == ButtonAction::None);
    CHECK(gap.update(false, 781, DeviceMode::Idle) == ButtonAction::None);
    CHECK(gap.update(false, 1100, DeviceMode::Idle) == ButtonAction::None);
    CHECK(gap.update(false, 1101, DeviceMode::Idle) == ButtonAction::Listen);

    BootButton triple; arm(triple);
    CHECK(short_press(triple, DeviceMode::Idle) == ButtonAction::None);
    CHECK(short_press(triple, DeviceMode::Idle, 400) == ButtonAction::StartBreak);
    CHECK(single(triple, DeviceMode::ShortBreak, 600) == ButtonAction::Listen);

    BootButton exact; arm(exact);
    CHECK(short_press(exact, DeviceMode::Idle) == ButtonAction::None);
    // Raw second down exactly 350 ms after the raw release still counts while debouncing.
    CHECK(short_press(exact, DeviceMode::Idle, 650) == ButtonAction::StartBreak);

    // A sub-debounce blip inside the window is not a second press: single Listen.
    BootButton blip; arm(blip);
    CHECK(short_press(blip, DeviceMode::Idle) == ButtonAction::None);
    CHECK(blip.take_click()); // first press
    CHECK(blip.update(true, 400, DeviceMode::Idle) == ButtonAction::None);
    CHECK(blip.update(false, 410, DeviceMode::Idle) == ButtonAction::None);
    CHECK(!blip.take_click());
    CHECK(blip.update(false, 650, DeviceMode::Idle) == ButtonAction::Listen);

    BootButton held; arm(held);
    CHECK(short_press(held, DeviceMode::Idle) == ButtonAction::None);
    CHECK(held.update(true, 400, DeviceMode::Idle) == ButtonAction::None);
    CHECK(held.update(true, 430, DeviceMode::Idle) == ButtonAction::None);
    CHECK(held.update(true, 5400, DeviceMode::Idle) == ButtonAction::EnterSoftAp);
    CHECK(held.update(false, 5500, DeviceMode::Idle) == ButtonAction::None);
    CHECK(held.update(false, 5530, DeviceMode::Idle) == ButtonAction::None);
    CHECK(held.update(false, 6000, DeviceMode::Idle) == ButtonAction::None);

    for (auto mode : {DeviceMode::Speaking, DeviceMode::Thinking, DeviceMode::Disconnected,
                      DeviceMode::Listening, DeviceMode::Provisioning}) {
        BootButton changed; arm(changed);
        CHECK(short_press(changed, DeviceMode::Idle) == ButtonAction::None);
        CHECK(changed.update(false, 680, mode) == (mode == DeviceMode::Disconnected
            ? ButtonAction::Retry : ButtonAction::None));
        CHECK(changed.update(false, 700, DeviceMode::Idle) == ButtonAction::None);
    }
    constexpr auto start = std::numeric_limits<std::uint32_t>::max() - 250;
    BootButton wrap; arm(wrap, start);
    CHECK(short_press(wrap, DeviceMode::Idle, start + 200) == ButtonAction::None);
    CHECK(short_press(wrap, DeviceMode::Idle, start + 400) == ButtonAction::StartBreak);

    ShortBreakTimer timer;
    CHECK(!timer.active());
    CHECK(!timer.update(0));
    timer.start(100, 500);
    CHECK(timer.active());
    CHECK(timer.remaining_ms(599) == 1);
    CHECK(!timer.update(599));
    CHECK(timer.update(600));
    CHECK(!timer.active());
    CHECK(timer.remaining_ms(600) == 0);
    CHECK(!timer.update(601));
    timer.start(700, 100);
    timer.stop();
    CHECK(timer.remaining_ms(750) == 0);
    CHECK(!timer.update(800));
    timer.start(start, 500);
    CHECK(timer.remaining_ms(start + 499) == 1);
    CHECK(!timer.update(start + 499));
    CHECK(timer.update(start + 500));
    CHECK(!timer.update(start + 501));
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
    CHECK(device_mode_for(S::Listening, VoiceState::Idle, true, false, true) == DeviceMode::ShortBreak);
    CHECK(device_mode_for(S::Listening, VoiceState::Idle, false, false, true) == DeviceMode::ShortBreak);
    CHECK(device_mode_for(S::Listening, VoiceState::Listening, true, false, true) == DeviceMode::Listening);
    CHECK(device_mode_for(S::Talking, VoiceState::Thinking, true, false, true) == DeviceMode::Thinking);
    CHECK(device_mode_for(S::Talking, VoiceState::Speaking, true, false, true) == DeviceMode::Speaking);
    CHECK(device_mode_for(S::Error, VoiceState::Speaking, true, false, true) == DeviceMode::Disconnected);
    CHECK(device_mode_for(S::Error, VoiceState::Speaking, true, true, true) == DeviceMode::Provisioning);
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
    CHECK(single(b, mode, 1000) == ButtonAction::Listen);
}
}
int main() {
    gestures(); double_boundaries_and_timer(); boot_and_bounce(); mapping_and_seam();
    return failures ? 1 : 0;
}
