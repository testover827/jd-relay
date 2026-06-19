#pragma once

#include <memory>
#include <string>
#include <vector>

namespace jd_relay::crypto {

/// Manages cryptographic keys: AES session key + ECDSA signing key pair.
/// Keys are loaded from files or environment variables — never hardcoded.
class KeyManager {
public:
    /// Load keys from configuration.
    /// @param ecdsa_private_pem  Path to ECDSA private key PEM file.
    /// @param peer_ecdsa_public_pem  Path to peer's ECDSA public key PEM file.
    /// @param aes_key_hex  AES-256 key as hex string (64 chars). If empty, read from env RELAY_AES_KEY.
    /// @param key_version  Key version for rotation.
    KeyManager(const std::string& ecdsa_private_pem,
               const std::string& peer_ecdsa_public_pem,
               const std::string& aes_key_hex = "",
               int key_version = 1);

    ~KeyManager();

    // Non-copyable, movable
    KeyManager(const KeyManager&) = delete;
    KeyManager& operator=(const KeyManager&) = delete;
    KeyManager(KeyManager&&) noexcept;
    KeyManager& operator=(KeyManager&&) noexcept;

    /// Get the AES-256 session key (32 bytes).
    const std::vector<uint8_t>& aes_key() const;

    /// Get the AES key as hex string.
    std::string aes_key_hex() const;

    /// Get the ECDSA signer (for signing outgoing messages).
    class EcdsaSigner* signer();
    /// Get the ECDSA verifier (for verifying incoming messages from peer).
    class EcdsaSigner* verifier();

    /// Get the key version.
    int key_version() const { return key_version_; }

    /// Derive AES session key from ECDH shared secret.
    /// Useful when using ECDH key exchange instead of pre-shared key.
    void derive_session_key(class EcdhKeyExchange& ecdh,
                            const std::vector<uint8_t>& peer_pub_der);

    /// Generate all keys for a new deployment.
    /// Creates: ecdsa_private.pem, ecdsa_public.pem, and prints AES key to stdout.
    static void generate_all_keys(const std::string& output_dir);

private:
    struct Impl;
    std::unique_ptr<Impl> pimpl_;
    int key_version_;
};

} // namespace jd_relay::crypto
