# src/tools/ — Standalone CLI tools: encryptor, decryptor, keygen

# keygen — Generate ECDSA + ECDH key pairs and AES session key
add_executable(keygen keygen.cpp)
target_link_libraries(keygen PRIVATE jd_relay_crypto)

# encryptor — Read plaintext JSON from stdin, output encrypted envelope to stdout
add_executable(encryptor encryptor.cpp)
target_link_libraries(encryptor PRIVATE jd_relay_crypto)

# decryptor — Read encrypted envelope from stdin, output plaintext JSON to stdout
add_executable(decryptor decryptor.cpp)
target_link_libraries(decryptor PRIVATE jd_relay_crypto)
