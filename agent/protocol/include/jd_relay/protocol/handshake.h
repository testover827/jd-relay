#pragma once

/// @file handshake.h
/// Plaintext handshake protocol for the Forwarder ↔ Agent WebSocket channel.
///
/// Flow:
///   1. Agent connects to Forwarder WebSocket (wss://forwarder/agent-ws)
///   2. Agent → Forwarder:  HandshakeInit  (plaintext JSON, signed by Agent ECDSA)
///   3. Forwarder verifies signature, generates ECDH keypair
///   4. Forwarder → Agent:  HandshakeAck   (plaintext JSON, signed by Forwarder ECDSA)
///   5. Both derive AES-256 key from ECDH shared secret
///   6. All subsequent messages are CryptoEnvelope JSON (encrypted)

#include <string>
#include <vector>
#include <nlohmann/json.hpp>

namespace jd_relay::protocol {

/// Agent → Forwarder: identity + ECDH/ECDSA public keys + signature.
struct HandshakeInit {
    std::string agent_id;               ///< Unique agent identifier
    std::vector<std::string> projects;  ///< Projects this agent handles
    std::string ecdh_pub_pem;           ///< Agent's ephemeral ECDH P-256 public key (PEM)
    std::string ecdsa_pub_pem;          ///< Agent's ECDSA P-256 public key (PEM, persistent)
    std::string signature_b64;          ///< base64(ECDSA(agent_id|ecdh_pub_pem|ecdsa_pub_pem))

    /// Canonical signing payload: "agent_id|ecdh_pub_pem|ecdsa_pub_pem"
    std::string signing_data() const {
        return agent_id + "|" + ecdh_pub_pem + "|" + ecdsa_pub_pem;
    }

    std::string to_json() const {
        nlohmann::json j;
        j["type"]         = "HANDSHAKE_INIT";
        j["agent_id"]     = agent_id;
        j["projects"]     = projects;
        j["ecdh_pub_pem"] = ecdh_pub_pem;
        j["ecdsa_pub_pem"] = ecdsa_pub_pem;
        j["signature"]    = signature_b64;
        return j.dump();
    }

    static HandshakeInit from_json(const std::string& json_str) {
        auto j = nlohmann::json::parse(json_str);
        HandshakeInit init;
        init.agent_id      = j.at("agent_id").get<std::string>();
        init.projects      = j.at("projects").get<std::vector<std::string>>();
        init.ecdh_pub_pem  = j.at("ecdh_pub_pem").get<std::string>();
        init.ecdsa_pub_pem = j.at("ecdsa_pub_pem").get<std::string>();
        init.signature_b64 = j.at("signature").get<std::string>();
        return init;
    }
};

/// Forwarder → Agent: status + ECDH/ECDSA public keys + signature.
struct HandshakeAck {
    std::string status;         ///< "OK" or "ERROR"
    std::string error;          ///< Error message if status == "ERROR"
    std::string ecdh_pub_pem;   ///< Forwarder's ephemeral ECDH P-256 public key (PEM)
    std::string ecdsa_pub_pem;  ///< Forwarder's ECDSA P-256 public key (PEM, persistent)
    std::string signature_b64;  ///< base64(ECDSA(ecdh_pub_pem|ecdsa_pub_pem))

    /// Canonical signing payload: "ecdh_pub_pem|ecdsa_pub_pem"
    std::string signing_data() const {
        return ecdh_pub_pem + "|" + ecdsa_pub_pem;
    }

    std::string to_json() const {
        nlohmann::json j;
        j["type"]         = "HANDSHAKE_ACK";
        j["status"]       = status;
        if (!error.empty()) j["error"] = error;
        j["ecdh_pub_pem"] = ecdh_pub_pem;
        j["ecdsa_pub_pem"] = ecdsa_pub_pem;
        j["signature"]    = signature_b64;
        return j.dump();
    }

    static HandshakeAck from_json(const std::string& json_str) {
        auto j = nlohmann::json::parse(json_str);
        HandshakeAck ack;
        ack.status        = j.at("status").get<std::string>();
        if (j.contains("error")) ack.error = j.at("error").get<std::string>();
        ack.ecdh_pub_pem  = j.at("ecdh_pub_pem").get<std::string>();
        ack.ecdsa_pub_pem = j.at("ecdsa_pub_pem").get<std::string>();
        ack.signature_b64 = j.at("signature").get<std::string>();
        return ack;
    }
};

} // namespace jd_relay::protocol
