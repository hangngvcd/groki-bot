# SPDX-License-Identifier: BSL-1.0
cmake_minimum_required(VERSION 3.16)
project(lingling_assets_tests CXX)
set(CMAKE_CXX_STANDARD 20)
add_executable(test_assets test_assets.cpp ../../assets.cpp)
target_include_directories(test_assets PRIVATE ../../include)
enable_testing()
add_test(NAME assets COMMAND test_assets)
