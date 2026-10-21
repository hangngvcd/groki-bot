// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: BSL-1.0

#pragma once

#include <cstdint>
#include "avatar/expression.hpp"

namespace stackchan::conversation {
enum class DeviceMode { Idle, Resting, Listening, Thinking, Speaking, Disconnected, Provisioning };
enum class ButtonAction { None, Listen, Abort, Retry, EnterSoftAp, ExitSoftAp };
// Mirrors app::ConvStatus without depending on main/.
enum class ButtonConvStatus : int {
    Disabled = 0, WaitingWifi, Connecting, Listening, Talking, Yielded, Reconnecting, Error
};

// Provisioning > disconnected > speaking > thinking > listening > resting > idle.
// Talking with a Thinking voice is a pending turn; Talking alone is conservatively
// treated as Speaking. Listening status alone is connected standby (Idle).
constexpr DeviceMode device_mode_for(ButtonConvStatus status, avatar::VoiceState voice,
                                     bool resting, bool soft_ap) {
    if (soft_ap) return DeviceMode::Provisioning;
    if (status == ButtonConvStatus::WaitingWifi || status == ButtonConvStatus::Connecting ||
        status == ButtonConvStatus::Reconnecting || status == ButtonConvStatus::Error)
        return DeviceMode::Disconnected;
    if (voice == avatar::VoiceState::Speaking ||
        (status == ButtonConvStatus::Talking && voice != avatar::VoiceState::Thinking))
        return DeviceMode::Speaking;
    if (voice == avatar::VoiceState::Thinking) return DeviceMode::Thinking;
    if (voice == avatar::VoiceState::Listening) return DeviceMode::Listening;
    return resting ? DeviceMode::Resting : DeviceMode::Idle;
}

class BootButton {
public:
    static constexpr std::uint32_t kReleaseArmMs = 100;
    static constexpr std::uint32_t kDebounceMs = 30;
    static constexpr std::uint32_t kLongPressMs = 5000;

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
        if (raw_ != pressed_ && now_ms - edge_ms_ >= kDebounceMs) {
            pressed_ = raw_;
            if (pressed_) {
                press_ms_ = edge_ms_;
                long_fired_ = false;
            } else {
                if (long_fired_) return ButtonAction::None;
                switch (mode) {
                case DeviceMode::Idle:
                case DeviceMode::Resting: return ButtonAction::Listen;
                case DeviceMode::Thinking: // Abort pending work to avoid a late stale reply.
                case DeviceMode::Speaking: return ButtonAction::Abort;
                case DeviceMode::Disconnected: return ButtonAction::Retry;
                default: return ButtonAction::None;
                }
            }
        }
        if (pressed_ && raw_ && !long_fired_ && now_ms - press_ms_ >= kLongPressMs) {
            long_fired_ = true;
            return mode == DeviceMode::Provisioning ? ButtonAction::ExitSoftAp : ButtonAction::EnterSoftAp;
        }
        return ButtonAction::None;
    }
private:
    bool initialized_ = false, armed_ = false, raw_ = false, pressed_ = false, long_fired_ = false;
    std::uint32_t edge_ms_ = 0, press_ms_ = 0;
};
} // namespace stackchan::conversation
