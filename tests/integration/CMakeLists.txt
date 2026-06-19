# tests/integration/ — Phase 2+ integration tests

find_package(GTest REQUIRED)

add_executable(phase2_tests
    test_phase2_transport.cpp
)

target_link_libraries(phase2_tests
    PRIVATE
        jd_relay_forwarder
        jd_relay_agent
        jd_relay_crypto
        jd_relay_protocol
        GTest::gtest
        GTest::gtest_main
        Threads::Threads
)

add_test(NAME phase2_tests COMMAND phase2_tests)

# Cross-language end-to-end test (C++ Agent ↔ Python Forwarder)
add_executable(test_cross_ws
    test_cross_ws.cpp
)

target_link_libraries(test_cross_ws
    PRIVATE
        jd_relay_agent
        jd_relay_crypto
        jd_relay_protocol
        Threads::Threads
)

# Minimal C++ WebSocket server for cross-language testing
add_executable(test_cross_ws_server
    test_cross_ws_server.cpp
)

target_link_libraries(test_cross_ws_server
    PRIVATE
        jd_relay_forwarder
        jd_relay_crypto
        jd_relay_protocol
        Threads::Threads
)
