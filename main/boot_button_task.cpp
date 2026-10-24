// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: BSL-1.0

#include "boot_button_task.hpp"
#include "sdkconfig.h"
#if CONFIG_STACKCHAN_BOARD_SPOTPEAR_128CAM
#include "board/spotpear_128cam_pins.hpp"
#include "conversation/boot_button.hpp"
#include "wifi_sta.hpp"
#include <esp_log.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

namespace stackchan::app {
void start_boot_button_task(SharedState& state) {
    gpio_config_t cfg{};
    cfg.pin_bit_mask = 1ULL << board::spotpear_128cam::boot_button;
    cfg.mode = GPIO_MODE_INPUT;
    cfg.pull_up_en = GPIO_PULLUP_ENABLE;
    cfg.pull_down_en = GPIO_PULLDOWN_DISABLE;
    cfg.intr_type = GPIO_INTR_DISABLE;
    ESP_ERROR_CHECK(gpio_config(&cfg));
    xTaskCreate([](void* arg) {
        auto& shared = *static_cast<SharedState*>(arg);
        conversation::BootButton button;
        for (;;) {
            const auto mode = conversation::device_mode_for(
                static_cast<conversation::ButtonConvStatus>(shared.conv.status.load()),
                static_cast<avatar::VoiceState>(shared.conv.voice_state.load()),
                false, wifi_ap_active()); // Resting and Idle both request listening.
            const auto action = button.update(gpio_get_level(board::spotpear_128cam::boot_button) == 0,
                static_cast<std::uint32_t>(esp_timer_get_time() / 1000), mode);
            using conversation::ButtonAction;
            constexpr const char* names[] = {"None", "Listen", "Abort", "Retry", "EnterSoftAp", "ExitSoftAp"};
            if (action != ButtonAction::None)
                ESP_LOGI("boot_button", "%s", names[static_cast<unsigned>(action)]);
            switch (action) {
            case ButtonAction::Listen: shared.listen_request.store(true); break;
            case ButtonAction::Abort: shared.barge_in_request.store(true); break;
            case ButtonAction::Retry: shared.reconnect_now.store(true); break;
            case ButtonAction::EnterSoftAp: wifi_enable_ap_mode(); break;
            case ButtonAction::ExitSoftAp: wifi_disable_ap_mode(); break;
            default: break;
            }
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }, "boot_button", 4096, &state, 5, nullptr);
}
}
#endif
