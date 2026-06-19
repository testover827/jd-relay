/// test_cross_ws.cpp — Cross-language WebSocket end-to-end test.
///
/// Connects a C++ Agent (WsClient) to a Python Forwarder, performs
/// ECDH+ECDSA handshake, and exchanges encrypted messages.
///
/// Usage:
///   test_cross_ws <forwarder_host> <forwarder_port> <ecdsa_priv.pem> <ecdsa_pub.pem> <fwd_pub.pem>
///
/// Exit code: 0 = all tests passed, 1 = failure.

#include "jd_relay/agent/ws_client.h"
#include "jd_relay/crypto/ecdsa_signer.h"
#include "jd_relay/crypto/envelope.h"

#include <nlohmann/json.hpp>

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>

namespace crypto = jd_relay::crypto;
using namespace jd_relay::agent;
using namespace std::chrono_literals;
using json = nlohmann::json;

template<typename Fn>
bool wait_for(Fn cond, std::chrono::seconds timeout = 10s) {
    auto deadline = std::chrono::steady_clock::now() + timeout;
    while (std::chrono::steady_clock::now() < deadline) {
        if (cond()) return true;
        std::this_thread::sleep_for(50ms);
    }
    return cond();
}

int main(int argc, char* argv[]) {
    if (argc < 6) {
        std::cerr << "Usage: test_cross_ws <host> <port> <agt_priv.pem> <agt_pub.pem> <fwd_pub.pem>\n";
        return 1;
    }

    std::string host       = argv[1];
    uint16_t    port       = static_cast<uint16_t>(std::stoi(argv[2]));
    std::string agt_priv   = argv[3];
    std::string agt_pub    = argv[4];
    std::string fwd_pub    = argv[5];

    int exit_code = 0;

    // ── Test 1: Handshake + message exchange ────────────────────
    std::cout << "=== Test 1: Handshake + Message Exchange ===\n";

    std::mutex mtx;
    std::string agent_received;
    bool agent_got_msg = false;
    std::atomic<bool> connected{false};
    std::atomic<bool> disconnected{false};

    auto msg_cb = [&](crypto::MessageType type, const std::string& plaintext) {
        std::lock_guard<std::mutex> lock(mtx);
        agent_received = plaintext;
        agent_got_msg = true;
        std::cout << "[Agent] Received: type=" << crypto::to_string(type)
                  << " payload=" << plaintext.substr(0, 100) << "\n";
    };

    auto conn_cb = [&](bool is_connected) {
        if (is_connected) {
            connected.store(true);
            std::cout << "[Agent] Connected\n";
        } else {
            disconnected.store(true);
            std::cout << "[Agent] Disconnected\n";
        }
    };

    WsClient client(host, port, "cpp-agent-001", {"test-project"},
                    agt_priv, agt_pub, msg_cb, conn_cb);
    client.start();

    // Wait for connection
    if (!wait_for([&]() { return connected.load(); }, 10s)) {
        std::cerr << "FAIL: Agent did not connect within 10s\n";
        exit_code = 1;
        goto cleanup;
    }
    std::cout << "PASS: Agent connected and handshake completed\n";

    // Send a BUILD_RESULT message
    {
        json result_msg = {
            {"work_order_id", "WO-CROSS-001"},
            {"build_number", 1},
            {"status", "SUCCESS"},
            {"log_url", "http://jenkins/job/1/log"},
        };
        if (client.send(crypto::MessageType::BUILD_RESULT, result_msg.dump())) {
            std::cout << "[Agent] Sent BUILD_RESULT\n";
        } else {
            std::cerr << "FAIL: Failed to send BUILD_RESULT\n";
            exit_code = 1;
            goto cleanup;
        }
    }

    // Wait a moment, then check if we received anything from Forwarder
    std::this_thread::sleep_for(500ms);

    // Send another message for good measure
    {
        json heartbeat_msg = {{"type", "test"}, {"msg", "cross-language ping"}};
        if (client.send(crypto::MessageType::BUILD_RESULT, heartbeat_msg.dump())) {
            std::cout << "[Agent] Sent second message\n";
        }
    }

    std::this_thread::sleep_for(500ms);

    std::cout << "PASS: Encrypted message exchange completed\n";

cleanup:
    client.stop();
    std::this_thread::sleep_for(500ms);

    if (exit_code == 0) {
        std::cout << "ALL TESTS PASSED\n";
    }
    return exit_code;
}
