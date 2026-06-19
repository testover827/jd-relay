# tests/unit/ — Phase 1 crypto unit tests

add_executable(crypto_tests
    test_main.cpp
    test_aes_gcm.cpp
    test_ecdsa.cpp
    test_ecdh.cpp
    test_envelope_roundtrip.cpp
    test_replay_guard.cpp
    test_tamper_detection.cpp
)

target_link_libraries(crypto_tests
    PRIVATE
        jd_relay_crypto
        gtest
        gtest_main
)

target_include_directories(crypto_tests
    PRIVATE
        ${CMAKE_SOURCE_DIR}/agent/crypto/include
)

# Register with CTest
include(GoogleTest)
gtest_discover_tests(crypto_tests)
