// SPDX-License-Identifier: MIT
#include "flash_worker.hpp"

#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_memory_utils.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>
#include <freertos/task.h>
#include <nvs.h>

namespace stackchan::app::flash_worker {
namespace {
constexpr const char* kTag = "flash-wkr";
// NVS set+commit (page write, possible page erase / GC) uses ~2 KiB of stack.
constexpr std::uint32_t kStackBytes = 4096;

struct Job {
    void (*fn)(void*);
    void* ctx;
    SemaphoreHandle_t done;
};

QueueHandle_t s_queue = nullptr;
SemaphoreHandle_t s_start_lock = nullptr;
portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;

void worker(void*)
{
    Job job;
    for (;;) {
        if (xQueueReceive(s_queue, &job, portMAX_DELAY) == pdTRUE) {
            job.fn(job.ctx);
            xSemaphoreGive(job.done);
        }
    }
}
} // namespace

bool caller_stack_is_external()
{
    volatile int probe = 0;
    return esp_ptr_external_ram(const_cast<int*>(&probe));
}

bool start()
{
    if (s_start_lock == nullptr) {
        // Create the lock once; benign race resolved under the spinlock.
        SemaphoreHandle_t lock = xSemaphoreCreateMutex();
        taskENTER_CRITICAL(&s_mux);
        if (s_start_lock == nullptr) { s_start_lock = lock; lock = nullptr; }
        taskEXIT_CRITICAL(&s_mux);
        if (lock) vSemaphoreDelete(lock);
        if (s_start_lock == nullptr) return false;
    }
    xSemaphoreTake(s_start_lock, portMAX_DELAY);
    bool ok = s_queue != nullptr;
    if (!ok) {
        QueueHandle_t q = xQueueCreate(4, sizeof(Job));
        // Plain xTaskCreate: stack from internal RAM (FreeRTOS default caps),
        // never PSRAM. That is the whole point of this task.
        if (q && xTaskCreatePinnedToCore(worker, "flash-wkr", kStackBytes, nullptr,
                                         tskIDLE_PRIORITY + 4, nullptr, tskNO_AFFINITY) == pdPASS) {
            s_queue = q;
            ok = true;
        } else {
            if (q) vQueueDelete(q);
            ESP_LOGE(kTag, "start failed (internal largest=%u)",
                     static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL)));
        }
    }
    xSemaphoreGive(s_start_lock);
    return ok;
}

bool run(void (*fn)(void*), void* ctx)
{
    if (!caller_stack_is_external()) {
        fn(ctx);
        return true;
    }
    if (s_queue == nullptr && !start()) {
        ESP_LOGE(kTag, "no worker: flash op skipped (caller stack in PSRAM)");
        return false;
    }
    SemaphoreHandle_t done = xSemaphoreCreateBinary();
    if (done == nullptr) return false;
    Job job{fn, ctx, done};
    bool ok = xQueueSend(s_queue, &job, portMAX_DELAY) == pdTRUE;
    // Wait without timeout: job/ctx live on the caller's stack.
    if (ok) xSemaphoreTake(done, portMAX_DELAY);
    vSemaphoreDelete(done);
    return ok;
}

namespace {
struct U8Op {
    const char* ns;
    const char* key;
    std::uint8_t value;
    bool ok;
};
} // namespace

bool nvs_write_u8(const char* ns, const char* key, std::uint8_t value)
{
    U8Op op{ns, key, value, false};
    run(+[](void* p) {
            auto& o = *static_cast<U8Op*>(p);
            nvs_handle_t h;
            if (nvs_open(o.ns, NVS_READWRITE, &h) != ESP_OK) return;
            o.ok = nvs_set_u8(h, o.key, o.value) == ESP_OK && nvs_commit(h) == ESP_OK;
            nvs_close(h);
        },
        &op);
    if (!op.ok) ESP_LOGW(kTag, "NVS write failed: %s/%s", ns, key);
    return op.ok;
}

bool nvs_read_u8(const char* ns, const char* key, std::uint8_t& value)
{
    U8Op op{ns, key, value, false};
    run(+[](void* p) {
            auto& o = *static_cast<U8Op*>(p);
            nvs_handle_t h;
            if (nvs_open(o.ns, NVS_READONLY, &h) != ESP_OK) return;
            o.ok = nvs_get_u8(h, o.key, &o.value) == ESP_OK;
            nvs_close(h);
        },
        &op);
    if (op.ok) value = op.value;
    return op.ok;
}

} // namespace stackchan::app::flash_worker
