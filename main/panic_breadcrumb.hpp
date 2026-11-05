// SPDX-License-Identifier: MIT
#pragma once

// Ticket 15h: lightweight panic breadcrumb.
//
// A --wrap'd esp_panic_handler stores the panic kind, faulting PC, task name,
// a few backtrace PCs and the reason/abort text in RTC no-init RAM (survives
// the panic's software reset; no NVS / flash writes anywhere). On the next
// boot app_main calls panic_breadcrumb_init(), which turns it into one short
// line and clears it. The conversation task reports that line next to
// reset_reason in the form_state event, so the gateway can log it.

namespace stackchan::app {

// Call once, early in app_main. Consumes (and clears) the stored crumb.
void panic_breadcrumb_init();

// Short one-line summary from the previous boot's panic, or "" if none.
// JSON-safe (no quotes, backslashes or control characters).
const char* panic_breadcrumb_text();

} // namespace stackchan::app
