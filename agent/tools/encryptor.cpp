// encryptor.cpp — Read plaintext JSON from stdin, output encrypted envelope to stdout
//
// Usage:
//   encryptor --ecdsa-key <private.pem> --peer-pub <public.pem> --aes-key <hex> [--type <TYPE>]
//
// Or via environment variables:
//   RELAY_ECDSA_PRIVATE_KEY, RELAY_PEER_ECDSA_PUBLIC, RELAY_AES_KEY
//
// Reads plaintext from stdin, writes encrypted envelope JSON to stdout.

#include "jd_relay/crypto/crypto_codec.h"
#include "jd_relay/crypto/aes_gcm_cipher.h"
#include "jd_relay/crypto/ecdsa_signer.h"
#include "jd_relay/crypto/key_manager.h"
#include <iostream>
#include <sstream>
#include <string>
#include <cstdlib>
#include <cstring>

static void print_usage() {
    std::cerr << "Usage: encryptor --ecdsa-key <private.pem> --peer-pub <public.pem> "
                 "--aes-key <hex> [--type <TYPE>]\n";
    std::cerr << "\nOr set environment: RELAY_ECDSA_PRIVATE_KEY, "
                 "RELAY_PEER_ECDSA_PUBLIC, RELAY_AES_KEY\n";
    std::cerr << "\nTypes: BUILD_TRIGGER, BUILD_RESULT, SENSITIVE_REVIEW_REQ, "
                 "SECOND_REVIEW_RESULT, HEARTBEAT, ACK (default: HEARTBEAT)\n";
    std::cerr << "\nReads plaintext from stdin, writes encrypted envelope JSON to stdout.\n";
}

int main(int argc, char* argv[]) {
    std::string ecdsa_key, peer_pub, aes_key, type_str = "HEARTBEAT";

    // Parse arguments
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--ecdsa-key" && i + 1 < argc) {
            ecdsa_key = argv[++i];
        } else if (arg == "--peer-pub" && i + 1 < argc) {
            peer_pub = argv[++i];
        } else if (arg == "--aes-key" && i + 1 < argc) {
            aes_key = argv[++i];
        } else if (arg == "--type" && i + 1 < argc) {
            type_str = argv[++i];
        } else if (arg == "--help" || arg == "-h") {
            print_usage();
            return 0;
        }
    }

    // Fall back to environment variables
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
        auto km = jd_relay::crypto::KeyManager(ecdsa_key, peer_pub, aes_key);

        auto cipher = std::make_unique<jd_relay::crypto::AesGcmCipher>(km.aes_key());
        auto signer_raw = km.signer();
        auto verifier_raw = km.verifier();

        // We need to transfer ownership... but KeyManager owns the signers.
        // For the standalone tool, we'll create the codec with the signers
        // by temporarily extracting them. Actually, CryptoCodec takes ownership
        // via unique_ptr. Since KeyManager owns them, we need a different approach.

        // Let's just create the signers directly for the standalone tool.
        auto signer = std::make_unique<jd_relay::crypto::EcdsaSigner>(ecdsa_key);
        auto verifier = std::make_unique<jd_relay::crypto::EcdsaSigner>(
            jd_relay::crypto::EcdsaSigner::from_public_key_pem(peer_pub));
        auto guard = std::make_unique<jd_relay::crypto::ReplayGuard>();

        jd_relay::crypto::CryptoCodec codec(
            std::move(cipher), std::move(signer), std::move(verifier), std::move(guard));

        // Read plaintext from stdin
        std::stringstream ss;
        ss << std::cin.rdbuf();
        std::string plaintext_str = ss.str();

        std::vector<uint8_t> plaintext(plaintext_str.begin(), plaintext_str.end());

        // Determine message type
        auto msg_type = jd_relay::crypto::parse_message_type(type_str);

        // Encrypt
        auto envelope = codec.encrypt(plaintext, msg_type);

        // Output JSON to stdout
        std::cout << jd_relay::crypto::CryptoCodec::to_json(envelope) << std::endl;

    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << std::endl;
        return 1;
    }

    return 0;
}
