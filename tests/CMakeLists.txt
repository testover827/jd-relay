# tests/ — All C++ test targets
#
# Structure:
#   tests/unit/        — Phase 1 crypto unit tests
#   tests/integration/ — Phase 2+ transport integration tests

# ── GoogleTest ────────────────────────────────────────────────
set(GTEST_SRC_DIR "/usr/src/googletest" CACHE PATH "GoogleTest source")
if(EXISTS "${GTEST_SRC_DIR}/CMakeLists.txt")
    set(BUILD_GMOCK ON CACHE BOOL "" FORCE)
    set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)
    add_subdirectory(${GTEST_SRC_DIR} ${CMAKE_BINARY_DIR}/_googletest EXCLUDE_FROM_ALL)
else()
    message(WARNING "GoogleTest source not found at ${GTEST_SRC_DIR}")
endif()

find_package(GTest QUIET)

# ── Unit tests (Phase 1: crypto) ──────────────────────────────
add_subdirectory(unit)

# ── Integration tests (Phase 2: transport) ────────────────────
add_subdirectory(integration)
