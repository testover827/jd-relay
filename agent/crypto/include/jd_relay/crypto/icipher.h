#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace jd_relay::crypto {

/// Result of an encrypt operation.
struct EncryptResult {
    std::vector<uint8_t> ciphertext;
    std::vector<uint8_t> iv;   ///< 12-byte nonce for AES-GCM
    std::vector<uint8_t> tag;  ///< 16-byte GCM authentication tag
    bool ok{false};
    std::string error;
};

/// Result of a decrypt operation.
struct DecryptResult {
    std::vector<uint8_t> plaintext;
    bool ok{false};
    std::string error;
};

/// Abstract cipher interface.
/// Current impl: AES-256-GCM. Future: SM4-GCM.
class ICipher {
public:
    virtual ~ICipher() = default;

    /// Encrypt plaintext, producing ciphertext + iv + tag.
    /// @param plaintext  Data to encrypt.
    /// @param iv         Caller-supplied 12-byte IV (or empty to auto-generate).
    virtual EncryptResult encrypt(const std::vector<uint8_t>& plaintext,
                                  const std::vector<uint8_t>& iv = {}) = 0;

    /// Decrypt ciphertext using iv + tag for authentication.
    virtual DecryptResult decrypt(const std::vector<uint8_t>& ciphertext,
                                  const std::vector<uint8_t>& iv,
                                  const std::vector<uint8_t>& tag) = 0;

    /// Key size in bytes (32 for AES-256).
    virtual size_t key_size() const = 0;

    /// IV size in bytes (12 for AES-GCM).
    virtual size_t iv_size() const = 0;

    /// Tag size in bytes (16 for AES-GCM).
    virtual size_t tag_size() const = 0;
};

} // namespace jd_relay::crypto
