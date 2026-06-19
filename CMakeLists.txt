cmake_minimum_required(VERSION 3.16)
project(jd_relay VERSION 0.1.0 LANGUAGES CXX)

# ── C++ standard ──────────────────────────────────────────────
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# ── Default build type ────────────────────────────────────────
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# ── Output directories ────────────────────────────────────────
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib)
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)

# ── Compile warnings ──────────────────────────────────────────
add_compile_options(-Wall -Wextra -Wpedantic -Wno-unused-parameter)

# ── Find packages ─────────────────────────────────────────────
find_package(OpenSSL REQUIRED)
find_package(Threads REQUIRED)
find_package(Boost 1.74 REQUIRED)

# nlohmann_json — header-only, try system package then FetchContent
find_package(nlohmann_json 3.2.0 QUIET)
if(NOT nlohmann_json_FOUND)
    message(STATUS "nlohmann_json not found via find_package, using header-only fallback")
    set(JSON_INCLUDE_DIR "" CACHE PATH "Path to nlohmann/json.hpp")
endif()

# ── Source modules ────────────────────────────────────────────
add_subdirectory(src/crypto)
add_subdirectory(src/protocol)
add_subdirectory(src/tools)
add_subdirectory(src/forwarder)
add_subdirectory(src/agent)

# ── Tests ─────────────────────────────────────────────────────
enable_testing()
add_subdirectory(tests)
