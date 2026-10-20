# Requirements are discovered before Kconfig is loaded. In the real pass,
# disabled components expose headers only and produce no archive or objects.
if(NOT CMAKE_BUILD_EARLY_EXPANSION AND NOT CONFIG_STACKCHAN_ESPNOW_REMOTE_ENABLED)
    idf_component_register(INCLUDE_DIRS "include")
    return()
endif()

idf_component_register(
    SRCS "espnow_remote.cpp"
    INCLUDE_DIRS "include"
    REQUIRES "espressif__esp-now" "tl_expected"
    PRIV_REQUIRES "esp_wifi" "esp_netif" "esp_event" "nvs_flash")

target_compile_features(${COMPONENT_LIB} PRIVATE cxx_std_20)
