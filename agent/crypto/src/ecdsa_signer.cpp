#include "jd_relay/crypto/ecdsa_signer.h"
#include "jd_relay/crypto/base64.h"
#include <openssl/evp.h>
#include <openssl/pem.h>
#include <openssl/ec.h>
#include <openssl/err.h>
#include <openssl/sha.h>
#include <stdexcept>
#include <fstream>
#include <sstream>

namespace jd_relay::crypto {

struct EcdsaSigner::Impl {
    EVP_PKEY* private_key{nullptr};
    EVP_PKEY* public_key{nullptr};
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

static EVP_PKEY* load_pem_private_key(const std::vector<uint8_t>& pem) {
    BIO* bio = BIO_new_mem_buf(pem.data(), static_cast<int>(pem.size()));
    if (!bio) return nullptr;
    EVP_PKEY* key = PEM_read_bio_PrivateKey(bio, nullptr, nullptr, nullptr);
    BIO_free(bio);
    return key;
}

static EVP_PKEY* load_pem_public_key(const std::vector<uint8_t>& pem) {
    BIO* bio = BIO_new_mem_buf(pem.data(), static_cast<int>(pem.size()));
    if (!bio) return nullptr;
    EVP_PKEY* key = PEM_read_bio_PUBKEY(bio, nullptr, nullptr, nullptr);
    BIO_free(bio);
    return key;
}

// ── Constructors ──────────────────────────────────────────────

EcdsaSigner::EcdsaSigner() : pimpl_(std::make_unique<Impl>()) {}

EcdsaSigner::EcdsaSigner(const std::string& key_pem_file)
    : pimpl_(std::make_unique<Impl>()) {
    auto pem = read_file(key_pem_file);
    load_private_key(pem);
}

EcdsaSigner::EcdsaSigner(const std::vector<uint8_t>& pem_data)
    : pimpl_(std::make_unique<Impl>()) {
    if (pem_data.empty()) return;
    load_private_key(pem_data);
}

EcdsaSigner EcdsaSigner::from_public_key_pem(const std::string& key_pem_file) {
    auto pem = read_file(key_pem_file);
    return from_public_key_data(pem);
}

EcdsaSigner EcdsaSigner::from_public_key_data(const std::vector<uint8_t>& pem_data) {
    EcdsaSigner result;
    result.load_public_key(pem_data);
    return result;
}

EcdsaSigner::~EcdsaSigner() {
    if (pimpl_) {
        if (pimpl_->private_key) EVP_PKEY_free(pimpl_->private_key);
        if (pimpl_->public_key) EVP_PKEY_free(pimpl_->public_key);
    }
}

EcdsaSigner::EcdsaSigner(EcdsaSigner&& other) noexcept
    : pimpl_(std::move(other.pimpl_)) {}

EcdsaSigner& EcdsaSigner::operator=(EcdsaSigner&& other) noexcept {
    if (this != &other) {
        if (pimpl_) {
            if (pimpl_->private_key) EVP_PKEY_free(pimpl_->private_key);
            if (pimpl_->public_key) EVP_PKEY_free(pimpl_->public_key);
        }
        pimpl_ = std::move(other.pimpl_);
    }
    return *this;
}

void EcdsaSigner::load_private_key(const std::vector<uint8_t>& pem) {
    pimpl_->private_key = load_pem_private_key(pem);
    if (!pimpl_->private_key) {
        throw std::runtime_error("Failed to load ECDSA private key from PEM");
    }
}

void EcdsaSigner::load_public_key(const std::vector<uint8_t>& pem) {
    pimpl_->public_key = load_pem_public_key(pem);
    if (!pimpl_->public_key) {
        throw std::runtime_error("Failed to load ECDSA public key from PEM");
    }
}

// ── Sign / Verify ─────────────────────────────────────────────

std::vector<uint8_t> EcdsaSigner::sign(const std::vector<uint8_t>& data) {
    if (!pimpl_->private_key) return {};

    EVP_MD_CTX* ctx = EVP_MD_CTX_new();
    if (!ctx) return {};

    std::vector<uint8_t> signature;

    if (EVP_DigestSignInit(ctx, nullptr, EVP_sha256(), nullptr, pimpl_->private_key) != 1) {
        EVP_MD_CTX_free(ctx);
        return {};
    }

    size_t siglen = 0;
    if (EVP_DigestSign(ctx, nullptr, &siglen, data.data(), data.size()) != 1) {
        EVP_MD_CTX_free(ctx);
        return {};
    }

    signature.resize(siglen);
    if (EVP_DigestSign(ctx, signature.data(), &siglen,
                       data.data(), data.size()) != 1) {
        EVP_MD_CTX_free(ctx);
        return {};
    }
    signature.resize(siglen);

    EVP_MD_CTX_free(ctx);
    return signature;
}

bool EcdsaSigner::verify(const std::vector<uint8_t>& data,
                          const std::vector<uint8_t>& signature) {
    // Prefer public key; fall back to private key (which contains public key)
    EVP_PKEY* key = pimpl_->public_key ? pimpl_->public_key : pimpl_->private_key;
    if (!key) return false;

    EVP_MD_CTX* ctx = EVP_MD_CTX_new();
    if (!ctx) return false;

    bool ok = false;
    if (EVP_DigestVerifyInit(ctx, nullptr, EVP_sha256(), nullptr, key) == 1) {
        int ret = EVP_DigestVerify(ctx, signature.data(), signature.size(),
                                   data.data(), data.size());
        ok = (ret == 1);
    }

    EVP_MD_CTX_free(ctx);
    return ok;
}

// ── Public key export ─────────────────────────────────────────

std::vector<uint8_t> EcdsaSigner::public_key_der() const {
    EVP_PKEY* key = pimpl_->public_key ? pimpl_->public_key : pimpl_->private_key;
    if (!key) return {};

    unsigned char* der = nullptr;
    int len = i2d_PUBKEY(key, &der);
    if (len <= 0 || !der) return {};

    std::vector<uint8_t> result(der, der + len);
    OPENSSL_free(der);
    return result;
}

bool EcdsaSigner::can_sign() const {
    return pimpl_ && pimpl_->private_key != nullptr;
}

// ── Key generation ────────────────────────────────────────────

void EcdsaSigner::generate_keypair(const std::string& private_pem_file,
                                    const std::string& public_pem_file) {
    EVP_PKEY* pkey = nullptr;
    EVP_PKEY_CTX* pctx = EVP_PKEY_CTX_new_id(EVP_PKEY_EC, nullptr);
    if (!pctx) throw std::runtime_error("EVP_PKEY_CTX_new_id failed");

    if (EVP_PKEY_keygen_init(pctx) != 1) {
        EVP_PKEY_CTX_free(pctx);
        throw std::runtime_error("EVP_PKEY_keygen_init failed");
    }

    if (EVP_PKEY_CTX_set_ec_paramgen_curve_nid(pctx, NID_X9_62_prime256v1) != 1) {
        EVP_PKEY_CTX_free(pctx);
        throw std::runtime_error("Failed to set EC curve to P-256");
    }

    if (EVP_PKEY_keygen(pctx, &pkey) != 1) {
        EVP_PKEY_CTX_free(pctx);
        throw std::runtime_error("ECDSA key generation failed");
    }
    EVP_PKEY_CTX_free(pctx);

    // Write private key
    BIO* bio_priv = BIO_new_file(private_pem_file.c_str(), "w");
    if (!bio_priv) {
        EVP_PKEY_free(pkey);
        throw std::runtime_error("Cannot open private key file: " + private_pem_file);
    }
    PEM_write_bio_PrivateKey(bio_priv, pkey, nullptr, nullptr, 0, nullptr, nullptr);
    BIO_free(bio_priv);

    // Write public key
    BIO* bio_pub = BIO_new_file(public_pem_file.c_str(), "w");
    if (!bio_pub) {
        EVP_PKEY_free(pkey);
        throw std::runtime_error("Cannot open public key file: " + public_pem_file);
    }
    PEM_write_bio_PUBKEY(bio_pub, pkey);
    BIO_free(bio_pub);

    EVP_PKEY_free(pkey);
}

} // namespace jd_relay::crypto
