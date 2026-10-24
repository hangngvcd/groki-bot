// SPDX-License-Identifier: BSL-1.0
#pragma once
#include <cstdint>
namespace stackchan::conversation {
struct XiaoyaCommand {
    enum class Kind { Form, Intro } kind{Kind::Form};
    std::uint8_t form{1}, from{0}; // zero means omitted
    bool animate{false}, force{false};
};
}
