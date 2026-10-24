// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: BSL-1.0

#pragma once

#include <iterator>
#include <string_view>

#include "avatar/expression_controller.hpp"

namespace stackchan::avatar {

// Opt-in companion behaviour. Owns the activity clock so the underlying
// controller can keep its existing overlay/voice arbitration without Bored.
// The render task owns this object; transport events arrive through SharedState.
class CompanionPolicy {
public:
    static constexpr std::uint32_t kSleepAfterMs = 300000;
    static constexpr std::uint32_t kDisconnectHoldMs = 3000;
    static constexpr std::uint32_t kDisconnectPeriodMs = 10000;

    explicit CompanionPolicy(std::uint32_t sleep_after_ms = kSleepAfterMs) : sleep_after_ms_{sleep_after_ms} {}

    static constexpr bool show_balloon() noexcept {
        return false;
    }

    static Expression whitelist(Expression emotion) noexcept {
        switch (emotion) {
        case Expression::Neutral:
        case Expression::Happy:
        case Expression::Excited:
        case Expression::Curious:
        case Expression::Thinking:
        case Expression::Affection:
        case Expression::Surprised:
        case Expression::Sad:
            return emotion;
        default:
            return Expression::Neutral;
        }
    }

    static Expression parse_emotion(std::string_view name) noexcept {
        constexpr std::string_view names[] = {"Neutral",  "Happy",     "Excited",   "Curious",
                                              "Thinking", "Affection", "Surprised", "Sad"};
        constexpr Expression expressions[] = {Expression::Neutral,   Expression::Happy,    Expression::Excited,
                                              Expression::Curious,   Expression::Thinking, Expression::Affection,
                                              Expression::Surprised, Expression::Sad};
        for (std::size_t i = 0; i < std::size(names); ++i) {
            if (name.size() != names[i].size())
                continue;
            bool equal = true;
            for (std::size_t j = 0; j < name.size(); ++j) {
                const auto lower = [](char c) { return c >= 'A' && c <= 'Z' ? c + ('a' - 'A') : c; };
                equal = equal && lower(name[j]) == lower(names[i][j]);
            }
            if (equal)
                return expressions[i];
        }
        return Expression::Neutral;
    }

    void set_base(Expression mood) noexcept {
        mood_ = whitelist(mood);
        note_activity();
    }
    // Same input interface as Avatar, for the render task's shared-state bridge.
    void set_expression(Expression mood) noexcept {
        set_base(mood);
    }

    void set_voice_state(VoiceState voice) noexcept {
        if (voice_ != voice) {
            voice_ = voice;
            controller_.set_voice_state(voice);
            controller_.clear_overlay();
            note_activity();
        }
    }
    VoiceState voice_state() const noexcept {
        return voice_;
    }

    void set_overlay(Expression expression, std::uint32_t hold_ms) noexcept {
        controller_.set_overlay(expression, hold_ms);
        note_activity();
    }
    void clear_overlay() noexcept {
        controller_.clear_overlay();
    }
    void note_activity() noexcept {
        activity_pending_ = true;
    }

    void set_connected(bool connected) noexcept {
        if (connected_ == connected)
            return;
        connected_ = connected;
        disconnect_started_ = false;
        controller_.clear_overlay();
        set_voice_state(VoiceState::Idle);
        mood_ = Expression::Neutral;
        note_activity();
    }

    Expression resolve(std::uint32_t now_ms) noexcept {
        if (activity_pending_ || voice_ != VoiceState::Idle) {
            last_activity_ms_ = now_ms;
            activity_pending_ = false;
        }
        if (!connected_ && (!disconnect_started_ || now_ms - last_disconnect_ms_ >= kDisconnectPeriodMs)) {
            controller_.set_overlay(Expression::Dizzy, kDisconnectHoldMs);
            last_disconnect_ms_ = now_ms;
            disconnect_started_ = true;
        }
        const bool resting = voice_ == VoiceState::Idle && now_ms - last_activity_ms_ >= sleep_after_ms_;
        controller_.set_base(resting                          ? Expression::Sleepy
                             : voice_ == VoiceState::Speaking ? mood_
                                                              : Expression::Neutral);
        // Decay belongs to this policy. Keep the legacy controller's decay
        // clock fresh even after long gaps between frames.
        controller_.note_activity();
        return controller_.resolve(now_ms);
    }

private:
    ExpressionController controller_{};
    Expression mood_{Expression::Neutral};
    VoiceState voice_{VoiceState::Idle};
    std::uint32_t sleep_after_ms_;
    std::uint32_t last_activity_ms_{0};
    std::uint32_t last_disconnect_ms_{0};
    bool activity_pending_{true};
    bool connected_{true};
    bool disconnect_started_{false};
};

} // namespace stackchan::avatar
