# agent/ws_client/ — WebSocket client with auto-reconnect

find_package(spdlog QUIET)

add_library(jd_relay_agent STATIC
    src/ws_client.cpp
)

target_include_directories(jd_relay_agent PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(jd_relay_agent PUBLIC
    jd_relay_crypto
    jd_relay_protocol
    Boost::boost
    Threads::Threads
)

if(TARGET spdlog::spdlog)
    target_link_libraries(jd_relay_agent PUBLIC spdlog::spdlog)
else()
    # spdlog is header-only, just need the include path
    target_include_directories(jd_relay_agent SYSTEM PUBLIC /usr/include)
endif()
