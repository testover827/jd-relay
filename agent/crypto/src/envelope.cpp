#include "jd_relay/crypto/envelope.h"
#include <stdexcept>

namespace jd_relay::crypto {

const char* to_string(MessageType t) {
    switch (t) {
        case MessageType::BUILD_TRIGGER:        return "BUILD_TRIGGER";
        case MessageType::BUILD_RESULT:         return "BUILD_RESULT";
        case MessageType::SENSITIVE_REVIEW_REQ: return "SENSITIVE_REVIEW_REQ";
        case MessageType::SECOND_REVIEW_RESULT: return "SECOND_REVIEW_RESULT";
        case MessageType::HEARTBEAT:            return "HEARTBEAT";
        case MessageType::ACK:                  return "ACK";
    }
    return "UNKNOWN";
}

MessageType parse_message_type(const std::string& s) {
    if (s == "BUILD_TRIGGER")        return MessageType::BUILD_TRIGGER;
    if (s == "BUILD_RESULT")         return MessageType::BUILD_RESULT;
    if (s == "SENSITIVE_REVIEW_REQ") return MessageType::SENSITIVE_REVIEW_REQ;
    if (s == "SECOND_REVIEW_RESULT") return MessageType::SECOND_REVIEW_RESULT;
    if (s == "HEARTBEAT")            return MessageType::HEARTBEAT;
    if (s == "ACK")                  return MessageType::ACK;
    throw std::invalid_argument("Unknown message type: " + s);
}

std::string build_signing_payload(const CryptoEnvelope& env) {
    // Format: msg_id|timestamp|nonce|type|iv|ciphertext|tag
    return env.msg_id + "|"
         + std::to_string(env.timestamp) + "|"
         + env.nonce + "|"
         + env.type + "|"
         + env.iv + "|"
         + env.ciphertext + "|"
         + env.tag;
}

} // namespace jd_relay::crypto
