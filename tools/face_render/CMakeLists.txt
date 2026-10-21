# SPDX-FileCopyrightText: 2026 Joseph Zhou
# SPDX-License-Identifier: BSL-1.0
cmake_minimum_required(VERSION 3.16)
project(face_render CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
get_filename_component(ROOT ../.. ABSOLUTE)
find_program(NODE_EXECUTABLE node REQUIRED)
set(FACE_SOURCE "${ROOT}/assets/grok_face.avdsl" CACHE FILEPATH "Face DSL (override to capture an original golden)")
file(GLOB COMPILER_SOURCES "${ROOT}/tools/avatar_dsl/*.js" "${ROOT}/tools/avatar_dsl/*.mjs")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/face.hpp"
    COMMAND ${NODE_EXECUTABLE} "${ROOT}/tools/avatar_dsl/cli.mjs" "${FACE_SOURCE}" "${CMAKE_CURRENT_BINARY_DIR}/face.avbc"
    COMMAND ${CMAKE_COMMAND} -DINPUT=${CMAKE_CURRENT_BINARY_DIR}/face.avbc -DOUTPUT=${CMAKE_CURRENT_BINARY_DIR}/face.hpp -P "${CMAKE_CURRENT_SOURCE_DIR}/embed.cmake"
    DEPENDS "${FACE_SOURCE}" ${COMPILER_SOURCES} embed.cmake VERBATIM)
add_executable(face_render face_render.cpp "${CMAKE_CURRENT_BINARY_DIR}/face.hpp"
    "${ROOT}/components/avatar_vm/vm.cpp" "${ROOT}/components/avatar_vm/decoder.cpp")
target_include_directories(face_render PRIVATE "${CMAKE_CURRENT_BINARY_DIR}" "${ROOT}/main"
    "${ROOT}/wasm/shim" "${ROOT}/components/avatar_vm/include"
    "${ROOT}/components/tl_expected/expected/include")
target_compile_options(face_render PRIVATE -Wall -Wextra)
enable_testing()
foreach(test safe_area sprout unchanged)
    add_test(NAME ${test} COMMAND face_render --test ${test})
endforeach()
