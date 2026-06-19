// test_envelope_roundtrip.cpp — Full CryptoEnvelope encrypt→decrypt round-trip
#include <gtest/gtest.h>
#include "jd_relay/crypto/crypto_codec.h"
#include "jd_relay/crypto/aes_gcm_cipher.h"
#include "jd_relay/crypto/ecdsa_signer.h"
#include "jd_relay/crypto/base64.h"
#include <filesystem>
#include <fstream>

using namespace jd_relay::crypto;

namespace {
struct TestKeys {
    std::string forwarder_priv;
    std::string forwarder_pub;
    std::string agent_priv;
    std::string agent_pub;
    std::string aes_hex;

    TestKeys() {
        forwarder_priv = "/tmp/jd_test_env_fwd_priv.pem";
        forwarder_pub  = "/tmp/jd_test_env_fwd_pub.pem";
        agent_priv     = "/tmp/jd_test_env_agt_priv.pem";
        agent_pub      = "/tmp/jd_test_env_agt_pub.pem";

        EcdsaSigner::generate_keypair(forwarder_priv, forwarder_pub);
        EcdsaSigner::generate_keypair(agent_priv, agent_pub);

        auto key = AesGcmCipher::generate_key();
        aes_hex = hex_encode(key);
    }
    ~TestKeys() {
        std::filesystem::remove(forwarder_priv);
        std::filesystem::remove(forwarder_pub);
        std::filesystem::remove(agent_priv);
        std::filesystem::remove(agent_pub);
    }
};

// Helper: build a CryptoCodec for sender
CryptoCodec make_sender_codec(const TestKeys& keys) {
    auto cipher = std::make_unique<AesGcmCipher>(keys.aes_hex);
    // Sender signs with its own private key
    auto signer = std::make_unique<EcdsaSigner>(keys.forwarder_priv);
    // Sender verifies with peer's (agent's) public key
    auto verifier = std::make_unique<EcdsaSigner>(
        EcdsaSigner::from_public_key_pem(keys.agent_pub));
    auto guard = std::make_unique<ReplayGuard>();
    return CryptoCodec(std::move(cipher), std::move(signer),
                       std::move(verifier), std::move(guard));
}

// Helper: build a CryptoCodec for receiver
CryptoCodec make_receiver_codec(const TestKeys& keys) {
    auto cipher = std::make_unique<AesGcmCipher>(keys.aes_hex);
    // Receiver signs with its own private key (agent's)
    auto signer = std::make_unique<EcdsaSigner>(keys.agent_priv);
    // Receiver verifies with peer's (forwarder's) public key
    auto verifier = std::make_unique<EcdsaSigner>(
        EcdsaSigner::from_public_key_pem(keys.forwarder_pub));
    auto guard = std::make_unique<ReplayGuard>();
    return CryptoCodec(std::move(cipher), std::move(signer),
                       std::move(verifier), std::move(guard));
}
}

TEST(EnvelopeRoundTripTest, BasicEncryptDecrypt) {
    TestKeys keys;
    auto sender = make_sender_codec(keys);
    auto receiver = make_receiver_codec(keys);

    std::string payload = R"({"work_order_id":"WO-001","issue":"ISS-123","project":"my-project","branch":"main","build_cmd":"make all"})";
    std::vector<uint8_t> plaintext(payload.begin(), payload.end());

    auto envelope = sender.encrypt(plaintext, MessageType::BUILD_TRIGGER);

    EXPECT_FALSE(envelope.msg_id.empty());
    EXPECT_GT(envelope.timestamp, 0);
    EXPECT_FALSE(envelope.nonce.empty());
    EXPECT_EQ(envelope.type, "BUILD_TRIGGER");
    EXPECT_FALSE(envelope.ciphertext.empty());
    EXPECT_FALSE(envelope.iv.empty());
    EXPECT_FALSE(envelope.tag.empty());
    EXPECT_FALSE(envelope.signature.empty());

    auto result = receiver.decrypt(envelope);
    ASSERT_TRUE(result.ok) << result.error;
    EXPECT_EQ(result.plaintext, plaintext);
}

TEST(EnvelopeRoundTripTest, JsonSerializeDeserialize) {
    TestKeys keys;
    auto sender = make_sender_codec(keys);
    auto receiver = make_receiver_codec(keys);

    std::vector<uint8_t> plaintext = {'J', 'S', 'O', 'N', ' ', 't', 'e', 's', 't'};

    auto envelope = sender.encrypt(plaintext, MessageType::BUILD_RESULT);

    // Serialize to JSON
    std::string json = CryptoCodec::to_json(envelope);
    EXPECT_FALSE(json.empty());
    EXPECT_NE(json.find("msg_id"), std::string::npos);
    EXPECT_NE(json.find("ciphertext"), std::string::npos);

    // Deserialize from JSON
    auto parsed = CryptoCodec::from_json(json);
    EXPECT_EQ(parsed.msg_id, envelope.msg_id);
    EXPECT_EQ(parsed.timestamp, envelope.timestamp);
    EXPECT_EQ(parsed.ciphertext, envelope.ciphertext);
    EXPECT_EQ(parsed.signature, envelope.signature);

    // Decrypt the parsed envelope
    auto result = receiver.decrypt(parsed);
    ASSERT_TRUE(result.ok) << result.error;
    EXPECT_EQ(result.plaintext, plaintext);
}

TEST(EnvelopeRoundTripTest, AllMessageTypes) {
    TestKeys keys;
    auto sender = make_sender_codec(keys);
    auto receiver = make_receiver_codec(keys);

    std::vector<uint8_t> payload = {'d', 'a', 't', 'a'};

    for (auto type : {MessageType::BUILD_TRIGGER,
                      MessageType::BUILD_RESULT,
                      MessageType::SENSITIVE_REVIEW_REQ,
                      MessageType::SECOND_REVIEW_RESULT,
                      MessageType::HEARTBEAT,
                      MessageType::ACK}) {
        // Each encryption uses a unique nonce, so no replay issue
        auto envelope = sender.encrypt(payload, type);
        auto result = receiver.decrypt(envelope);
        ASSERT_TRUE(result.ok) << "Failed for type " << to_string(type) << ": " << result.error;
        EXPECT_EQ(result.plaintext, payload);
    }
}

TEST(EnvelopeRoundTripTest, EmptyPlaintext) {
    TestKeys keys;
    auto sender = make_sender_codec(keys);
    auto receiver = make_receiver_codec(keys);

    std::vector<uint8_t> empty;
    auto envelope = sender.encrypt(empty, MessageType::HEARTBEAT);

    auto result = receiver.decrypt(envelope);
    ASSERT_TRUE(result.ok) << result.error;
    EXPECT_TRUE(result.plaintext.empty());
}

TEST(EnvelopeRoundTripTest, LargePayload) {
    TestKeys keys;
    auto sender = make_sender_codec(keys);
    auto receiver = make_receiver_codec(keys);

    // 1MB payload
    std::vector<uint8_t> payload(1024 * 1024);
    for (size_t i = 0; i < payload.size(); ++i) {
        payload[i] = static_cast<uint8_t>(i & 0xFF);
    }

    auto envelope = sender.encrypt(payload, MessageType::BUILD_TRIGGER);
    auto result = receiver.decrypt(envelope);
    ASSERT_TRUE(result.ok) << result.error;
    EXPECT_EQ(result.plaintext, payload);
}
