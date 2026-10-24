// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-FileCopyrightText: 2026 Kenta IDA <fuga@fugafuga.org>
// SPDX-License-Identifier: BSL-1.0
#pragma once
#include <algorithm>
#include "avatar/canvas.hpp"
namespace stackchan::avatar_vm {
// Compute the design→canvas uniform scale the original face.cpp used.
// For circular displays we fit the 320×240 design rect by its DIAGONAL
// to the panel diameter, with a small margin so corner-anchored effect
// marks (heart / sweat / spark on the 320×240 design's top-right) don't
// graze the visible circle's edge. The earlier 1/√2 inset matched a
// SQUARE design (320×320) and so under-scaled the actual 4:3 rect by
// ~10% on 466×466 — using the proper rectangle diagonal recovers that.
inline float design_scale(const avatar::Canvas& c) noexcept
{
    constexpr float kBaseW = 320.0f;
    constexpr float kBaseH = 240.0f;
    if (c.is_circular()) {
        // Round AMOLEDs report width == height == diameter, so either dim
        // gives the diameter. Slight margin (0.97) keeps the very corner
        // pixels just inside the visible edge.
        constexpr float kCornerMargin = 0.97f;
        constexpr float kBaseDiag = 400.0f;  // sqrt(320² + 240²)
        const float diameter = static_cast<float>(std::min(c.width(), c.height()));
        return diameter / kBaseDiag * kCornerMargin;
    }
    return std::min(static_cast<float>(c.width()) / kBaseW,
                    static_cast<float>(c.height()) / kBaseH);
}

// SpotPear head-centred layout; shared by the body VM and aora eye rings.
inline constexpr float kRoundFaceScale = 0.85f;
inline float round_face_layout_scale(const avatar::Canvas& c) noexcept
{
    return static_cast<float>(std::min(c.width(), c.height())) / 240.0f * kRoundFaceScale;
}

}
