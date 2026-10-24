// SPDX-License-Identifier: MIT
#pragma once
// Internal-RAM-stack executor for NVS / flash access (ticket 15d).
//
// Any SPI-flash operation (nvs_get_* / nvs_set_* / nvs_commit /
// esp_partition_read|write|erase / esp_flash_*) disables the flash cache, and
// ESP-IDF asserts that the calling task's stack is in internal RAM:
//   assert failed: spi_flash_disable_interrupts_caches_and_other_cpu
//   cache_utils.c:127 (esp_task_stack_is_sane_cache_disabled())
// Tasks created with MALLOC_CAP_SPIRAM stacks (conversation, say_worker,
// audio-stream, *_tx senders, vfetch, asr_*) must therefore NEVER touch NVS or
// flash directly. They call flash_worker::run(), which executes the callback
// on a small dedicated task whose stack is in internal RAM and blocks until it
// returns. Callers already on an internal stack run the callback inline.
//
// tools/test_flash_stack_guard.py enforces this statically.
#include <cstdint>

namespace stackchan::app::flash_worker {

// Create the worker task (idempotent). Call once from app_main, before any
// PSRAM-stack task starts. run() also starts it lazily as a fallback.
bool start();

// Run fn(ctx) on an internal-RAM stack and wait for it to finish. Returns
// false only if the worker could not be started/queued (fn not run).
bool run(void (*fn)(void*), void* ctx);

// True when the calling task's stack lives in external (PSRAM) memory.
bool caller_stack_is_external();

// Convenience helpers for small NVS values (open/set/commit/close).
bool nvs_write_u8(const char* ns, const char* key, std::uint8_t value);
bool nvs_read_u8(const char* ns, const char* key, std::uint8_t& value);

} // namespace stackchan::app::flash_worker
