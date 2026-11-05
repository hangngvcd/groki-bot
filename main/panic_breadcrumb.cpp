// SPDX-License-Identifier: MIT
#include "panic_breadcrumb.hpp"

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include <esp_attr.h>
#include <esp_cpu.h>
#include <esp_debug_helpers.h>
#include <esp_log.h>
#include <esp_memory_utils.h>
#include <esp_private/cache_utils.h>
#include <esp_private/panic_internal.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <sdkconfig.h>
#if CONFIG_IDF_TARGET_ARCH_XTENSA
#include <esp_cpu_utils.h>
#include <xtensa_context.h>
#endif

// Owned by esp_system/panic.c; set by abort()/assert before the panic.
extern "C" bool g_panic_abort;
extern "C" char* g_panic_abort_details;
extern "C" void __real_esp_panic_handler(panic_info_t* info);

namespace {

constexpr std::uint32_t kMagic = 0x50414e31; // "PAN1"
constexpr int kFrames = 4;

struct Crumb {
    std::uint32_t magic;
    std::uint32_t kind;
    std::uint32_t core;
    std::uint32_t pc;
    std::uint32_t nbt;
    std::uint32_t bt[kFrames];
    char task[16];
    char reason[64];
    std::uint32_t check;
};

RTC_NOINIT_ATTR Crumb s_crumb;
char s_text[256];

std::uint32_t IRAM_ATTR checksum(const Crumb& c)
{
    const auto* w = reinterpret_cast<const std::uint32_t*>(&c);
    std::uint32_t x = 0x9e3779b9;
    for (std::size_t i = 0; i < offsetof(Crumb, check) / 4; ++i) x = (x ^ w[i]) * 16777619u;
    return x;
}

// Reading flash rodata or PSRAM with the cache off would fault inside the
// panic handler, so anything not in internal RAM is skipped then.
bool IRAM_ATTR readable(const void* p, bool cache_on)
{
    if (p == nullptr) return false;
    if (esp_ptr_internal(p)) return true;
    return cache_on && (esp_ptr_external_ram(p) || esp_ptr_in_drom(p));
}

void IRAM_ATTR copy_str(char* dst, std::size_t cap, const char* src, bool cache_on)
{
    std::size_t i = 0;
    if (readable(src, cache_on)) {
        for (; i + 1 < cap && src[i] != '\0'; ++i) {
            const char ch = src[i];
            dst[i] = (ch == '"' || ch == '\\' || static_cast<unsigned char>(ch) < 0x20) ? '?' : ch;
        }
    }
    dst[i] = '\0';
}

} // namespace

extern "C" void IRAM_ATTR __wrap_esp_panic_handler(panic_info_t* info)
{
    Crumb& c = s_crumb;
    const bool cache_on = spi_flash_cache_enabled();
    c.magic = 0;
    c.kind = g_panic_abort ? static_cast<std::uint32_t>(PANIC_EXCEPTION_ABORT)
                           : static_cast<std::uint32_t>(info->exception);
    c.core = static_cast<std::uint32_t>(info->core);
    c.pc = reinterpret_cast<std::uint32_t>(info->addr);
    c.nbt = 0;
    c.task[0] = '\0';
    copy_str(c.reason, sizeof(c.reason), g_panic_abort ? g_panic_abort_details : info->reason, cache_on);
    if (cache_on) {
        TaskHandle_t t = xTaskGetCurrentTaskHandleForCore(info->core);
        if (t != nullptr) copy_str(c.task, sizeof(c.task), pcTaskGetName(t), cache_on);
#if CONFIG_IDF_TARGET_ARCH_XTENSA
        const auto* f = static_cast<const XtExcFrame*>(info->frame);
        if (f != nullptr) {
            if (c.pc == 0) c.pc = esp_cpu_process_stack_pc(static_cast<std::uint32_t>(f->pc));
            esp_backtrace_frame_t fr{static_cast<std::uint32_t>(f->pc), static_cast<std::uint32_t>(f->a1),
                                     static_cast<std::uint32_t>(f->a0), f};
            while (c.nbt < kFrames && fr.next_pc != 0 && esp_stack_ptr_is_sane(fr.sp)) {
                if (!esp_backtrace_get_next_frame(&fr)) break;
                c.bt[c.nbt++] = esp_cpu_process_stack_pc(fr.pc);
            }
        }
#endif
    }
    c.check = checksum(c);
    c.magic = kMagic;
    __real_esp_panic_handler(info);
}

namespace stackchan::app {

void panic_breadcrumb_init()
{
    s_text[0] = '\0';
    Crumb& c = s_crumb;
    if (c.magic != kMagic || c.check != checksum(c) || c.nbt > kFrames) {
        c.magic = 0;
        return;
    }
    c.task[sizeof(c.task) - 1] = '\0';
    c.reason[sizeof(c.reason) - 1] = '\0';
    static const char* const kKinds[] = {"debug", "int_wdt", "task_wdt", "abort", "fault"};
    const char* kind = c.kind < sizeof(kKinds) / sizeof(kKinds[0]) ? kKinds[c.kind] : "?";
    char bt[4 * 11 + 1] = "";
    for (std::uint32_t i = 0; i < c.nbt; ++i) {
        char one[12];
        std::snprintf(one, sizeof(one), "%s%08lx", i ? "," : "", static_cast<unsigned long>(c.bt[i]));
        std::strncat(bt, one, sizeof(bt) - std::strlen(bt) - 1);
    }
    std::snprintf(s_text, sizeof(s_text), "%s core%lu pc=%08lx task=%s bt=%s %s", kind,
                  static_cast<unsigned long>(c.core), static_cast<unsigned long>(c.pc),
                  c.task[0] ? c.task : "?", bt[0] ? bt : "-", c.reason);
    c.magic = 0; // consume: report it for this boot only
    ESP_LOGW("panic", "previous boot panicked: %s", s_text);
}

const char* panic_breadcrumb_text()
{
    return s_text;
}

} // namespace stackchan::app
