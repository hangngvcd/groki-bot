// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: BSL-1.0

#pragma once

#include <cstdint>
#include "avatar/expression.hpp"

namespace stackchan::conversation {
enum class DeviceMode { Idle, Resting, Listening, Thinking, Speaking, Disconnected, Provisioning, ShortBreak };
enum class ButtonAction { None, Listen, Abort, Retry, EnterSoftAp, ExitSoftAp, StartBreak, EndBreak };
// Mirrors app::ConvStatus without depending on main/.
enum class ButtonConvStatus : int {
    Disabled = 0, WaitingWifi, Connecting, Listening, Talking, Yielded, Reconnecting, Error
};

// Provisioning > disconnected > speaking > thinking > listening > short break > resting > idle.
// Talking with a Thinking voice is a pending turn; Talking alone is conservatively
// treated as Speaking. Listening status alone is connected standby (Idle).
constexpr DeviceMode device_mode_for(ButtonConvStatus status, avatar::VoiceState voice,
                                     bool resting, bool soft_ap, bool short_break = false) {
    if (soft_ap) return DeviceMode::Provisioning;
    if (status == ButtonConvStatus::WaitingWifi || status == ButtonConvStatus::Connecting ||
        status == ButtonConvStatus::Reconnecting || status == ButtonConvStatus::Error)
        return DeviceMode::Disconnected;
    if (voice == avatar::VoiceState::Speaking ||
        (status == ButtonConvStatus::Talking && voice != avatar::VoiceState::Thinking))
        return DeviceMode::Speaking;
    if (voice == avatar::VoiceState::Thinking) return DeviceMode::Thinking;
    if (voice == avatar::VoiceState::Listening) return DeviceMode::Listening;
    if (short_break) return DeviceMode::ShortBreak;
    return resting ? DeviceMode::Resting : DeviceMode::Idle;
}

// The real length is a parent setting (pending decision), set via Grok Bot /
// parent API later. Breaks remain local; no gateway protocol messages.
inline constexpr std::uint32_t kDefaultShortBreakMs = 5 * 60 * 1000;

class ShortBreakTimer {
public:
    void start(std::uint32_t now_ms, std::uint32_t duration_ms) {
        start_ms_ = now_ms;
        duration_ms_ = duration_ms;
        active_ = true;
    }
    void stop() { active_ = false; }
    bool active() const { return active_; }
    std::uint32_t remaining_ms(std::uint32_t now_ms) const {
        const auto elapsed = now_ms - start_ms_;
        return active_ && elapsed < duration_ms_ ? duration_ms_ - elapsed : 0;
    }
    bool update(std::uint32_t now_ms) {
        if (!active_ || now_ms - start_ms_ < duration_ms_) return false;
        active_ = false;
        return true;
    }
private:
    bool active_ = false;
    std::uint32_t start_ms_ = 0, duration_ms_ = 0;
};

class BootButton {
public:
    static constexpr std::uint32_t kReleaseArmMs = 100;
    static constexpr std::uint32_t kDebounceMs = 30;
    static constexpr std::uint32_t kLongPressMs = 5000;

    static constexpr std::uint32_t kDoublePressWindowMs = 350;

    bool take_click() {
        const bool result = click_;
        click_ = false;
        return result;
    }

    ButtonAction update(bool pressed_level, std::uint32_t now_ms, DeviceMode mode) {
        if (!initialized_ || pressed_level != raw_) {
            initialized_ = true;
            raw_ = pressed_level;
            edge_ms_ = now_ms;
        }
        // Boot-time low is never a gesture, however long it lasts.
        if (!armed_) {
            if (!raw_ && now_ms - edge_ms_ >= kReleaseArmMs) armed_ = true;
            return ButtonAction::None;
        }
        // The window is measured on raw edges: a second press whose raw edge is
        // within 350 ms of the first raw release still counts while debouncing.
        const bool second_in_window =
            pending_ && raw_ && !pressed_ && edge_ms_ - release_ms_ <= kDoublePressWindowMs;
        ButtonAction expired = ButtonAction::None;
        if (pending_ && !second_in_window && now_ms - release_ms_ >= kDoublePressWindowMs) {
            pending_ = false;
            if (double_capable(mode)) expired = ButtonAction::Listen;
            else if (mode == DeviceMode::Disconnected) expired = ButtonAction::Retry;
        }
        if (raw_ != pressed_ && now_ms - edge_ms_ >= kDebounceMs) {
            pressed_ = raw_;
            if (pressed_) {
                click_ = click_ || mode != DeviceMode::Provisioning;
                second_ = pending_;
                pending_ = false;
                press_ms_ = edge_ms_;
                long_fired_ = false;
            } else {
                if (long_fired_) return ButtonAction::None;
                if (second_) {
                    second_ = false;
                    if (mode == DeviceMode::ShortBreak) return ButtonAction::EndBreak;
                    if (mode == DeviceMode::Idle || mode == DeviceMode::Resting)
                        return ButtonAction::StartBreak;
                    return ButtonAction::None;
                }
                if (double_capable(mode)) {
                    pending_ = true;
                    release_ms_ = edge_ms_; // raw release edge
                    return ButtonAction::None;
                }
                switch (mode) {
                case DeviceMode::Thinking: // Abort pending work to avoid a late stale reply.
                case DeviceMode::Speaking: return ButtonAction::Abort;
                case DeviceMode::Disconnected: return ButtonAction::Retry;
                default: return ButtonAction::None;
                }
            }
        }
        if (pressed_ && raw_ && !long_fired_ && now_ms - press_ms_ >= kLongPressMs) {
            long_fired_ = true;
            pending_ = second_ = false;
            return mode == DeviceMode::Provisioning ? ButtonAction::ExitSoftAp : ButtonAction::EnterSoftAp;
        }
        return expired;
    }
private:
    static bool double_capable(DeviceMode mode) {
        return mode == DeviceMode::Idle || mode == DeviceMode::Resting || mode == DeviceMode::ShortBreak;
    }
    bool pending_ = false, second_ = false, click_ = false;
    std::uint32_t release_ms_ = 0;
    bool initialized_ = false, armed_ = false, raw_ = false, pressed_ = false, long_fired_ = false;
    std::uint32_t edge_ms_ = 0, press_ms_ = 0;
};
} // namespace stackchan::conversation
