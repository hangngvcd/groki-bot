set(_board_srcs
    "audio_module_es8388.cpp"
    "board.cpp"
    "camera_gc0308.cpp"
    "io_expander_py32.cpp"
    "led_strip.cpp"
    "nekomimi_led_strip.cpp"
    "si12t_touch.cpp"
)

set(_board_priv_reqs driver)
if(NOT CONFIG_STACKCHAN_PY32_ENABLED)
    list(REMOVE_ITEM _board_srcs "io_expander_py32.cpp" "led_strip.cpp")
endif()
if(NOT CONFIG_STACKCHAN_HEAD_TOUCH_ENABLED)
    list(REMOVE_ITEM _board_srcs "si12t_touch.cpp")
endif()
if(NOT CONFIG_STACKCHAN_LED_ENABLED)
    list(REMOVE_ITEM _board_srcs "led_strip.cpp" "nekomimi_led_strip.cpp")
endif()
if(NOT CONFIG_STACKCHAN_CAMERA_ENABLED)
    list(REMOVE_ITEM _board_srcs "camera_gc0308.cpp")
endif()

idf_component_register(
    SRCS ${_board_srcs}
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "."
    REQUIRES
        M5Unified
        M5GFX
        tl_expected
    PRIV_REQUIRES ${_board_priv_reqs}
)
target_compile_features(${COMPONENT_LIB} PUBLIC cxx_std_20)

# Config-dependent dependencies must be added after early expansion.
if(CONFIG_STACKCHAN_LED_ENABLED)
    idf_component_optional_requires(PRIVATE espressif__led_strip)
endif()
if(CONFIG_STACKCHAN_CAMERA_ENABLED)
    idf_component_optional_requires(PRIVATE espressif__esp32-camera)
endif()
