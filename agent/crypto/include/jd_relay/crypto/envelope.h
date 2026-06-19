#pragma once

#include <cstdint>
#include <string>

namespace jd_relay::crypto {

/// Message types for the Forwarder ↔ Agent encrypted channel.
enum class MessageType : uint8_t {
    BUILD_TRIGGER        = 0,
    BUILD_RESULT         = 1,
    SENSITIVE_REVIEW_REQ = 2,
    SECOND_REVIEW_RESULT = 3,
    HEARTBEAT            = 4,
    ACK                  = 5,
};

/// Convert MessageType to string for JSON serialization.
const char* to_string(MessageType t);

/// Parse MessageType from string.
MessageType parse_message_type(const std::string& s);

/// The encrypted envelope structure.
/// Wire format is JSON with base64-encoded binary fields.
struct CryptoEnvelope {
    std::string msg_id;       ///< UUID v4
    int64_t     timestamp;    ///< Unix epoch milliseconds
    std::string nonce;        ///< base64(16 random bytes)
    std::string type;         ///< MessageType string
    std::string iv;           ///< base64(12 bytes, AES-GCM nonce)
    std::string ciphertext;   ///< base64(AES-256-GCM(plaintext))
    std::string tag;          ///< base64(16 bytes, GCM auth tag)
    std::string signature;    ///< base64(ECDSA(sha256(msg_id|timestamp|nonce|type|iv|ciphertext|tag)))
};

/// Build the canonical signing payload.
/// Format: msg_id|timestamp|nonce|type|iv|ciphertext|tag
std::string build_signing_payload(const CryptoEnvelope& env);

} // namespace jd_relay::crypto
