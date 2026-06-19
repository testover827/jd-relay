// test_ecdsa.cpp — ECDSA P-256 sign/verify tests
#include <gtest/gtest.h>
#include "jd_relay/crypto/ecdsa_signer.h"
#include "jd_relay/crypto/base64.h"
#include <filesystem>
#include <fstream>

using namespace jd_relay::crypto;

namespace {
// Helper: generate a temp key pair
struct TempKeyPair {
    std::string private_pem;
    std::string public_pem;

    TempKeyPair() {
        private_pem = "/tmp/jd_test_ecdsa_" + std::to_string(reinterpret_cast<uintptr_t>(this)) + "_priv.pem";
        public_pem  = "/tmp/jd_test_ecdsa_" + std::to_string(reinterpret_cast<uintptr_t>(this)) + "_pub.pem";
        EcdsaSigner::generate_keypair(private_pem, public_pem);
    }
    ~TempKeyPair() {
        std::filesystem::remove(private_pem);
        std::filesystem::remove(public_pem);
    }
};
}

TEST(EcdsaTest, SignAndVerify) {
    TempKeyPair keys;

    EcdsaSigner signer(keys.private_pem);

    std::vector<uint8_t> data = {'m', 'e', 's', 's', 'a', 'g', 'e'};
    auto signature = signer.sign(data);

    ASSERT_FALSE(signature.empty());
    EXPECT_TRUE(signer.verify(data, signature));
}

TEST(EcdsaTest, VerifyWithPublicKeyOnly) {
    TempKeyPair keys;

    EcdsaSigner signer(keys.private_pem);
    EcdsaSigner verifier = EcdsaSigner::from_public_key_pem(keys.public_pem);

    std::vector<uint8_t> data = {'h', 'e', 'l', 'l', 'o'};
    auto signature = signer.sign(data);

    // Verifier has only public key, should still verify
    EXPECT_TRUE(verifier.verify(data, signature));
    EXPECT_FALSE(verifier.can_sign());
}

TEST(EcdsaTest, WrongSignatureFails) {
    TempKeyPair keys;

    EcdsaSigner signer(keys.private_pem);

    std::vector<uint8_t> data = {'d', 'a', 't', 'a'};
    auto signature = signer.sign(data);

    // Flip a bit in the signature
    auto bad_sig = signature;
    bad_sig[0] ^= 0xFF;

    EXPECT_FALSE(signer.verify(data, bad_sig));
}

TEST(EcdsaTest, WrongDataFails) {
    TempKeyPair keys;

    EcdsaSigner signer(keys.private_pem);

    std::vector<uint8_t> data1 = {'d', 'a', 't', 'a', '1'};
    std::vector<uint8_t> data2 = {'d', 'a', 't', 'a', '2'};

    auto sig1 = signer.sign(data1);

    EXPECT_FALSE(signer.verify(data2, sig1));
}

TEST(EcdsaTest, DifferentKeysProduceDifferentSignatures) {
    TempKeyPair keys1;
    TempKeyPair keys2;

    EcdsaSigner signer1(keys1.private_pem);
    EcdsaSigner signer2(keys2.private_pem);

    std::vector<uint8_t> data = {'s', 'a', 'm', 'e', ' ', 'd', 'a', 't', 'a'};

    auto sig1 = signer1.sign(data);
    auto sig2 = signer2.sign(data);

    // Signatures should be different (different keys)
    EXPECT_NE(sig1, sig2);

    // But both should verify with their respective public keys
    EcdsaSigner verifier1 = EcdsaSigner::from_public_key_pem(keys1.public_pem);
    EcdsaSigner verifier2 = EcdsaSigner::from_public_key_pem(keys2.public_pem);

    EXPECT_TRUE(verifier1.verify(data, sig1));
    EXPECT_TRUE(verifier2.verify(data, sig2));

    // Cross-verification should fail
    EXPECT_FALSE(verifier1.verify(data, sig2));
    EXPECT_FALSE(verifier2.verify(data, sig1));
}

TEST(EcdsaTest, SignEmptyData) {
    TempKeyPair keys;
    EcdsaSigner signer(keys.private_pem);

    std::vector<uint8_t> empty;
    auto signature = signer.sign(empty);

    ASSERT_FALSE(signature.empty());
    EXPECT_TRUE(signer.verify(empty, signature));
}

TEST(EcdsaTest, PublicKeyDerExport) {
    TempKeyPair keys;
    EcdsaSigner signer(keys.private_pem);

    auto der = signer.public_key_der();
    EXPECT_FALSE(der.empty());
    // DER-encoded P-256 public key is typically 91 bytes
    EXPECT_GT(der.size(), 80u);
}
