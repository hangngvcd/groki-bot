// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: BSL-1.0

#pragma once
#include "shared_state.hpp"
namespace stackchan::app {
// SpotPear-only GPIO poller; owns the local short-break timer.
void start_boot_button_task(SharedState& state);
}
