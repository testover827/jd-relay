#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace jd_relay::crypto {

/// ECDH P-256 key exchange using OpenSSL.
/// Used during WebSocket handshake to derive a shared AES-256 session key.
class EcdhKeyExchange {
public:
    EcdhKeyExchange();
    ~EcdhKeyExchange();

    // Non-copyable, movable
    EcdhKeyExchange(const EcdhKeyExchange&) = delete;
    EcdhKeyExchange& operator=(const EcdhKeyExchange&) = delete;
    EcdhKeyExchange(EcdhKeyExchange&&) noexcept;
    EcdhKeyExchange& operator=(EcdhKeyExchange&&) noexcept;

    /// Get our public key in uncompressed DER format (for sending to peer).
    std::vector<uint8_t> public_key_der() const;

    /// Get our public key in PEM format.
    std::string public_key_pem() const;

    /// Derive a shared secret from peer's public key (DER uncompressed).
    /// Returns 32 bytes suitable for AES-256 key.
    std::vector<uint8_t> derive_shared_secret(const std::vector<uint8_t>& peer_pub_der);

    /// Convenience: derive shared secret from peer's PEM public key.
    std::vector<uint8_t> derive_shared_secret_pem(const std::string& peer_pub_pem);

    /// Generate a key pair and save to PEM files.
    static void generate_keypair(const std::string& private_pem_file,
                                 const std::string& public_pem_file);

    /// Load from existing PEM private key file.
    static EcdhKeyExchange from_private_key_pem(const std::string& pem_file);

private:
    struct Impl;
    std::unique_ptr<Impl> pimpl_;
};

} // namespace jd_relay::crypto
