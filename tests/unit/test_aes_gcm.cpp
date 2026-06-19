// test_aes_gcm.cpp — AES-256-GCM encrypt/decrypt tests
#include <gtest/gtest.h>
#include "jd_relay/crypto/aes_gcm_cipher.h"
#include "jd_relay/crypto/base64.h"

using namespace jd_relay::crypto;

TEST(AesGcmTest, EncryptDecryptRoundTrip) {
    auto key = AesGcmCipher::generate_key();
    AesGcmCipher cipher(key);

    std::vector<uint8_t> plaintext = {'H', 'e', 'l', 'l', 'o', ' ', 'W', 'o', 'r', 'l', 'd'};

    auto enc = cipher.encrypt(plaintext);
    ASSERT_TRUE(enc.ok) << enc.error;
    EXPECT_EQ(enc.iv.size(), 12u);
    EXPECT_EQ(enc.tag.size(), 16u);
    EXPECT_EQ(enc.ciphertext.size(), plaintext.size());

    auto dec = cipher.decrypt(enc.ciphertext, enc.iv, enc.tag);
    ASSERT_TRUE(dec.ok) << dec.error;
    EXPECT_EQ(dec.plaintext, plaintext);
}

TEST(AesGcmTest, EncryptDecryptEmptyPlaintext) {
    auto key = AesGcmCipher::generate_key();
    AesGcmCipher cipher(key);

    std::vector<uint8_t> plaintext;

    auto enc = cipher.encrypt(plaintext);
    ASSERT_TRUE(enc.ok) << enc.error;

    auto dec = cipher.decrypt(enc.ciphertext, enc.iv, enc.tag);
    ASSERT_TRUE(dec.ok) << dec.error;
    EXPECT_TRUE(dec.plaintext.empty());
}

TEST(AesGcmTest, EncryptDecryptLargePayload) {
    auto key = AesGcmCipher::generate_key();
    AesGcmCipher cipher(key);

    // 64KB payload
    std::vector<uint8_t> plaintext(65536);
    for (size_t i = 0; i < plaintext.size(); ++i) {
        plaintext[i] = static_cast<uint8_t>(i & 0xFF);
    }

    auto enc = cipher.encrypt(plaintext);
    ASSERT_TRUE(enc.ok) << enc.error;

    auto dec = cipher.decrypt(enc.ciphertext, enc.iv, enc.tag);
    ASSERT_TRUE(dec.ok) << dec.error;
    EXPECT_EQ(dec.plaintext, plaintext);
}

TEST(AesGcmTest, WrongKeyFails) {
    auto key1 = AesGcmCipher::generate_key();
    auto key2 = AesGcmCipher::generate_key();
    AesGcmCipher cipher1(key1);
    AesGcmCipher cipher2(key2);

    std::vector<uint8_t> plaintext = {'s', 'e', 'c', 'r', 'e', 't'};

    auto enc = cipher1.encrypt(plaintext);
    ASSERT_TRUE(enc.ok);

    // Decrypt with wrong key should fail (tag verification)
    auto dec = cipher2.decrypt(enc.ciphertext, enc.iv, enc.tag);
    EXPECT_FALSE(dec.ok);
}

TEST(AesGcmTest, TamperedCiphertextFails) {
    auto key = AesGcmCipher::generate_key();
    AesGcmCipher cipher(key);

    std::vector<uint8_t> plaintext = {'d', 'a', 't', 'a'};

    auto enc = cipher.encrypt(plaintext);
    ASSERT_TRUE(enc.ok);

    // Tamper with ciphertext
    auto tampered = enc.ciphertext;
    tampered[0] ^= 0xFF;

    auto dec = cipher.decrypt(tampered, enc.iv, enc.tag);
    EXPECT_FALSE(dec.ok);
}

TEST(AesGcmTest, TamperedTagFails) {
    auto key = AesGcmCipher::generate_key();
    AesGcmCipher cipher(key);

    std::vector<uint8_t> plaintext = {'d', 'a', 't', 'a'};

    auto enc = cipher.encrypt(plaintext);
    ASSERT_TRUE(enc.ok);

    // Tamper with tag
    auto tampered_tag = enc.tag;
    tampered_tag[0] ^= 0xFF;

    auto dec = cipher.decrypt(enc.ciphertext, enc.iv, tampered_tag);
    EXPECT_FALSE(dec.ok);
}

TEST(AesGcmTest, TamperedIVFails) {
    auto key = AesGcmCipher::generate_key();
    AesGcmCipher cipher(key);

    std::vector<uint8_t> plaintext = {'d', 'a', 't', 'a'};

    auto enc = cipher.encrypt(plaintext);
    ASSERT_TRUE(enc.ok);

    // Tamper with IV
    auto tampered_iv = enc.iv;
    tampered_iv[0] ^= 0xFF;

    auto dec = cipher.decrypt(enc.ciphertext, tampered_iv, enc.tag);
    EXPECT_FALSE(dec.ok);
}

TEST(AesGcmTest, ConstructFromHexKey) {
    auto key = AesGcmCipher::generate_key();
    std::string hex = hex_encode(key);

    AesGcmCipher cipher(hex);
    EXPECT_EQ(cipher.key_size(), 32u);

    std::vector<uint8_t> plaintext = {'t', 'e', 's', 't'};
    auto enc = cipher.encrypt(plaintext);
    ASSERT_TRUE(enc.ok);

    auto dec = cipher.decrypt(enc.ciphertext, enc.iv, enc.tag);
    ASSERT_TRUE(dec.ok);
    EXPECT_EQ(dec.plaintext, plaintext);
}

TEST(AesGcmTest, InvalidKeySizeThrows) {
    std::vector<uint8_t> short_key(16, 0x42);
    EXPECT_THROW(AesGcmCipher cipher(short_key), std::invalid_argument);
}
