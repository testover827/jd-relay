# src/tools/ — Standalone CLI tools: encryptor, decryptor, keygen, agent_main

# keygen — Generate ECDSA + ECDH key pairs and AES session key
add_executable(keygen keygen.cpp)
target_link_libraries(keygen PRIVATE jd_relay_crypto)

# encryptor — Read plaintext JSON from stdin, output encrypted envelope to stdout
add_executable(encryptor encryptor.cpp)
target_link_libraries(encryptor PRIVATE jd_relay_crypto)

# decryptor — Read encrypted envelope from stdin, output plaintext JSON to stdout
add_executable(decryptor decryptor.cpp)
target_link_libraries(decryptor PRIVATE jd_relay_crypto)

# agent_main — Full Agent binary with config file + Jenkins integration
add_executable(jd_relay_agent_main agent_main.cpp)
target_link_libraries(jd_relay_agent_main
    PRIVATE
        jd_relay_agent
        jd_relay_jenkins
        jd_relay_crypto
        jd_relay_protocol
        Threads::Threads
)
