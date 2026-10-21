# SPDX-FileCopyrightText: 2026 Joseph Zhou
# SPDX-License-Identifier: BSL-1.0
cmake_minimum_required(VERSION 3.18)
project(release_versions_host_test C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Debug)
endif()

if(DEFINED ENV{IDF_PATH} AND EXISTS "$ENV{IDF_PATH}/components/json/cJSON/cJSON.c")
    set(CJSON_DIR "$ENV{IDF_PATH}/components/json/cJSON")
else()
    include(FetchContent)
    # SOURCE_SUBDIR points at a directory without a CMakeLists.txt, so
    # MakeAvailable only downloads; cJSON.c is compiled below.
    FetchContent_Declare(cjson
        URL https://github.com/DaveGamble/cJSON/archive/refs/tags/v1.7.19.tar.gz
        DOWNLOAD_EXTRACT_TIMESTAMP TRUE
        SOURCE_SUBDIR no-cmake-build
    )
    FetchContent_MakeAvailable(cjson)
    set(CJSON_DIR ${cjson_SOURCE_DIR})
endif()

add_library(cjson_host STATIC ${CJSON_DIR}/cJSON.c)
target_include_directories(cjson_host PUBLIC ${CJSON_DIR})

enable_testing()
add_executable(test_release_versions test_release_versions.cpp)
target_include_directories(test_release_versions PRIVATE ../..)
target_link_libraries(test_release_versions PRIVATE cjson_host)
target_compile_options(test_release_versions PRIVATE -Wall -Wextra)
add_test(NAME release_versions COMMAND test_release_versions)
