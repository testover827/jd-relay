#pragma once

#include "jd_relay/crypto/isigner.h"
#include <memory>

namespace jd_relay::crypto {

/// ECDSA P-256 signer using OpenSSL EVP.
/// Implements ISigner interface. Can be replaced by SM2 in future.
class EcdsaSigner : public ISigner {
public:
    /// Default constructor (verify-only, load public key later).
    EcdsaSigner();

    /// Construct from a PEM-encoded private key file (can sign and verify).
    explicit EcdsaSigner(const std::string& key_pem_file);

    /// Construct from in-memory PEM private key data.
    explicit EcdsaSigner(const std::vector<uint8_t>& pem_data);

    /// Construct for verification-only from a PEM-encoded public key file.
    static EcdsaSigner from_public_key_pem(const std::string& key_pem_file);

    /// Construct for verification-only from in-memory PEM public key data.
    static EcdsaSigner from_public_key_data(const std::vector<uint8_t>& pem_data);

    ~EcdsaSigner() override;

    // Non-copyable, movable
    EcdsaSigner(const EcdsaSigner&) = delete;
    EcdsaSigner& operator=(const EcdsaSigner&) = delete;
    EcdsaSigner(EcdsaSigner&&) noexcept;
    EcdsaSigner& operator=(EcdsaSigner&&) noexcept;

    std::vector<uint8_t> sign(const std::vector<uint8_t>& data) override;
    bool verify(const std::vector<uint8_t>& data,
                const std::vector<uint8_t>& signature) override;

    std::vector<uint8_t> public_key_der() const override;
    std::string algorithm() const override { return "ECDSA-P256"; }

    /// True if this instance can sign (has a private key).
    bool can_sign() const;

    /// Generate a new P-256 key pair and write to files.
    static void generate_keypair(const std::string& private_pem_file,
                                 const std::string& public_pem_file);

private:
    struct Impl;
    std::unique_ptr<Impl> pimpl_;

    void load_private_key(const std::vector<uint8_t>& pem_data);
    void load_public_key(const std::vector<uint8_t>& pem_data);
};

} // namespace jd_relay::crypto
