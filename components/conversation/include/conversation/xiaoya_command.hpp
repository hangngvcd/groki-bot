// SPDX-License-Identifier: BSL-1.0
#pragma once
#include <cstdint>
#include <memory>
#include <vector>
namespace stackchan::conversation {
struct XiaoyaCommand {
    enum class Kind { Form, Intro, Volume, Glyph } kind{Kind::Form};
    std::uint8_t form{1}, from{0}; // zero means omitted
    std::uint8_t volume{0};        // Kind::Volume: speaker volume 0..100 %
    std::shared_ptr<const std::vector<std::uint8_t>> png;
    std::uint32_t hold_ms{0};
    bool clear{false};
    bool animate{false}, force{false};
};
}
