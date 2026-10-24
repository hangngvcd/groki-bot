// SPDX-License-Identifier: BSL-1.0
#pragma once
#include <cstdint>
namespace stackchan::conversation {
struct XiaoyaCommand {
    enum class Kind { Form, Intro, Volume } kind{Kind::Form};
    std::uint8_t form{1}, from{0}; // zero means omitted
    std::uint8_t volume{0};        // Kind::Volume: speaker volume 0..100 %
    bool animate{false}, force{false};
};
}
