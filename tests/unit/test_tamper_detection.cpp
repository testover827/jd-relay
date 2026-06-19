// test_tamper_detection.cpp — Verify that tampered envelopes are rejected
#include <gtest/gtest.h>
#include "jd_relay/crypto/crypto_codec.h"
#include "jd_relay/crypto/aes_gcm_cipher.h"
#include "jd_relay/crypto/ecdsa_signer.h"
#include "jd_relay/crypto/base64.h"
#include <filesystem>

using namespace jd_relay::crypto;

namespace {
struct TestKeys {
    std::string fwd_priv, fwd_pub, agt_priv, agt_pub, aes_hex;

    TestKeys() {
        fwd_priv = "/tmp/jd_test_tamper_fwd_priv.pem";
        fwd_pub  = "/tmp/jd_test_tamper_fwd_pub.pem";
        agt_priv = "/tmp/jd_test_tamper_agt_priv.pem";
        agt_pub  = "/tmp/jd_test_tamper_agt_pub.pem";

        EcdsaSigner::generate_keypair(fwd_priv, fwd_pub);
        EcdsaSigner::generate_keypair(agt_priv, agt_pub);

        auto key = AesGcmCipher::generate_key();
        aes_hex = hex_encode(key);
    }
    ~TestKeys() {
        std::filesystem::remove(fwd_priv);
        std::filesystem::remove(fwd_pub);
        std::filesystem::remove(agt_priv);
        std::filesystem::remove(agt_pub);
    }
};

CryptoCodec make_pair(const TestKeys& keys, bool sender) {
    auto cipher = std::make_unique<AesGcmCipher>(keys.aes_hex);
    std::unique_ptr<EcdsaSigner> signer;
    std::unique_ptr<EcdsaSigner> verifier;

    if (sender) {
        signer = std::make_unique<EcdsaSigner>(keys.fwd_priv);
        verifier = std::make_unique<EcdsaSigner>(
            EcdsaSigner::from_public_key_pem(keys.agt_pub));
    } else {
        signer = std::make_unique<EcdsaSigner>(keys.agt_priv);
        verifier = std::make_unique<EcdsaSigner>(
            EcdsaSigner::from_public_key_pem(keys.fwd_pub));
    }
    auto guard = std::make_unique<ReplayGuard>();
    return CryptoCodec(std::move(cipher), std::move(signer),
                       std::move(verifier), std::move(guard));
}
}

TEST(TamperDetectionTest, TamperedCiphertextRejected) {
    TestKeys keys;
    auto sender = make_pair(keys, true);
    auto receiver = make_pair(keys, false);

    std::vector<uint8_t> payload = {'s', 'e', 'c', 'r', 'e', 't'};
    auto envelope = sender.encrypt(payload, MessageType::BUILD_TRIGGER);

    // Tamper with ciphertext: flip last character of base64
    envelope.ciphertext.back() = (envelope.ciphertext.back() == 'A') ? 'B' : 'A';

    auto result = receiver.decrypt(envelope);
    EXPECT_FALSE(result.ok);
    // Should fail at signature verification (since signing payload includes ciphertext)
    EXPECT_NE(result.error.find("signature"), std::string::npos);
}

TEST(TamperDetectionTest, TamperedTagRejected) {
    TestKeys keys;
    auto sender = make_pair(keys, true);
    auto receiver = make_pair(keys, false);

    std::vector<uint8_t> payload = {'d', 'a', 't', 'a'};
    auto envelope = sender.encrypt(payload, MessageType::HEARTBEAT);

    // Tamper with tag
    envelope.tag.back() = (envelope.tag.back() == 'A') ? 'B' : 'A';

    auto result = receiver.decrypt(envelope);
    EXPECT_FALSE(result.ok);
}

TEST(TamperDetectionTest, TamperedSignatureRejected) {
    TestKeys keys;
    auto sender = make_pair(keys, true);
    auto receiver = make_pair(keys, false);

    std::vector<uint8_t> payload = {'m', 's', 'g'};
    auto envelope = sender.encrypt(payload, MessageType::ACK);

    // Tamper with signature
    envelope.signature.back() = (envelope.signature.back() == 'A') ? 'B' : 'A';

    auto result = receiver.decrypt(envelope);
    EXPECT_FALSE(result.ok);
    EXPECT_NE(result.error.find("signature"), std::string::npos);
}

TEST(TamperDetectionTest, ExpiredTimestampRejected) {
    TestKeys keys;
    auto sender = make_pair(keys, true);
    auto receiver = make_pair(keys, false);

    std::vector<uint8_t> payload = {'x'};
    auto envelope = sender.encrypt(payload, MessageType::HEARTBEAT);

    // Set timestamp to 10 minutes ago
    envelope.timestamp = ReplayGuard::now_ms() - 600 * 1000;

    auto result = receiver.decrypt(envelope);
    EXPECT_FALSE(result.ok);
    EXPECT_NE(result.error.find("imestamp"), std::string::npos);
}

TEST(TamperDetectionTest, ReplayNonceRejected) {
    TestKeys keys;
    auto sender = make_pair(keys, true);
    auto receiver = make_pair(keys, false);

    std::vector<uint8_t> payload = {'r', 'e', 'p', 'l', 'a', 'y'};
    auto envelope = sender.encrypt(payload, MessageType::BUILD_RESULT);

    // First decrypt: should succeed
    auto result1 = receiver.decrypt(envelope);
    EXPECT_TRUE(result1.ok);

    // Second decrypt of same envelope: should fail (replay)
    auto result2 = receiver.decrypt(envelope);
    EXPECT_FALSE(result2.ok);
    EXPECT_NE(result2.error.find("Replay"), std::string::npos);
}

TEST(TamperDetectionTest, WrongSignerKeyRejected) {
    TestKeys keys;

    // Create a third party with its own keys
    std::string attacker_priv = "/tmp/jd_test_tamper_atk_priv.pem";
    std::string attacker_pub  = "/tmp/jd_test_tamper_atk_pub.pem";
    EcdsaSigner::generate_keypair(attacker_priv, attacker_pub);

    // Attacker signs with their own key, but receiver expects forwarder's key
    auto cipher = std::make_unique<AesGcmCipher>(keys.aes_hex);
    auto attacker_signer = std::make_unique<EcdsaSigner>(attacker_priv);
    auto verifier = std::make_unique<EcdsaSigner>(
        EcdsaSigner::from_public_key_pem(keys.fwd_pub));
    auto guard = std::make_unique<ReplayGuard>();

    CryptoCodec attacker_codec(std::move(cipher), std::move(attacker_signer),
                                std::move(verifier), std::move(guard));

    std::vector<uint8_t> payload = {'f', 'o', 'r', 'g', 'e', 'd'};
    auto envelope = attacker_codec.encrypt(payload, MessageType::BUILD_TRIGGER);

    // Receiver should reject — signature won't match forwarder's public key
    auto receiver = make_pair(keys, false);
    auto result = receiver.decrypt(envelope);
    EXPECT_FALSE(result.ok);
    EXPECT_NE(result.error.find("signature"), std::string::npos);

    std::filesystem::remove(attacker_priv);
    std::filesystem::remove(attacker_pub);
}
