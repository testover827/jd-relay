#pragma once

#include "jd_relay/crypto/envelope.h"
#include "jd_relay/crypto/icipher.h"
#include "jd_relay/crypto/isigner.h"
#include "jd_relay/crypto/replay_guard.h"
#include <memory>
#include <string>

namespace jd_relay::crypto {

/// High-level codec that ties together cipher + signer + replay guard.
/// Provides one-call encrypt/decrypt producing/consuming CryptoEnvelope.
class CryptoCodec {
public:
    /// @param cipher   Owned cipher (AES-256-GCM).
    /// @param signer   Owned signer for outgoing messages (ECDSA with our private key).
    /// @param verifier Owned verifier for incoming messages (ECDSA with peer's public key).
    /// @param guard    Owned replay guard.
    CryptoCodec(std::unique_ptr<ICipher> cipher,
                std::unique_ptr<ISigner> signer,
                std::unique_ptr<ISigner> verifier,
                std::unique_ptr<ReplayGuard> guard);

    ~CryptoCodec();

    CryptoCodec(const CryptoCodec&) = delete;
    CryptoCodec& operator=(const CryptoCodec&) = delete;
    CryptoCodec(CryptoCodec&&) noexcept;
    CryptoCodec& operator=(CryptoCodec&&) noexcept;

    /// Encrypt a plaintext payload into a CryptoEnvelope.
    /// @param plaintext  Raw bytes to encrypt (typically JSON).
    /// @param type       Message type.
    /// @return           Ready-to-serialize CryptoEnvelope.
    CryptoEnvelope encrypt(const std::vector<uint8_t>& plaintext,
                           MessageType type);

    /// Decrypt a CryptoEnvelope, with full validation.
    /// Order: timestamp window → nonce replay → ECDSA verify → AES-GCM decrypt.
    /// @param env  The received envelope.
    /// @return     Plaintext bytes. On failure, result.ok == false with error message.
    DecryptResult decrypt(const CryptoEnvelope& env);

    /// Serialize envelope to JSON string.
    static std::string to_json(const CryptoEnvelope& env);

    /// Parse JSON string to envelope.
    static CryptoEnvelope from_json(const std::string& json_str);

private:
    std::unique_ptr<ICipher>     cipher_;
    std::unique_ptr<ISigner>     signer_;
    std::unique_ptr<ISigner>     verifier_;
    std::unique_ptr<ReplayGuard> guard_;
};

} // namespace jd_relay::crypto
