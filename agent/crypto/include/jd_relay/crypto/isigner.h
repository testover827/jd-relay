#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace jd_relay::crypto {

/// Abstract signer interface.
/// Current impl: ECDSA P-256. Future: SM2.
class ISigner {
public:
    virtual ~ISigner() = default;

    /// Sign a message digest.
    /// @param data  The raw data to sign (will be SHA-256 hashed internally).
    /// @return      DER-encoded signature.
    virtual std::vector<uint8_t> sign(const std::vector<uint8_t>& data) = 0;

    /// Verify a signature.
    /// @param data      The original raw data.
    /// @param signature DER-encoded signature to verify.
    /// @return          true if valid.
    virtual bool verify(const std::vector<uint8_t>& data,
                        const std::vector<uint8_t>& signature) = 0;

    /// Get the public key in uncompressed DER format.
    virtual std::vector<uint8_t> public_key_der() const = 0;

    /// Algorithm name (e.g. "ECDSA-P256").
    virtual std::string algorithm() const = 0;
};

} // namespace jd_relay::crypto
