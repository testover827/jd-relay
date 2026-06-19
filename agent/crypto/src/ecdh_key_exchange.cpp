#include "jd_relay/crypto/ecdh_key_exchange.h"
#include "jd_relay/crypto/base64.h"
#include <openssl/evp.h>
#include <openssl/pem.h>
#include <openssl/ec.h>
#include <openssl/err.h>
#include <openssl/sha.h>
#include <openssl/kdf.h>
#include <stdexcept>
#include <fstream>
#include <sstream>
#include <memory>

namespace jd_relay::crypto {

struct EcdhKeyExchange::Impl {
    EVP_PKEY* key_pair{nullptr};
};

// ── Helpers ───────────────────────────────────────────────────

static std::vector<uint8_t> read_file(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error("Cannot open file: " + path);
    std::stringstream ss;
    ss << f.rdbuf();
    std::string s = ss.str();
    return std::vector<uint8_t>(s.begin(), s.end());
}

// ── Constructors ──────────────────────────────────────────────

EcdhKeyExchange::EcdhKeyExchange() : pimpl_(std::make_unique<Impl>()) {
    // Generate a new P-256 key pair
    EVP_PKEY_CTX* pctx = EVP_PKEY_CTX_new_id(EVP_PKEY_EC, nullptr);
    if (!pctx) throw std::runtime_error("EVP_PKEY_CTX_new_id failed");

    if (EVP_PKEY_keygen_init(pctx) != 1) {
        EVP_PKEY_CTX_free(pctx);
        throw std::runtime_error("EVP_PKEY_keygen_init failed");
    }

    if (EVP_PKEY_CTX_set_ec_paramgen_curve_nid(pctx, NID_X9_62_prime256v1) != 1) {
        EVP_PKEY_CTX_free(pctx);
        throw std::runtime_error("Failed to set EC curve P-256");
    }

    EVP_PKEY* pkey = nullptr;
    if (EVP_PKEY_keygen(pctx, &pkey) != 1) {
        EVP_PKEY_CTX_free(pctx);
        throw std::runtime_error("ECDH key generation failed");
    }
    EVP_PKEY_CTX_free(pctx);
    pimpl_->key_pair = pkey;
}

EcdhKeyExchange::~EcdhKeyExchange() {
    if (pimpl_ && pimpl_->key_pair) {
        EVP_PKEY_free(pimpl_->key_pair);
    }
}

EcdhKeyExchange::EcdhKeyExchange(EcdhKeyExchange&& other) noexcept
    : pimpl_(std::move(other.pimpl_)) {}

EcdhKeyExchange& EcdhKeyExchange::operator=(EcdhKeyExchange&& other) noexcept {
    if (this != &other) {
        if (pimpl_ && pimpl_->key_pair) EVP_PKEY_free(pimpl_->key_pair);
        pimpl_ = std::move(other.pimpl_);
    }
    return *this;
}

// ── Key export ────────────────────────────────────────────────

std::vector<uint8_t> EcdhKeyExchange::public_key_der() const {
    if (!pimpl_->key_pair) return {};

    unsigned char* der = nullptr;
    int len = i2d_PUBKEY(pimpl_->key_pair, &der);
    if (len <= 0 || !der) return {};

    std::vector<uint8_t> result(der, der + len);
    OPENSSL_free(der);
    return result;
}

std::string EcdhKeyExchange::public_key_pem() const {
    if (!pimpl_->key_pair) return "";

    BIO* bio = BIO_new(BIO_s_mem());
    if (!bio) return "";

    PEM_write_bio_PUBKEY(bio, pimpl_->key_pair);

    BUF_MEM* bptr = nullptr;
    BIO_get_mem_ptr(bio, &bptr);

    std::string result(bptr->data, bptr->length);
    BIO_free(bio);
    return result;
}

// ── Shared secret derivation ──────────────────────────────────

std::vector<uint8_t> EcdhKeyExchange::derive_shared_secret(
        const std::vector<uint8_t>& peer_pub_der) {

    if (!pimpl_->key_pair) return {};

    // Load peer's public key from DER
    const unsigned char* p = peer_pub_der.data();
    EVP_PKEY* peer_key = d2i_PUBKEY(nullptr, &p, static_cast<long>(peer_pub_der.size()));
    if (!peer_key) return {};

    // Derive shared secret
    EVP_PKEY_CTX* ctx = EVP_PKEY_CTX_new(pimpl_->key_pair, nullptr);
    if (!ctx) {
        EVP_PKEY_free(peer_key);
        return {};
    }

    std::vector<uint8_t> secret;

    if (EVP_PKEY_derive_init(ctx) != 1) {
        EVP_PKEY_CTX_free(ctx);
        EVP_PKEY_free(peer_key);
        return {};
    }

    if (EVP_PKEY_derive_set_peer(ctx, peer_key) != 1) {
        EVP_PKEY_CTX_free(ctx);
        EVP_PKEY_free(peer_key);
        return {};
    }

    size_t secret_len = 0;
    if (EVP_PKEY_derive(ctx, nullptr, &secret_len) != 1) {
        EVP_PKEY_CTX_free(ctx);
        EVP_PKEY_free(peer_key);
        return {};
    }

    secret.resize(secret_len);
    if (EVP_PKEY_derive(ctx, secret.data(), &secret_len) != 1) {
        EVP_PKEY_CTX_free(ctx);
        EVP_PKEY_free(peer_key);
        return {};
    }
    secret.resize(secret_len);

    EVP_PKEY_CTX_free(ctx);
    EVP_PKEY_free(peer_key);

    // Derive a 32-byte AES key from the shared secret using SHA-256 KDF
    // Simple approach: SHA-256(shared_secret)
    // This is sufficient for our use case since ECDH(P-256) shared secret
    // is already 32 bytes of high-entropy material.
    std::vector<uint8_t> aes_key(32);
    SHA256(secret.data(), secret.size(), aes_key.data());

    // Cleanse the raw shared secret
    OPENSSL_cleanse(secret.data(), secret.size());

    return aes_key;
}

std::vector<uint8_t> EcdhKeyExchange::derive_shared_secret_pem(
        const std::string& peer_pub_pem) {

    BIO* bio = BIO_new_mem_buf(peer_pub_pem.data(), static_cast<int>(peer_pub_pem.size()));
    if (!bio) return {};

    EVP_PKEY* peer_key = PEM_read_bio_PUBKEY(bio, nullptr, nullptr, nullptr);
    BIO_free(bio);
    if (!peer_key) return {};

    // Get DER from the PEM-loaded key
    unsigned char* der = nullptr;
    int len = i2d_PUBKEY(peer_key, &der);
    EVP_PKEY_free(peer_key);

    if (len <= 0 || !der) return {};

    std::vector<uint8_t> der_vec(der, der + len);
    OPENSSL_free(der);

    return derive_shared_secret(der_vec);
}

// ── Static methods ────────────────────────────────────────────

void EcdhKeyExchange::generate_keypair(const std::string& private_pem_file,
                                        const std::string& public_pem_file) {
    EcdhKeyExchange ecdh;  // Generates a new key pair

    // Write private key
    BIO* bio_priv = BIO_new_file(private_pem_file.c_str(), "w");
    if (!bio_priv) throw std::runtime_error("Cannot open: " + private_pem_file);
    PEM_write_bio_PrivateKey(bio_priv, ecdh.pimpl_->key_pair, nullptr, nullptr, 0, nullptr, nullptr);
    BIO_free(bio_priv);

    // Write public key
    BIO* bio_pub = BIO_new_file(public_pem_file.c_str(), "w");
    if (!bio_pub) throw std::runtime_error("Cannot open: " + public_pem_file);
    PEM_write_bio_PUBKEY(bio_pub, ecdh.pimpl_->key_pair);
    BIO_free(bio_pub);
}

EcdhKeyExchange EcdhKeyExchange::from_private_key_pem(const std::string& pem_file) {
    auto pem_data = read_file(pem_file);

    BIO* bio = BIO_new_mem_buf(pem_data.data(), static_cast<int>(pem_data.size()));
    if (!bio) throw std::runtime_error("BIO_new_mem_buf failed");

    EVP_PKEY* key = PEM_read_bio_PrivateKey(bio, nullptr, nullptr, nullptr);
    BIO_free(bio);
    if (!key) throw std::runtime_error("Failed to load ECDH private key from: " + pem_file);

    EcdhKeyExchange result;
    // Free the auto-generated key, replace with loaded one
    if (result.pimpl_->key_pair) EVP_PKEY_free(result.pimpl_->key_pair);
    result.pimpl_->key_pair = key;

    return result;
}

} // namespace jd_relay::crypto
