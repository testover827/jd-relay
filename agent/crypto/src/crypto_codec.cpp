#include "jd_relay/crypto/crypto_codec.h"
#include "jd_relay/crypto/base64.h"
#include <nlohmann/json.hpp>
#include <stdexcept>

namespace jd_relay::crypto {

using json = nlohmann::json;

// ── Constructor / Destructor ──────────────────────────────────

CryptoCodec::CryptoCodec(std::unique_ptr<ICipher> cipher,
                         std::unique_ptr<ISigner> signer,
                         std::unique_ptr<ISigner> verifier,
                         std::unique_ptr<ReplayGuard> guard)
    : cipher_(std::move(cipher))
    , signer_(std::move(signer))
    , verifier_(std::move(verifier))
    , guard_(std::move(guard)) {}

CryptoCodec::~CryptoCodec() = default;

CryptoCodec::CryptoCodec(CryptoCodec&& other) noexcept
    : cipher_(std::move(other.cipher_))
    , signer_(std::move(other.signer_))
    , verifier_(std::move(other.verifier_))
    , guard_(std::move(other.guard_)) {}

CryptoCodec& CryptoCodec::operator=(CryptoCodec&& other) noexcept {
    if (this != &other) {
        cipher_   = std::move(other.cipher_);
        signer_   = std::move(other.signer_);
        verifier_ = std::move(other.verifier_);
        guard_    = std::move(other.guard_);
    }
    return *this;
}

// ── Encrypt ───────────────────────────────────────────────────

CryptoEnvelope CryptoCodec::encrypt(const std::vector<uint8_t>& plaintext,
                                     MessageType type) {
    CryptoEnvelope env;

    // 1. Generate metadata
    env.msg_id    = generate_uuid();
    env.timestamp = ReplayGuard::now_ms();
    env.nonce     = base64_encode(random_bytes(16));
    env.type      = to_string(type);

    // 2. AES-256-GCM encrypt
    auto enc_result = cipher_->encrypt(plaintext);
    if (!enc_result.ok) {
        throw std::runtime_error("Encryption failed: " + enc_result.error);
    }

    env.iv         = base64_encode(enc_result.iv);
    env.ciphertext = base64_encode(enc_result.ciphertext);
    env.tag        = base64_encode(enc_result.tag);

    // 3. ECDSA sign the canonical payload
    std::string payload = build_signing_payload(env);
    std::vector<uint8_t> payload_bytes(payload.begin(), payload.end());
    auto signature = signer_->sign(payload_bytes);
    env.signature = base64_encode(signature);

    return env;
}

// ── Decrypt ───────────────────────────────────────────────────

DecryptResult CryptoCodec::decrypt(const CryptoEnvelope& env) {
    DecryptResult result;

    // 1. Check timestamp window
    if (!guard_->is_within_window(env.timestamp)) {
        result.error = "Timestamp outside acceptable window (msg_id=" + env.msg_id + ")";
        return result;
    }

    // 2. Check replay (nonce uniqueness)
    if (guard_->is_replay(env.nonce)) {
        result.error = "Replay detected: nonce already seen (msg_id=" + env.msg_id + ")";
        return result;
    }

    // 3. Verify ECDSA signature
    std::string payload = build_signing_payload(env);
    std::vector<uint8_t> payload_bytes(payload.begin(), payload.end());
    auto signature = base64_decode(env.signature);

    if (!verifier_->verify(payload_bytes, signature)) {
        result.error = "ECDSA signature verification failed (msg_id=" + env.msg_id + ")";
        return result;
    }

    // 4. AES-256-GCM decrypt
    auto ciphertext = base64_decode(env.ciphertext);
    auto iv         = base64_decode(env.iv);
    auto tag        = base64_decode(env.tag);

    auto dec_result = cipher_->decrypt(ciphertext, iv, tag);
    if (!dec_result.ok) {
        result.error = "AES-GCM decryption failed: " + dec_result.error
                     + " (msg_id=" + env.msg_id + ")";
        return result;
    }

    // 5. Record nonce to prevent future replays
    guard_->record_nonce(env.nonce);

    result.plaintext = std::move(dec_result.plaintext);
    result.ok = true;
    return result;
}

// ── JSON serialization ────────────────────────────────────────

std::string CryptoCodec::to_json(const CryptoEnvelope& env) {
    json j;
    j["msg_id"]     = env.msg_id;
    j["timestamp"]  = env.timestamp;
    j["nonce"]      = env.nonce;
    j["type"]       = env.type;
    j["iv"]         = env.iv;
    j["ciphertext"] = env.ciphertext;
    j["tag"]        = env.tag;
    j["signature"]  = env.signature;
    return j.dump();
}

CryptoEnvelope CryptoCodec::from_json(const std::string& json_str) {
    json j = json::parse(json_str);
    CryptoEnvelope env;
    env.msg_id     = j.value("msg_id", "");
    env.timestamp  = j.value("timestamp", 0LL);
    env.nonce      = j.value("nonce", "");
    env.type       = j.value("type", "");
    env.iv         = j.value("iv", "");
    env.ciphertext = j.value("ciphertext", "");
    env.tag        = j.value("tag", "");
    env.signature  = j.value("signature", "");
    return env;
}

} // namespace jd_relay::crypto
