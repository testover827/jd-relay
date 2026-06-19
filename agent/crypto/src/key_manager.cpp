#include "jd_relay/crypto/key_manager.h"
#include "jd_relay/crypto/aes_gcm_cipher.h"
#include "jd_relay/crypto/ecdsa_signer.h"
#include "jd_relay/crypto/ecdh_key_exchange.h"
#include "jd_relay/crypto/base64.h"
#include <openssl/evp.h>
#include <openssl/sha.h>
#include <cstdlib>
#include <stdexcept>
#include <iostream>

namespace jd_relay::crypto {

struct KeyManager::Impl {
    std::vector<uint8_t> aes_key;
    std::unique_ptr<EcdsaSigner> signer;     // our private key (for signing)
    std::unique_ptr<EcdsaSigner> verifier;   // peer's public key (for verifying)
};

KeyManager::KeyManager(const std::string& ecdsa_private_pem,
                       const std::string& peer_ecdsa_public_pem,
                       const std::string& aes_key_hex,
                       int key_version)
    : pimpl_(std::make_unique<Impl>()), key_version_(key_version) {

    // Load our ECDSA private key for signing
    pimpl_->signer = std::make_unique<EcdsaSigner>(ecdsa_private_pem);

    // Load peer's ECDSA public key for verification
    pimpl_->verifier = std::make_unique<EcdsaSigner>(
        EcdsaSigner::from_public_key_pem(peer_ecdsa_public_pem));

    // Load AES key
    std::string hex = aes_key_hex;
    if (hex.empty()) {
        // Try environment variable
        const char* env_key = std::getenv("RELAY_AES_KEY");
        if (env_key) {
            hex = env_key;
        } else {
            throw std::runtime_error("AES key not provided. Set aes_key_hex parameter "
                                     "or RELAY_AES_KEY environment variable.");
        }
    }

    pimpl_->aes_key = hex_decode(hex);
    if (pimpl_->aes_key.size() != 32) {
        throw std::runtime_error("AES key must be 32 bytes (64 hex chars), got "
            + std::to_string(pimpl_->aes_key.size()) + " bytes");
    }
}

KeyManager::~KeyManager() {
    if (pimpl_) {
        OPENSSL_cleanse(pimpl_->aes_key.data(), pimpl_->aes_key.size());
    }
}

KeyManager::KeyManager(KeyManager&& other) noexcept
    : pimpl_(std::move(other.pimpl_)), key_version_(other.key_version_) {}

KeyManager& KeyManager::operator=(KeyManager&& other) noexcept {
    if (this != &other) {
        if (pimpl_) OPENSSL_cleanse(pimpl_->aes_key.data(), pimpl_->aes_key.size());
        pimpl_ = std::move(other.pimpl_);
        key_version_ = other.key_version_;
    }
    return *this;
}

const std::vector<uint8_t>& KeyManager::aes_key() const {
    return pimpl_->aes_key;
}

std::string KeyManager::aes_key_hex() const {
    return hex_encode(pimpl_->aes_key);
}

EcdsaSigner* KeyManager::signer() {
    return pimpl_->signer.get();
}

EcdsaSigner* KeyManager::verifier() {
    return pimpl_->verifier.get();
}

void KeyManager::derive_session_key(EcdhKeyExchange& ecdh,
                                     const std::vector<uint8_t>& peer_pub_der) {
    // Securely clear old key
    if (!pimpl_->aes_key.empty()) {
        OPENSSL_cleanse(pimpl_->aes_key.data(), pimpl_->aes_key.size());
    }
    pimpl_->aes_key = ecdh.derive_shared_secret(peer_pub_der);
    if (pimpl_->aes_key.size() != 32) {
        throw std::runtime_error("ECDH key derivation failed: got "
            + std::to_string(pimpl_->aes_key.size()) + " bytes");
    }
}

void KeyManager::generate_all_keys(const std::string& output_dir) {
    // Generate ECDSA key pair
    std::string ecdsa_priv = output_dir + "/ecdsa_private.pem";
    std::string ecdsa_pub  = output_dir + "/ecdsa_public.pem";
    EcdsaSigner::generate_keypair(ecdsa_priv, ecdsa_pub);

    // Generate ECDH key pair (for session key exchange)
    std::string ecdh_priv = output_dir + "/ecdh_private.pem";
    std::string ecdh_pub  = output_dir + "/ecdh_public.pem";
    EcdhKeyExchange::generate_keypair(ecdh_priv, ecdh_pub);

    // Generate AES key
    auto aes_key = AesGcmCipher::generate_key();
    std::string aes_hex = hex_encode(aes_key);

    std::cout << "=== Keys generated successfully ===" << std::endl;
    std::cout << "ECDSA private key: " << ecdsa_priv << std::endl;
    std::cout << "ECDSA public key:  " << ecdsa_pub << std::endl;
    std::cout << "ECDH private key:  " << ecdh_priv << std::endl;
    std::cout << "ECDH public key:   " << ecdh_pub << std::endl;
    std::cout << std::endl;
    std::cout << "AES-256 session key (set as RELAY_AES_KEY env var):" << std::endl;
    std::cout << "  " << aes_hex << std::endl;
    std::cout << std::endl;
    std::cout << "⚠  Keep private keys secure. Do NOT commit to version control." << std::endl;

    // Securely clear the AES key from memory
    OPENSSL_cleanse(aes_key.data(), aes_key.size());
}

} // namespace jd_relay::crypto
