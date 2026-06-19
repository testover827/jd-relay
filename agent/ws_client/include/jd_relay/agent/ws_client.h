#pragma once

#include "jd_relay/crypto/crypto_codec.h"
#include "jd_relay/crypto/ecdh_key_exchange.h"
#include "jd_relay/crypto/envelope.h"
#include "jd_relay/protocol/handshake.h"

#include <boost/beast/core.hpp>
#include <boost/beast/websocket.hpp>
#include <boost/asio.hpp>

#include <atomic>
#include <condition_variable>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace jd_relay::agent {

namespace beast     = boost::beast;
namespace websocket = beast::websocket;
namespace net       = boost::asio;
using tcp           = net::ip::tcp;
namespace crypto    = jd_relay::crypto;

/// WebSocket client that connects to the Forwarder.
/// Features: ECDH handshake, encrypted I/O, exponential-backoff auto-reconnect.
class WsClient {
public:
    /// Called when a decrypted message arrives from the Forwarder.
    using MessageCallback = std::function<void(
        crypto::MessageType type,
        const std::string& plaintext_json)>;

    /// Called when connection state changes.
    using ConnectCallback = std::function<void(bool connected)>;

    WsClient(const std::string& host,
             uint16_t port,
             const std::string& agent_id,
             const std::vector<std::string>& projects,
             const std::string& ecdsa_priv_file,
             const std::string& ecdsa_pub_file,
             MessageCallback msg_cb,
             ConnectCallback conn_cb = nullptr);

    ~WsClient();

    WsClient(const WsClient&) = delete;
    WsClient& operator=(const WsClient&) = delete;

    /// Start the I/O thread (connects, handshakes, runs I/O loop, reconnects).
    void start();

    /// Stop the client and wait for the I/O thread to finish.
    void stop();

    /// Thread-safe: send an encrypted message to the Forwarder.
    bool send(crypto::MessageType type, const std::string& payload_json);

    bool is_connected() const { return connected_.load(); }

private:
    void run();
    bool connect_and_handshake();
    void io_loop();
    void close_connection();

    // ── Configuration ──────────────────────────────────────────
    std::string host_;
    uint16_t    port_;
    std::string agent_id_;
    std::vector<std::string> projects_;
    std::string ecdsa_priv_file_;
    std::string ecdsa_pub_file_;
    MessageCallback  message_cb_;
    ConnectCallback  connect_cb_;

    // ── Network ────────────────────────────────────────────────
    net::io_context ioc_;
    std::unique_ptr<websocket::stream<tcp::socket>> ws_;

    // ── Crypto (per-connection) ────────────────────────────────
    std::unique_ptr<crypto::CryptoCodec>     codec_;
    std::unique_ptr<crypto::EcdhKeyExchange> ecdh_;

    // ── Threading ──────────────────────────────────────────────
    std::thread       io_thread_;
    std::atomic<bool> running_{false};
    std::atomic<bool> connected_{false};
    std::mutex        write_mutex_;

    // For interruptible backoff sleep
    std::mutex              cv_mutex_;
    std::condition_variable cv_;
};

} // namespace jd_relay::agent
