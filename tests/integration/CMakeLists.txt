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
