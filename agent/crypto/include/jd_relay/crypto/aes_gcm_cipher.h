#pragma once

#include "jd_relay/crypto/icipher.h"
#include <cstdint>
#include <string>
#include <vector>

namespace jd_relay::crypto {

/// AES-256-GCM cipher using OpenSSL EVP.
/// Implements ICipher interface. Can be replaced by SM4-GCM in future.
class AesGcmCipher : public ICipher {
public:
    /// Construct with a 32-byte key.
    explicit AesGcmCipher(const std::vector<uint8_t>& key);

    /// Construct from a hex-encoded key string (64 hex chars = 32 bytes).
    explicit AesGcmCipher(const std::string& hex_key);

    ~AesGcmCipher() override;

    // Non-copyable, movable
    AesGcmCipher(const AesGcmCipher&) = delete;
    AesGcmCipher& operator=(const AesGcmCipher&) = delete;
    AesGcmCipher(AesGcmCipher&&) noexcept;
    AesGcmCipher& operator=(AesGcmCipher&&) noexcept;

    EncryptResult encrypt(const std::vector<uint8_t>& plaintext,
                          const std::vector<uint8_t>& iv = {}) override;

    DecryptResult decrypt(const std::vector<uint8_t>& ciphertext,
                          const std::vector<uint8_t>& iv,
                          const std::vector<uint8_t>& tag) override;

    size_t key_size() const override { return 32; }
    size_t iv_size() const override { return 12; }
    size_t tag_size() const override { return 16; }

    /// Generate a random 32-byte key.
    static std::vector<uint8_t> generate_key();

private:
    std::vector<uint8_t> key_;
};

} // namespace jd_relay::crypto
