// SPDX-License-Identifier: BSL-1.0
#pragma once
#include <cstdint>
#include <memory>
#include <vector>
#include <M5GFX.h>

namespace stackchan::glyph_overlay {
// show()/clear(): any task. compose()/take_ended(): render task only.
// compose() draws the glyph over target (call after the face, before the
// status ring) and returns true while it is visible.
void show(std::shared_ptr<const std::vector<std::uint8_t>> png, std::uint32_t hold_ms);
void clear();
bool compose(M5Canvas& target, std::uint32_t now_ms);
bool take_ended();
}
