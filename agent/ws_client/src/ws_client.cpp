#include "jd_relay/agent/ws_client.h"
#include "jd_relay/crypto/aes_gcm_cipher.h"
#include "jd_relay/crypto/base64.h"
#include "jd_relay/crypto/ecdsa_signer.h"
#include "jd_relay/crypto/replay_guard.h"
#include "jd_relay/protocol/util.h"

#include <algorithm>
#include <chrono>
#include <iostream>

namespace jd_relay::agent {

namespace beast = boost::beast;
namespace net   = boost::asio;

// ──────────────────────────────────────────────────────────────────
// Construction / Destruction
// ──────────────────────────────────────────────────────────────────

WsClient::WsClient(const std::string& host,
                   uint16_t port,
                   const std::string& agent_id,
                   const std::vector<std::string>& projects,
                   const std::string& ecdsa_priv_file,
                   const std::string& ecdsa_pub_file,
                   MessageCallback msg_cb,
                   ConnectCallback conn_cb)
    : host_(host)
    , port_(port)
    , agent_id_(agent_id)
    , projects_(projects)
    , ecdsa_priv_file_(ecdsa_priv_file)
    , ecdsa_pub_file_(ecdsa_pub_file)
    , message_cb_(std::move(msg_cb))
    , connect_cb_(std::move(conn_cb))
{
}

WsClient::~WsClient() {
    stop();
    if (io_thread_.joinable()) {
        io_thread_.join();
    }
}

// ──────────────────────────────────────────────────────────────────
// Lifecycle
// ──────────────────────────────────────────────────────────────────

void WsClient::start() {
    running_.store(true);
    io_thread_ = std::thread([this]() { run(); });
}

void WsClient::stop() {
    running_.store(false);
    cv_.notify_all();           // wake up backoff sleep
    close_connection();         // unblock ws_->read()
}

void WsClient::close_connection() {
    if (ws_) {
        boost::system::error_code ec;
        // shutdown() reliably unblocks a blocking read() from another thread
        ws_->next_layer().shutdown(tcp::socket::shutdown_both, ec);
        ws_->next_layer().close(ec);
    }
}

// ──────────────────────────────────────────────────────────────────
// Main loop: connect → handshake → I/O → reconnect on failure
// ──────────────────────────────────────────────────────────────────

void WsClient::run() {
    int backoff = 1;  // seconds

    while (running_.load()) {
        bool ok = false;
        try {
            ok = connect_and_handshake();
        } catch (const std::exception& e) {
            std::cerr << "[WsClient] Connect/handshake error: " << e.what() << "\n";
        }

        if (ok) {
            connected_.store(true);
            if (connect_cb_) connect_cb_(true);
            backoff = 1;  // reset backoff on successful connect

            io_loop();    // blocks until connection breaks

            connected_.store(false);
            if (connect_cb_) connect_cb_(false);
        }

        // Clean up per-connection state
        ws_.reset();
        codec_.reset();
        ecdh_.reset();

        if (!running_.load()) break;

        // Interruptible backoff sleep
        {
            std::unique_lock<std::mutex> lock(cv_mutex_);
            cv_.wait_for(lock, std::chrono::seconds(backoff),
                         [this]() { return !running_.load(); });
        }
        backoff = std::min(backoff * 2, 30);  // max 30s
    }
}

// ──────────────────────────────────────────────────────────────────
// Connect + Handshake
// ──────────────────────────────────────────────────────────────────

bool WsClient::connect_and_handshake() {
    // 1. TCP connect
    tcp::resolver resolver(ioc_);
    auto results = resolver.resolve(host_, std::to_string(port_));
    tcp::socket socket(ioc_);
    net::connect(socket, results.begin(), results.end());

    // 2. WebSocket upgrade
    ws_ = std::make_unique<websocket::stream<tcp::socket>>(std::move(socket));
    ws_->handshake(host_ + ":" + std::to_string(port_), "/agent-ws");

    // 3. Generate ephemeral ECDH key pair
    ecdh_ = std::make_unique<crypto::EcdhKeyExchange>();

    // 4. Read Agent's ECDSA public key from file
    std::string agent_pub_pem = protocol::read_file(ecdsa_pub_file_);

    // 5. Sign handshake data with Agent's ECDSA private key
    std::string signing_data =
        agent_id_ + "|" + ecdh_->public_key_pem() + "|" + agent_pub_pem;
    crypto::EcdsaSigner signer(ecdsa_priv_file_);
    auto sig = signer.sign(
        std::vector<uint8_t>(signing_data.begin(), signing_data.end()));

    // 6. Send HandshakeInit
    protocol::HandshakeInit init;
    init.agent_id      = agent_id_;
    init.projects      = projects_;
    init.ecdh_pub_pem  = ecdh_->public_key_pem();
    init.ecdsa_pub_pem = agent_pub_pem;
    init.signature_b64 = crypto::base64_encode(sig);
    ws_->write(net::buffer(init.to_json()));

    // 7. Read HandshakeAck
    beast::flat_buffer buf;
    ws_->read(buf);
    auto ack = protocol::HandshakeAck::from_json(
        beast::buffers_to_string(buf.data()));

    if (ack.status != "OK") {
        std::cerr << "[WsClient] Handshake rejected: " << ack.error << "\n";
        return false;
    }

    // 8. Verify Forwarder's signature
    std::vector<uint8_t> fwd_pub_pem(ack.ecdsa_pub_pem.begin(),
                                      ack.ecdsa_pub_pem.end());
    auto verifier = crypto::EcdsaSigner::from_public_key_data(fwd_pub_pem);
    auto ack_data = ack.signing_data();
    auto ack_sig  = crypto::base64_decode(ack.signature_b64);
    if (!verifier.verify(
            std::vector<uint8_t>(ack_data.begin(), ack_data.end()),
            ack_sig)) {
        std::cerr << "[WsClient] Forwarder signature verification failed\n";
        return false;
    }

    // 9. Derive shared AES-256 key
    auto shared_secret = ecdh_->derive_shared_secret_pem(ack.ecdh_pub_pem);

    // 10. Assemble CryptoCodec
    auto signer_ptr = std::make_unique<crypto::EcdsaSigner>(ecdsa_priv_file_);
    auto verifier_ptr = std::make_unique<crypto::EcdsaSigner>(
        crypto::EcdsaSigner::from_public_key_data(fwd_pub_pem));

    codec_ = std::make_unique<crypto::CryptoCodec>(
        std::make_unique<crypto::AesGcmCipher>(shared_secret),
        std::move(signer_ptr),
        std::move(verifier_ptr),
        std::make_unique<crypto::ReplayGuard>());

    return true;
}

// ──────────────────────────────────────────────────────────────────
// I/O Loop (blocks until connection breaks)
// ──────────────────────────────────────────────────────────────────

void WsClient::io_loop() {
    beast::flat_buffer buf;
    while (running_.load()) {
        boost::system::error_code ec;
        ws_->read(buf, ec);
        if (ec) break;

        std::string json_str = beast::buffers_to_string(buf.data());
        buf.consume(buf.size());

        auto env    = crypto::CryptoCodec::from_json(json_str);
        auto result = codec_->decrypt(env);
        if (!result.ok) {
            std::cerr << "[WsClient] Decrypt error: " << result.error << "\n";
            continue;
        }

        auto type = crypto::parse_message_type(env.type);

        if (type == crypto::MessageType::HEARTBEAT) continue;
        if (type == crypto::MessageType::ACK)       continue;

        std::string plaintext(result.plaintext.begin(),
                              result.plaintext.end());
        if (message_cb_) {
            message_cb_(type, plaintext);
        }
    }
}

// ──────────────────────────────────────────────────────────────────
// Send (thread-safe)
// ──────────────────────────────────────────────────────────────────

bool WsClient::send(crypto::MessageType type,
                     const std::string& payload_json) {
    if (!connected_.load() || !running_.load() || !codec_ || !ws_) {
        return false;
    }
    auto env = codec_->encrypt(
        std::vector<uint8_t>(payload_json.begin(), payload_json.end()),
        type);
    std::string json_str = crypto::CryptoCodec::to_json(env);

    std::lock_guard<std::mutex> lock(write_mutex_);
    boost::system::error_code ec;
    ws_->write(net::buffer(json_str), ec);
    return !ec;
}

} // namespace jd_relay::agent
