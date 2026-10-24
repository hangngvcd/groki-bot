# Requirements are discovered before Kconfig is loaded. In the real pass,
# disabled components expose headers only and produce no archive or objects.
if(NOT CMAKE_BUILD_EARLY_EXPANSION AND NOT CONFIG_STACKCHAN_SERVO_ENABLED)
    idf_component_register(INCLUDE_DIRS "include")
    return()
endif()

idf_component_register(
    SRCS
        "scs_bus.cpp"
        "scs_servo.cpp"
    INCLUDE_DIRS "include"
    REQUIRES
        tl_expected
    PRIV_REQUIRES
        driver
        esp_timer
)
target_compile_features(${COMPONENT_LIB} PUBLIC cxx_std_20)
