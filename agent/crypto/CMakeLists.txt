# src/crypto/ — Independent encryption library

set(CRYPTO_SOURCES
    src/aes_gcm_cipher.cpp
    src/ecdsa_signer.cpp
    src/ecdh_key_exchange.cpp
    src/envelope.cpp
    src/replay_guard.cpp
    src/key_manager.cpp
    src/crypto_codec.cpp
    src/base64.cpp
)

add_library(jd_relay_crypto STATIC ${CRYPTO_SOURCES})

target_include_directories(jd_relay_crypto
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(jd_relay_crypto
    PUBLIC
        OpenSSL::Crypto
        OpenSSL::SSL
        Threads::Threads
)

# nlohmann_json
if(TARGET nlohmann_json::nlohmann_json)
    target_link_libraries(jd_relay_crypto PUBLIC nlohmann_json::nlohmann_json)
else()
    target_include_directories(jd_relay_crypto SYSTEM PUBLIC /usr/include)
endif()
