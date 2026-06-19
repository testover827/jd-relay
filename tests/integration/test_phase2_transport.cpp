/// @file test_phase2_transport.cpp
/// Phase 2 integration tests: ECDH handshake, encrypted message exchange,
/// project routing, and auto-reconnect.

#include <gtest/gtest.h>

#include "jd_relay/forwarder/ws_server.h"
#include "jd_relay/agent/ws_client.h"
#include "jd_relay/crypto/ecdsa_signer.h"
#include "jd_relay/crypto/envelope.h"

#include <nlohmann/json.hpp>

#include <atomic>
#include <chrono>
#include <filesystem>
#include <mutex>
#include <string>
#include <thread>

namespace fs = std::filesystem;
namespace crypto = jd_relay::crypto;
using namespace jd_relay::forwarder;
using namespace jd_relay::agent;
using namespace std::chrono_literals;

// ──────────────────────────────────────────────────────────────────
// Test fixture: generates ECDSA key pairs in a temp directory
// ──────────────────────────────────────────────────────────────────
class Phase2TransportTest : public ::testing::Test {
protected:
    fs::path tmp_dir;
    std::string fwd_priv, fwd_pub, agt_priv, agt_pub;

    void SetUp() override {
        tmp_dir = fs::temp_directory_path() / "jd_test_phase2";
        fs::create_directories(tmp_dir);

        fwd_priv = (tmp_dir / "fwd_priv.pem").string();
        fwd_pub  = (tmp_dir / "fwd_pub.pem").string();
        agt_priv = (tmp_dir / "agt_priv.pem").string();
        agt_pub  = (tmp_dir / "agt_pub.pem").string();

        crypto::EcdsaSigner::generate_keypair(fwd_priv, fwd_pub);
        crypto::EcdsaSigner::generate_keypair(agt_priv, agt_pub);
    }

    void TearDown() override {
        std::error_code ec;
        fs::remove_all(tmp_dir, ec);
    }

    /// Wait until condition returns true, with timeout.
    template<typename Fn, typename Dur = std::chrono::seconds>
    bool wait_for(Fn cond, Dur timeout = 10s,
                  std::chrono::milliseconds interval = 50ms) {
        auto deadline = std::chrono::steady_clock::now() + timeout;
        while (std::chrono::steady_clock::now() < deadline) {
            if (cond()) return true;
            std::this_thread::sleep_for(interval);
        }
        return cond();
    }
};

// ──────────────────────────────────────────────────────────────────
// Test 1: Basic handshake + bidirectional encrypted message exchange
// ──────────────────────────────────────────────────────────────────
TEST_F(Phase2TransportTest, HandshakeAndMessageExchange) {
    // Track received messages
    std::mutex mtx;
    std::string server_received, client_received;
    bool server_got_msg = false, client_got_msg = false;
    std::atomic<bool> client_connected{false};

    auto server_cb = [&](const std::string& agent_id,
                          crypto::MessageType type,
                          const std::string& plaintext) {
        std::lock_guard<std::mutex> lock(mtx);
        server_received = plaintext;
        server_got_msg = true;
    };

    auto client_cb = [&](crypto::MessageType type,
                          const std::string& plaintext) {
        std::lock_guard<std::mutex> lock(mtx);
        client_received = plaintext;
        client_got_msg = true;
    };

    auto conn_cb = [&](bool connected) {
        if (connected) client_connected.store(true);
        else           client_connected.store(false);
    };

    // Start server on a random port
    WsServer server(0, fwd_priv, fwd_pub, server_cb);
    server.start();
    uint16_t port = server.port();
    ASSERT_GT(port, 0u);

    // Start client
    WsClient client("127.0.0.1", port, "agent-001", {"proj-a"},
                    agt_priv, agt_pub, client_cb, conn_cb);
    client.start();

    // Wait for connection + agent registration
    ASSERT_TRUE(wait_for([&]() { return client_connected.load(); }, 10s));
    ASSERT_TRUE(wait_for([&]() { return server.manager().count() > 0; }, 5s));

    // ── Server → Client: BUILD_TRIGGER ──────────────────────────
    std::string trigger = R"({"work_order_id":"WO-001","issue":"ISS-001","project":"proj-a","branch":"main","build_cmd":"make all"})";
    ASSERT_TRUE(server.send_to_agent("agent-001",
                                     crypto::MessageType::BUILD_TRIGGER,
                                     trigger));

    ASSERT_TRUE(wait_for([&]() { return client_got_msg; }, 5s));
    {
        std::lock_guard<std::mutex> lock(mtx);
        auto expected = nlohmann::json::parse(trigger);
        auto actual   = nlohmann::json::parse(client_received);
        EXPECT_EQ(expected, actual);
    }

    // ── Client → Server: BUILD_RESULT ───────────────────────────
    client_got_msg = false;
    server_got_msg = false;

    std::string result = R"({"work_order_id":"WO-001","build_number":42,"status":"SUCCESS","log_url":"http://jenkins/job/42/log"})";
    ASSERT_TRUE(client.send(crypto::MessageType::BUILD_RESULT, result));

    ASSERT_TRUE(wait_for([&]() { return server_got_msg; }, 5s));
    {
        std::lock_guard<std::mutex> lock(mtx);
        auto expected = nlohmann::json::parse(result);
        auto actual   = nlohmann::json::parse(server_received);
        EXPECT_EQ(expected, actual);
    }

    // Cleanup
    client.stop();
    server.stop();
}

// ──────────────────────────────────────────────────────────────────
// Test 2: Project-based routing (1:N)
// ──────────────────────────────────────────────────────────────────
TEST_F(Phase2TransportTest, ProjectRouting) {
    std::mutex mtx;
    std::string client_received;
    bool got_msg = false;
    std::atomic<bool> connected{false};

    auto client_cb = [&](crypto::MessageType type,
                          const std::string& plaintext) {
        std::lock_guard<std::mutex> lock(mtx);
        client_received = plaintext;
        got_msg = true;
    };

    WsServer server(0, fwd_priv, fwd_pub,
                    [](const std::string&, crypto::MessageType,
                       const std::string&) {});
    server.start();
    uint16_t port = server.port();

    // Client registers for projects "alpha" and "beta"
    WsClient client("127.0.0.1", port, "agent-002", {"alpha", "beta"},
                    agt_priv, agt_pub, client_cb,
                    [&](bool c) { connected.store(c); });
    client.start();

    ASSERT_TRUE(wait_for([&]() { return connected.load(); }, 10s));
    ASSERT_TRUE(wait_for([&]() { return server.manager().count() > 0; }, 5s));

    // Send to "alpha" — should succeed
    got_msg = false;
    ASSERT_TRUE(server.send_to_project("alpha",
                                        crypto::MessageType::BUILD_TRIGGER,
                                        R"({"project":"alpha"})"));
    ASSERT_TRUE(wait_for([&]() { return got_msg; }, 5s));

    // Send to "beta" — should succeed
    got_msg = false;
    ASSERT_TRUE(server.send_to_project("beta",
                                        crypto::MessageType::BUILD_TRIGGER,
                                        R"({"project":"beta"})"));
    ASSERT_TRUE(wait_for([&]() { return got_msg; }, 5s));

    // Send to "gamma" (not registered) — should fail
    EXPECT_FALSE(server.send_to_project("gamma",
                                         crypto::MessageType::BUILD_TRIGGER,
                                         R"({"project":"gamma"})"));

    // Cleanup
    client.stop();
    server.stop();
}

// ──────────────────────────────────────────────────────────────────
// Test 3: Auto-reconnect after server restart
// ──────────────────────────────────────────────────────────────────
TEST_F(Phase2TransportTest, AutoReconnectAfterServerRestart) {
    std::mutex mtx;
    std::string client_received;
    int msg_count = 0;
    std::atomic<int> connect_count{0};
    std::atomic<int> disconnect_count{0};

    auto client_cb = [&](crypto::MessageType type,
                          const std::string& plaintext) {
        std::lock_guard<std::mutex> lock(mtx);
        client_received = plaintext;
        msg_count++;
    };

    auto conn_cb = [&](bool connected) {
        if (connected) connect_count.fetch_add(1);
        else           disconnect_count.fetch_add(1);
    };

    // Start server
    WsServer* server = new WsServer(0, fwd_priv, fwd_pub,
        [](const std::string&, crypto::MessageType,
           const std::string&) {});
    server->start();
    uint16_t port = server->port();

    // Start client
    WsClient client("127.0.0.1", port, "agent-003", {"proj-x"},
                    agt_priv, agt_pub, client_cb, conn_cb);
    client.start();

    // Wait for initial connection
    ASSERT_TRUE(wait_for([&]() { return connect_count.load() == 1; }, 10s));
    ASSERT_TRUE(wait_for([&]() { return server->manager().count() > 0; }, 5s));

    // Send a message — should work
    msg_count = 0;
    ASSERT_TRUE(server->send_to_agent("agent-003",
                                       crypto::MessageType::BUILD_TRIGGER,
                                       R"({"msg":"first"})"));
    ASSERT_TRUE(wait_for([&]() { return msg_count > 0; }, 5s));

    // Stop server — client should detect disconnect
    server->stop();
    delete server;
    server = nullptr;

    ASSERT_TRUE(wait_for([&]() { return disconnect_count.load() >= 1; }, 10s));

    // Restart server on the SAME port
    server = new WsServer(port, fwd_priv, fwd_pub,
        [](const std::string&, crypto::MessageType,
           const std::string&) {});
    server->start();

    // Wait for client to reconnect (connect_count should be 2)
    ASSERT_TRUE(wait_for([&]() { return connect_count.load() >= 2; }, 15s));
    ASSERT_TRUE(wait_for([&]() { return server->manager().count() > 0; }, 5s));

    // Send another message — should work after reconnect
    msg_count = 0;
    ASSERT_TRUE(server->send_to_agent("agent-003",
                                       crypto::MessageType::BUILD_TRIGGER,
                                       R"({"msg":"second"})"));
    ASSERT_TRUE(wait_for([&]() { return msg_count > 0; }, 5s));

    {
        std::lock_guard<std::mutex> lock(mtx);
        EXPECT_EQ(client_received, R"({"msg":"second"})");
    }

    // Cleanup
    client.stop();
    server->stop();
    delete server;
}

// ──────────────────────────────────────────────────────────────────
// Test 4: Multiple agents connected simultaneously (1:N)
// ──────────────────────────────────────────────────────────────────
TEST_F(Phase2TransportTest, MultipleAgents) {
    std::atomic<int> total_msgs{0};
    std::mutex mtx;
    std::map<std::string, std::string> received_by_agent;

    auto server_cb = [&](const std::string& agent_id,
                          crypto::MessageType type,
                          const std::string& plaintext) {
        std::lock_guard<std::mutex> lock(mtx);
        received_by_agent[agent_id] = plaintext;
        total_msgs.fetch_add(1);
    };

    WsServer server(0, fwd_priv, fwd_pub, server_cb);
    server.start();
    uint16_t port = server.port();

    // Two agents with different projects
    WsClient client1("127.0.0.1", port, "agent-A", {"proj-1"},
                     agt_priv, agt_pub,
                     [](crypto::MessageType, const std::string&) {});
    WsClient client2("127.0.0.1", port, "agent-B", {"proj-2"},
                     agt_priv, agt_pub,
                     [](crypto::MessageType, const std::string&) {});

    client1.start();
    client2.start();

    // Wait for both to connect and register
    ASSERT_TRUE(wait_for([&]() { return server.manager().count() >= 2; }, 10s));

    auto agents = server.manager().list_agents();
    ASSERT_EQ(agents.size(), 2u);

    // Send to each agent by project
    ASSERT_TRUE(server.send_to_project("proj-1",
                                        crypto::MessageType::BUILD_TRIGGER,
                                        R"({"target":"agent-A"})"));
    ASSERT_TRUE(server.send_to_project("proj-2",
                                        crypto::MessageType::BUILD_TRIGGER,
                                        R"({"target":"agent-B"})"));

    // Each agent should respond
    ASSERT_TRUE(client1.send(crypto::MessageType::BUILD_RESULT,
                              R"({"from":"agent-A"})"));
    ASSERT_TRUE(client2.send(crypto::MessageType::BUILD_RESULT,
                              R"({"from":"agent-B"})"));

    ASSERT_TRUE(wait_for([&]() { return total_msgs.load() >= 2; }, 5s));

    {
        std::lock_guard<std::mutex> lock(mtx);
        EXPECT_EQ(received_by_agent["agent-A"], R"({"from":"agent-A"})");
        EXPECT_EQ(received_by_agent["agent-B"], R"({"from":"agent-B"})");
    }

    client1.stop();
    client2.stop();
    server.stop();
}
