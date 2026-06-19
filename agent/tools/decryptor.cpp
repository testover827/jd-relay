// decryptor.cpp — Read encrypted envelope from stdin, output plaintext to stdout
//
// Usage:
//   decryptor --ecdsa-key <private.pem> --peer-pub <public.pem> --aes-key <hex>
//
// Or via environment variables:
//   RELAY_ECDSA_PRIVATE_KEY, RELAY_PEER_ECDSA_PUBLIC, RELAY_AES_KEY
//
// Reads encrypted envelope JSON from stdin, writes decrypted plaintext to stdout.
// Exit code: 0 = success, 1 = decryption/verification failure

#include "jd_relay/crypto/crypto_codec.h"
#include "jd_relay/crypto/aes_gcm_cipher.h"
#include "jd_relay/crypto/ecdsa_signer.h"
#include "jd_relay/crypto/key_manager.h"
#include <iostream>
#include <sstream>
#include <string>
#include <cstdlib>

static void print_usage() {
    std::cerr << "Usage: decryptor --ecdsa-key <private.pem> --peer-pub <public.pem> "
                 "--aes-key <hex>\n";
    std::cerr << "\nOr set environment: RELAY_ECDSA_PRIVATE_KEY, "
                 "RELAY_PEER_ECDSA_PUBLIC, RELAY_AES_KEY\n";
    std::cerr << "\nReads encrypted envelope JSON from stdin, "
                 "writes decrypted plaintext to stdout.\n";
    std::cerr << "Exit: 0 = success, 1 = failure (timestamp/replay/signature/decrypt)\n";
}

int main(int argc, char* argv[]) {
    std::string ecdsa_key, peer_pub, aes_key;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--ecdsa-key" && i + 1 < argc) {
            ecdsa_key = argv[++i];
        } else if (arg == "--peer-pub" && i + 1 < argc) {
            peer_pub = argv[++i];
        } else if (arg == "--aes-key" && i + 1 < argc) {
            aes_key = argv[++i];
        } else if (arg == "--help" || arg == "-h") {
            print_usage();
            return 0;
        }
    }

    if (ecdsa_key.empty()) {
        if (const char* env = std::getenv("RELAY_ECDSA_PRIVATE_KEY")) ecdsa_key = env;
    }
    if (peer_pub.empty()) {
        if (const char* env = std::getenv("RELAY_PEER_ECDSA_PUBLIC")) peer_pub = env;
    }
    if (aes_key.empty()) {
        if (const char* env = std::getenv("RELAY_AES_KEY")) aes_key = env;
    }

    if (ecdsa_key.empty() || peer_pub.empty() || aes_key.empty()) {
        std::cerr << "Error: Missing required parameters.\n\n";
        print_usage();
        return 1;
    }

    try {
        // Build the codec
        auto cipher = std::make_unique<jd_relay::crypto::AesGcmCipher>(aes_key);
        auto signer = std::make_unique<jd_relay::crypto::EcdsaSigner>(ecdsa_key);
        auto verifier = std::make_unique<jd_relay::crypto::EcdsaSigner>(
            jd_relay::crypto::EcdsaSigner::from_public_key_pem(peer_pub));
        auto guard = std::make_unique<jd_relay::crypto::ReplayGuard>();

        jd_relay::crypto::CryptoCodec codec(
            std::move(cipher), std::move(signer), std::move(verifier), std::move(guard));

        // Read envelope JSON from stdin
        std::stringstream ss;
        ss << std::cin.rdbuf();
        std::string json_str = ss.str();

        // Parse envelope
        auto envelope = jd_relay::crypto::CryptoCodec::from_json(json_str);

        // Decrypt
        auto result = codec.decrypt(envelope);

        if (!result.ok) {
            std::cerr << "Decrypt failed: " << result.error << std::endl;
            return 1;
        }

        // Output plaintext to stdout
        std::cout.write(reinterpret_cast<const char*>(result.plaintext.data()),
                        result.plaintext.size());
        std::cout.flush();

    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << std::endl;
        return 1;
    }

    return 0;
}
