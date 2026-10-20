// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: BSL-1.0

#pragma once

#include <cstring>
#include <optional>

#include <cJSON.h>

#include "avatar/expression.hpp"

namespace stackchan::conversation {

// Face milestones are separate from the half-duplex audio state machine:
// TTS start/stop controls the face even when audio is buffered or absent.
// Called from the same parse_control other-sink in firmware and host tests.
inline std::optional<avatar::VoiceState> companion_voice_state(const char* type, const cJSON* root) noexcept {
    const auto* state_item = cJSON_GetObjectItemCaseSensitive(root, "state");
    const char* state = cJSON_IsString(state_item) ? state_item->valuestring : "";
    if (std::strcmp(type, "listen") == 0) {
        if (std::strcmp(state, "start") == 0 || std::strcmp(state, "detect") == 0 || state[0] == '\0')
            return avatar::VoiceState::Listening;
        if (std::strcmp(state, "stop") == 0)
            return avatar::VoiceState::Thinking;
    } else if (std::strcmp(type, "stt") == 0) {
        return avatar::VoiceState::Thinking;
    } else if (std::strcmp(type, "tts") == 0) {
        if (std::strcmp(state, "start") == 0)
            return avatar::VoiceState::Speaking;
        if (std::strcmp(state, "stop") == 0)
            return avatar::VoiceState::Idle;
    }
    return std::nullopt;
}

} // namespace stackchan::conversation
