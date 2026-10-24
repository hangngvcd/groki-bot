// SPDX-License-Identifier: BSL-1.0
// Ticket 17: full-screen 生字 glyph overlay. The conversation task hands over a
// validated PNG; the render task decodes it into its own PSRAM sprite and
// composes it over the face until hold_ms expires. Self-contained so the face
// painter and asset pack code stay untouched.
#include "glyph_overlay.hpp"
#include "conversation/xiaoya_control.hpp"
#include <mutex>
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_timer.h>

namespace stackchan::glyph_overlay {
namespace {
constexpr const char* kTag = "glyph_overlay";
constexpr std::uint32_t kDecodeWaitMs = 2000;

std::uint32_t now_ms_clock() { return static_cast<std::uint32_t>(esp_timer_get_time() / 1000); }

// Request state, shared between the conversation and render tasks.
std::mutex mutex;
std::shared_ptr<const std::vector<std::uint8_t>> pending;
std::uint32_t pending_hold = 0, requested_at = 0;
bool changed = false;

// Render-task-only state (no lock needed).
std::uint32_t hold = 0, visible_at = 0;
bool visible = false, ended = false;
std::shared_ptr<const std::vector<std::uint8_t>> waiting;  // PNG waiting for heap
std::uint32_t waiting_since = 0;

M5Canvas& sprite() {
    static M5Canvas canvas;  // constructed on first render-task use
    return canvas;
}

void hide() {
    if (visible) {
        ESP_LOGI(kTag, "hidden");
        visible = false;
        ended = true;
    }
    sprite().deleteSprite();  // give back ~115 KB PSRAM
}
}  // namespace

void show(std::shared_ptr<const std::vector<std::uint8_t>> png, std::uint32_t hold_ms) {
    std::lock_guard<std::mutex> lock(mutex);
    ESP_LOGI(kTag, "show: %u bytes, hold %u ms", unsigned(png ? png->size() : 0), unsigned(hold_ms));
    pending = std::move(png);
    pending_hold = hold_ms;
    requested_at = now_ms_clock();
    changed = true;
}

void clear() {
    std::lock_guard<std::mutex> lock(mutex);
    pending.reset();
    changed = true;
}

bool compose(M5Canvas& target, std::uint32_t /*now_ms*/) {
    {
        std::lock_guard<std::mutex> lock(mutex);
        if (changed) {
            changed = false;
            hide();
            waiting = std::move(pending);
            hold = pending_hold;
            waiting_since = requested_at;
        }
    }
    if (waiting) {
        // Decode outside the lock: PNG decode takes tens of ms.
        if (!conversation::glyph_png_header(*waiting)) {
            ESP_LOGW(kTag, "dropping invalid PNG: %u bytes", unsigned(waiting->size()));
            waiting.reset();
        } else if (now_ms_clock() - waiting_since >= kDecodeWaitMs) {
            ESP_LOGW(kTag, "dropping PNG after heap wait: %u bytes", unsigned(waiting->size()));
            waiting.reset();
            sprite().deleteSprite();
        } else if (heap_caps_get_largest_free_block(MALLOC_CAP_DEFAULT) >= 64 * 1024 &&
                   heap_caps_get_free_size(MALLOC_CAP_INTERNAL) >= 16 * 1024) {
            // Same guard as the PNG face painter: pngle needs ~50 KB.
            auto& s = sprite();
            s.setPsram(true);
            s.setColorDepth(16);
            if (s.getBuffer() || s.createSprite(240, 240)) {
                s.fillScreen(0);
                if (s.drawPng(waiting->data(), waiting->size(), 0, 0)) {
                    visible = true;
                    visible_at = now_ms_clock();
                    ESP_LOGI(kTag, "decoded: %u bytes, hold %u ms", unsigned(waiting->size()), unsigned(hold));
                } else {
                    ESP_LOGW(kTag, "PNG decode failed: %u bytes", unsigned(waiting->size()));
                    s.deleteSprite();
                }
                waiting.reset();
            }
        }
    }
    if (visible && now_ms_clock() - visible_at >= hold) hide();
    if (!visible) return false;
    sprite().pushSprite(&target, 0, 0);
    return true;
}

bool take_ended() {
    const bool result = ended;  // render-task only
    ended = false;
    return result;
}
}  // namespace stackchan::glyph_overlay
