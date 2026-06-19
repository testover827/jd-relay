/// test_cross_ws_server.cpp — Minimal C++ WebSocket server for cross-language testing.
///
/// Starts a WsServer, prints the port, waits for an Agent to connect and
/// send a message, then replies. Blocks until a message is received or timeout.
///
/// Usage: test_cross_ws_server <port> <ecdsa_priv.pem> <ecdsa_pub.pem>

#include "jd_relay/forwarder/ws_server.h"
#include "jd_relay/crypto/ecdsa_signer.h"

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>

namespace crypto = jd_relay::crypto;
using namespace jd_relay::forwarder;
using namespace std::chrono_literals;

int main(int argc, char* argv[]) {
    if (argc < 4) {
        std::cerr << "Usage: test_cross_ws_server <port> <priv.pem> <pub.pem>\n";
        return 1;
    }

    uint16_t    port     = static_cast<uint16_t>(std::stoi(argv[1]));
    std::string priv_pem = argv[2];
    std::string pub_pem  = argv[3];

    std::mutex mtx;
    std::string received_msg;
    std::string received_agent;
    bool got_msg = false;
    std::atomic<bool> agent_connected{false};

    auto msg_cb = [&](const std::string& agent_id, crypto::MessageType type,
                      const std::string& plaintext) {
        std::lock_guard<std::mutex> lock(mtx);
        received_agent = agent_id;
        received_msg   = plaintext;
        got_msg        = true;
        std::cerr << "[Server] Received from " << agent_id
                  << ": " << plaintext.substr(0, 200) << "\n";
    };

    auto conn_cb = [&](const std::string& agent_id, bool connected) {
        agent_connected.store(connected);
        std::cerr << "[Server] Agent " << agent_id
                  << (connected ? " connected" : " disconnected") << "\n";
    };

    WsServer server(port, priv_pem, pub_pem, msg_cb, conn_cb);
    server.start();
    std::cerr << "[Server] Listening on port " << server.port() << "\n";

    // Wait for a message (max 30 seconds)
    auto deadline = std::chrono::steady_clock::now() + 30s;
    while (std::chrono::steady_clock::now() < deadline && !got_msg) {
        std::this_thread::sleep_for(50ms);
    }

    if (got_msg) {
        // Reply to the agent
        std::string reply = R"({"status":"ok","reply":"hello from C++ server"})";
        server.send_to_agent(received_agent, crypto::MessageType::BUILD_RESULT, reply);
        std::cerr << "[Server] Sent reply\n";
        std::this_thread::sleep_for(2s);
    } else {
        std::cerr << "[Server] No message received (timeout)\n";
    }

    server.stop();
    std::this_thread::sleep_for(1s);

    return got_msg ? 0 : 1;
}
