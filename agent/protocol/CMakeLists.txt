# src/protocol/ — header-only library shared by Forwarder and Agent
add_library(jd_relay_protocol INTERFACE)
target_include_directories(jd_relay_protocol INTERFACE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)
target_link_libraries(jd_relay_protocol INTERFACE
    jd_relay_crypto
)
