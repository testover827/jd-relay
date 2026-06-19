// test_ecdh.cpp — ECDH P-256 key agreement tests
#include <gtest/gtest.h>
#include "jd_relay/crypto/ecdh_key_exchange.h"
#include "jd_relay/crypto/base64.h"
#include <filesystem>
#include <fstream>

using namespace jd_relay::crypto;

TEST(EcdhTest, BothSidesDeriveSameSecret) {
    // Alice and Bob each generate a key pair
    EcdhKeyExchange alice;
    EcdhKeyExchange bob;

    // Exchange public keys (in DER format)
    auto alice_pub = alice.public_key_der();
    auto bob_pub   = bob.public_key_der();

    // Each derives the shared secret using the other's public key
    auto alice_secret = alice.derive_shared_secret(bob_pub);
    auto bob_secret   = bob.derive_shared_secret(alice_pub);

    // Both should derive the same 32-byte AES key
    ASSERT_EQ(alice_secret.size(), 32u);
    ASSERT_EQ(bob_secret.size(), 32u);
    EXPECT_EQ(alice_secret, bob_secret);
}

TEST(EcdhTest, DifferentPairsProduceDifferentSecrets) {
    EcdhKeyExchange alice1;
    EcdhKeyExchange bob;

    auto secret1 = alice1.derive_shared_secret(bob.public_key_der());

    EcdhKeyExchange alice2;
    auto secret2 = alice2.derive_shared_secret(bob.public_key_der());

    EXPECT_NE(secret1, secret2);
}

TEST(EcdhTest, PemKeyExchange) {
    // Generate key pairs to PEM files
    EcdhKeyExchange::generate_keypair("/tmp/jd_test_ecdh_a_priv.pem",
                                       "/tmp/jd_test_ecdh_a_pub.pem");
    EcdhKeyExchange::generate_keypair("/tmp/jd_test_ecdh_b_priv.pem",
                                       "/tmp/jd_test_ecdh_b_pub.pem");

    // Load from PEM
    auto alice = EcdhKeyExchange::from_private_key_pem("/tmp/jd_test_ecdh_a_priv.pem");
    auto bob   = EcdhKeyExchange::from_private_key_pem("/tmp/jd_test_ecdh_b_priv.pem");

    // Read public PEM files
    std::ifstream pub_file_a("/tmp/jd_test_ecdh_a_pub.pem");
    std::ifstream pub_file_b("/tmp/jd_test_ecdh_b_pub.pem");
    std::string pub_pem_a((std::istreambuf_iterator<char>(pub_file_a)),
                           std::istreambuf_iterator<char>());
    std::string pub_pem_b((std::istreambuf_iterator<char>(pub_file_b)),
                           std::istreambuf_iterator<char>());

    auto secret_a = alice.derive_shared_secret_pem(pub_pem_b);
    auto secret_b = bob.derive_shared_secret_pem(pub_pem_a);

    EXPECT_EQ(secret_a, secret_b);
    EXPECT_EQ(secret_a.size(), 32u);

    // Cleanup
    std::filesystem::remove("/tmp/jd_test_ecdh_a_priv.pem");
    std::filesystem::remove("/tmp/jd_test_ecdh_a_pub.pem");
    std::filesystem::remove("/tmp/jd_test_ecdh_b_priv.pem");
    std::filesystem::remove("/tmp/jd_test_ecdh_b_pub.pem");
}

TEST(EcdhTest, PublicKeyExportNotEmpty) {
    EcdhKeyExchange ecdh;

    auto der = ecdh.public_key_der();
    auto pem = ecdh.public_key_pem();

    EXPECT_FALSE(der.empty());
    EXPECT_FALSE(pem.empty());
    EXPECT_NE(pem.find("BEGIN PUBLIC KEY"), std::string::npos);
}
