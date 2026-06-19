#include "jd_relay/crypto/aes_gcm_cipher.h"
#include "jd_relay/crypto/base64.h"
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <stdexcept>
#include <cstring>

namespace jd_relay::crypto {

// ── AesGcmCipher ──────────────────────────────────────────────

AesGcmCipher::AesGcmCipher(const std::vector<uint8_t>& key) : key_(key) {
    if (key_.size() != 32) {
        throw std::invalid_argument("AES-256-GCM requires a 32-byte key, got "
            + std::to_string(key_.size()));
    }
}

AesGcmCipher::AesGcmCipher(const std::string& hex_key) {
    key_ = hex_decode(hex_key);
    if (key_.size() != 32) {
        throw std::invalid_argument("AES-256-GCM requires a 64-char hex key, got "
            + std::to_string(hex_key.size()) + " chars");
    }
}

AesGcmCipher::~AesGcmCipher() {
    // Securely zero the key
    if (!key_.empty()) {
        OPENSSL_cleanse(key_.data(), key_.size());
    }
}

AesGcmCipher::AesGcmCipher(AesGcmCipher&& other) noexcept
    : key_(std::move(other.key_)) {
    other.key_.clear();
}

AesGcmCipher& AesGcmCipher::operator=(AesGcmCipher&& other) noexcept {
    if (this != &other) {
        if (!key_.empty()) OPENSSL_cleanse(key_.data(), key_.size());
        key_ = std::move(other.key_);
        other.key_.clear();
    }
    return *this;
}

EncryptResult AesGcmCipher::encrypt(const std::vector<uint8_t>& plaintext,
                                     const std::vector<uint8_t>& iv) {
    EncryptResult result;

    // Use provided IV or generate one
    std::vector<uint8_t> use_iv = iv;
    if (use_iv.empty()) {
        use_iv = random_bytes(12);
    }
    if (use_iv.size() != 12) {
        result.error = "IV must be 12 bytes for AES-GCM, got "
                     + std::to_string(use_iv.size());
        return result;
    }

    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    if (!ctx) {
        result.error = "EVP_CIPHER_CTX_new failed";
        return result;
    }

    // Initialize encryption
    if (EVP_EncryptInit_ex(ctx, EVP_aes_256_gcm(), nullptr,
                           key_.data(), use_iv.data()) != 1) {
        result.error = "EVP_EncryptInit_ex failed";
        EVP_CIPHER_CTX_free(ctx);
        return result;
    }

    // Allocate output buffer (plaintext + 16 for potential padding, though GCM doesn't pad)
    result.ciphertext.resize(plaintext.size());

    int outlen = 0;
    if (EVP_EncryptUpdate(ctx, result.ciphertext.data(), &outlen,
                          plaintext.data(), static_cast<int>(plaintext.size())) != 1) {
        result.error = "EVP_EncryptUpdate failed";
        EVP_CIPHER_CTX_free(ctx);
        return result;
    }

    int finallen = 0;
    if (EVP_EncryptFinal_ex(ctx, result.ciphertext.data() + outlen, &finallen) != 1) {
        result.error = "EVP_EncryptFinal_ex failed";
        EVP_CIPHER_CTX_free(ctx);
        return result;
    }
    result.ciphertext.resize(outlen + finallen);

    // Get the GCM tag (16 bytes)
    result.tag.resize(16);
    if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_GET_TAG, 16, result.tag.data()) != 1) {
        result.error = "EVP_CTRL_GCM_GET_TAG failed";
        EVP_CIPHER_CTX_free(ctx);
        return result;
    }

    result.iv = use_iv;
    result.ok = true;

    EVP_CIPHER_CTX_free(ctx);
    return result;
}

DecryptResult AesGcmCipher::decrypt(const std::vector<uint8_t>& ciphertext,
                                     const std::vector<uint8_t>& iv,
                                     const std::vector<uint8_t>& tag) {
    DecryptResult result;

    if (iv.size() != 12) {
        result.error = "IV must be 12 bytes, got " + std::to_string(iv.size());
        return result;
    }
    if (tag.size() != 16) {
        result.error = "Tag must be 16 bytes, got " + std::to_string(tag.size());
        return result;
    }

    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    if (!ctx) {
        result.error = "EVP_CIPHER_CTX_new failed";
        return result;
    }

    if (EVP_DecryptInit_ex(ctx, EVP_aes_256_gcm(), nullptr,
                           key_.data(), iv.data()) != 1) {
        result.error = "EVP_DecryptInit_ex failed";
        EVP_CIPHER_CTX_free(ctx);
        return result;
    }

    result.plaintext.resize(ciphertext.size());

    int outlen = 0;
    if (EVP_DecryptUpdate(ctx, result.plaintext.data(), &outlen,
                          ciphertext.data(), static_cast<int>(ciphertext.size())) != 1) {
        result.error = "EVP_DecryptUpdate failed";
        EVP_CIPHER_CTX_free(ctx);
        return result;
    }

    // Set the expected tag before final
    if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_TAG, 16,
                            const_cast<uint8_t*>(tag.data())) != 1) {
        result.error = "EVP_CTRL_GCM_SET_TAG failed";
        EVP_CIPHER_CTX_free(ctx);
        return result;
    }

    int finallen = 0;
    if (EVP_DecryptFinal_ex(ctx, result.plaintext.data() + outlen, &finallen) != 1) {
        // Tag verification failed — ciphertext was tampered or wrong key
        result.error = "AES-GCM authentication tag verification failed";
        result.plaintext.clear();
        EVP_CIPHER_CTX_free(ctx);
        return result;
    }

    result.plaintext.resize(outlen + finallen);
    result.ok = true;

    EVP_CIPHER_CTX_free(ctx);
    return result;
}

std::vector<uint8_t> AesGcmCipher::generate_key() {
    return random_bytes(32);
}

} // namespace jd_relay::crypto
