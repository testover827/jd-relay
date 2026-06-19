// keygen.cpp — Generate ECDSA + ECDH key pairs and AES session key
//
// Usage:
//   keygen <output_dir>
//
// Creates:
//   <output_dir>/ecdsa_private.pem
//   <output_dir>/ecdsa_public.pem
//   <output_dir>/ecdh_private.pem
//   <output_dir>/ecdh_public.pem
//   (AES key printed to stdout)

#include "jd_relay/crypto/key_manager.h"
#include <iostream>
#include <filesystem>
#include <cstdlib>

int main(int argc, char* argv[]) {
    if (argc < 2) {
        std::cerr << "Usage: keygen <output_dir>\n";
        std::cerr << "\nGenerates:\n";
        std::cerr << "  ecdsa_private.pem / ecdsa_public.pem  (for signing)\n";
        std::cerr << "  ecdh_private.pem  / ecdh_public.pem   (for key exchange)\n";
        std::cerr << "  AES key printed to stdout (set as RELAY_AES_KEY)\n";
        return 1;
    }

    std::string output_dir = argv[1];
    std::filesystem::create_directories(output_dir);

    try {
        jd_relay::crypto::KeyManager::generate_all_keys(output_dir);
    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << std::endl;
        return 1;
    }

    return 0;
}
