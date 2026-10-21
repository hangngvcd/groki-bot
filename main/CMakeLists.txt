set(_main_priv_reqs
    board
    avatar
    groki_motion
    conversation
    config_service
    wifi_config_service
    espressif__esp_audio_codec
    espressif__esp-sr
    json
    M5Unified
    M5GFX
    esp_timer
    esp_psram
    driver
    nvs_flash
    esp_wifi
    esp_netif
    esp_event
    app_update
    lwip
)

# Optional dependencies are added after registration with
# idf_component_optional_requires: Kconfig is unavailable during IDF's early
# requirements pass. All project components are discovered by the root build;
# their own source gates make disabled components header-only interfaces.
set(_main_srcs
    "ap_screen.cpp"
    "app_main.cpp"
    "asr_probe.cpp"
    "atom_status.cpp"
    "audio_codec.cpp"
    "battery.cpp"
    "camera_service.cpp"
    "captive_portal.cpp"
    "demo_loop.cpp"
    "boot_button_task.cpp"
    "device_ui.cpp"
    "diag.cpp"
    "face_config.cpp"
    "i2c_dump.cpp"
    "led_task.cpp"
    "lt_timer.cpp"
    "mic_lip_sync_task.cpp"
    "qr_task.cpp"
    "render_task.cpp"
    "screens.cpp"
    "servo_limits.cpp"
    "servo_task.cpp"
    "settings_sinks.cpp"
    "speech.cpp"
    "voice_db.cpp"
    "hmm_voice.cpp"
    "wifi_sta.cpp"
)
if(NOT CONFIG_STACKCHAN_SERVO_ENABLED)
    list(REMOVE_ITEM _main_srcs "servo_task.cpp")
endif()
if(NOT CONFIG_STACKCHAN_LED_ENABLED)
    list(REMOVE_ITEM _main_srcs "led_task.cpp")
endif()
if(NOT CONFIG_STACKCHAN_JTTS_ENABLED)
    list(REMOVE_ITEM _main_srcs "speech.cpp" "voice_db.cpp" "hmm_voice.cpp")
endif()
if(NOT CONFIG_STACKCHAN_CAMERA_ENABLED)
    list(REMOVE_ITEM _main_srcs "camera_service.cpp" "qr_task.cpp")
endif()
if(CONFIG_STACKCHAN_CONVERSATION_ENABLED)
    list(APPEND _main_srcs "conversation_task.cpp")
endif()
if(CONFIG_STACKCHAN_AUDIO_STREAM_ENABLED)
    list(APPEND _main_srcs "audio_stream_sink.cpp")
endif()
if(CONFIG_STACKCHAN_WIFI_AUDIO_ENABLED)
    list(APPEND _main_srcs "wifi_audio.cpp")
endif()

# quirc (QR decoder) is only needed when the camera driver is compiled in.
# Same gating as components/board/CMakeLists.txt — keeps atoms3r builds free
# of the managed component download.
if(CONFIG_STACKCHAN_CAMERA_ENABLED)
    list(APPEND _main_priv_reqs espressif__quirc)
endif()

idf_component_register(
    SRCS ${_main_srcs}
    INCLUDE_DIRS "."
    PRIV_REQUIRES ${_main_priv_reqs}
)
target_compile_features(${COMPONENT_LIB} PRIVATE cxx_std_20)

if(CONFIG_STACKCHAN_SERVO_ENABLED)
    idf_component_optional_requires(PRIVATE scs_servo)
endif()
if(CONFIG_STACKCHAN_JTTS_ENABLED)
    idf_component_optional_requires(PRIVATE jtts)
endif()
if(CONFIG_STACKCHAN_TELEGRAM_ENABLED)
    idf_component_optional_requires(PRIVATE telegram)
endif()
if(CONFIG_STACKCHAN_ESPNOW_POC)
    idf_component_optional_requires(PRIVATE espnow_poc)
endif()
if(CONFIG_STACKCHAN_ESPNOW_REMOTE_ENABLED)
    idf_component_optional_requires(PRIVATE espnow_remote)
endif()
