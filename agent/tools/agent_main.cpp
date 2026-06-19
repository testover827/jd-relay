/// agent_main.cpp — JD-Relay Agent main entry point (tech debt resolution)
///
/// Reads agent.conf (TOML), connects to Forwarder via WebSocket,
/// handles BUILD_TRIGGER → Jenkins → BUILD_RESULT flow,
/// and monitors special.md for secondary review.
///
/// Replaces the hardcoded configuration in current tests/tools.

#include "jd_relay/agent/ws_client.h"
#include "jd_relay/jenkins/jenkins_client.h"
#include "jd_relay/crypto/ecdsa_signer.h"
#include "jd_relay/crypto/envelope.h"
#include "jd_relay/crypto/key_manager.h"

#include <nlohmann/json.hpp>

#include <atomic>
#include <chrono>
#include <csignal>
#include <iostream>
#include <fstream>
#include <sstream>
#include <string>
#include <thread>
#include <unordered_map>

namespace crypto = jd_relay::crypto;
using namespace jd_relay::agent;
using json = nlohmann::json;

// Global for signal handling
static std::atomic<bool> g_running{true};

static void signal_handler(int) {
    g_running.store(false);
}

// ── Simple INI-style config parser (TODO: replace with TOML) ─────

struct AgentConfig {
    std::string forwarder_host = "127.0.0.1";
    uint16_t    forwarder_port = 8000;
    std::string agent_id       = "agent-001";
    std::vector<std::string> projects;

    std::string ecdsa_priv_file;
    std::string ecdsa_pub_file;

    std::string jenkins_url;
    std::string jenkins_user;
    std::string jenkins_token;

    // Project → Jenkins job mapping
    std::unordered_map<std::string, std::string> job_mapping;

    int poll_interval_sec = 5;
    int build_timeout_sec = 3600;
    int max_concurrent    = 4;
};

static std::string trim(const std::string& s) {
    size_t start = s.find_first_not_of(" \t\r\n");
    if (start == std::string::npos) return "";
    size_t end = s.find_last_not_of(" \t\r\n");
    return s.substr(start, end - start + 1);
}

static AgentConfig load_config(const std::string& path) {
    AgentConfig cfg;
    std::ifstream f(path);
    if (!f) {
        std::cerr << "Warning: Cannot open config " << path << ", using defaults\n";
        return cfg;
    }

    std::string line, section;
    while (std::getline(f, line)) {
        line = trim(line);
        if (line.empty() || line[0] == '#') continue;
        if (line[0] == '[' && line.back() == ']') {
            section = line.substr(1, line.size() - 2);
            continue;
        }
        auto eq = line.find('=');
        if (eq == std::string::npos) continue;

        std::string key   = trim(line.substr(0, eq));
        std::string value = trim(line.substr(eq + 1));

        if (section == "forwarder") {
            if (key == "host") cfg.forwarder_host = value;
            else if (key == "port") cfg.forwarder_port = static_cast<uint16_t>(std::stoi(value));
        } else if (section == "crypto") {
            if (key == "ecdsa_private_key_file") cfg.ecdsa_priv_file = value;
            else if (key == "ecdsa_public_key_file") cfg.ecdsa_pub_file = value;
        } else if (section == "agent") {
            if (key == "agent_id") cfg.agent_id = value;
            else if (key == "projects") {
                // Comma-separated
                std::istringstream ss(value);
                std::string proj;
                while (std::getline(ss, proj, ',')) {
                    cfg.projects.push_back(trim(proj));
                }
            }
            else if (key == "max_concurrent_builds") cfg.max_concurrent = std::stoi(value);
        } else if (section == "jenkins") {
            if (key == "url") cfg.jenkins_url = value;
            else if (key == "user") cfg.jenkins_user = value;
            else if (key == "token") cfg.jenkins_token = value;
            else if (key == "poll_interval") cfg.poll_interval_sec = std::stoi(value);
            else if (key == "build_timeout") cfg.build_timeout_sec = std::stoi(value);
        } else if (section == "projects") {
            // project_name = jenkins_job_name
            cfg.job_mapping[section + "." + key] = value;
        }
    }

    return cfg;
}

// ── Main ─────────────────────────────────────────────────────────

int main(int argc, char* argv[]) {
    std::string config_path = "agent.conf";
    if (argc > 1) config_path = argv[1];

    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    auto cfg = load_config(config_path);

    std::cout << "[Agent] Starting: " << cfg.agent_id << "\n";
    std::cout << "[Agent] Forwarder: " << cfg.forwarder_host << ":" << cfg.forwarder_port << "\n";
    std::cout << "[Agent] Projects: ";
    for (size_t i = 0; i < cfg.projects.size(); ++i) {
        if (i > 0) std::cout << ", ";
        std::cout << cfg.projects[i];
    }
    std::cout << "\n";

    // Create Jenkins client
    std::unique_ptr<jd_relay::jenkins::JenkinsClient> jenkins;
    if (!cfg.jenkins_url.empty()) {
        jenkins = std::make_unique<jd_relay::jenkins::JenkinsClient>(
            cfg.jenkins_url, cfg.jenkins_user, cfg.jenkins_token);
        std::cout << "[Agent] Jenkins: " << cfg.jenkins_url << "\n";
    }

    // Message callback: handle BUILD_TRIGGER from Forwarder
    std::mutex mtx;
    auto msg_cb = [&](crypto::MessageType type, const std::string& plaintext) {
        std::lock_guard<std::mutex> lock(mtx);
        std::cout << "[Agent] Received: " << crypto::to_string(type)
                  << " payload=" << plaintext.substr(0, 200) << "\n";

        if (type == crypto::MessageType::BUILD_TRIGGER && jenkins) {
            try {
                auto j = json::parse(plaintext);
                std::string project = j.value("project", "");
                std::string branch  = j.value("branch", "main");
                std::string build_cmd = j.value("build_cmd", "");

                // Resolve job name from project mapping
                std::string job_name = project;  // Default: project = job name
                auto it = cfg.job_mapping.find(project);
                if (it != cfg.job_mapping.end()) {
                    job_name = it->second;
                }

                std::cout << "[Agent] Triggering Jenkins job: " << job_name << "\n";

                std::vector<std::pair<std::string, std::string>> params = {
                    {"ISSUE", j.value("issue", "")},
                    {"PROJECT", project},
                    {"BRANCH", branch},
                    {"BUILD_CMD", build_cmd},
                };

                auto result = jenkins->trigger_build(job_name, params);
                if (result.ok) {
                    std::cout << "[Agent] Build queued\n";
                } else {
                    std::cerr << "[Agent] Build trigger failed: " << result.error << "\n";
                }
            } catch (const std::exception& e) {
                std::cerr << "[Agent] Error processing BUILD_TRIGGER: " << e.what() << "\n";
            }
        }
    };

    auto conn_cb = [](bool connected) {
        std::cout << "[Agent] " << (connected ? "Connected" : "Disconnected") << "\n";
    };

    // Create and start WebSocket client
    WsClient client(
        cfg.forwarder_host, cfg.forwarder_port,
        cfg.agent_id, cfg.projects,
        cfg.ecdsa_priv_file, cfg.ecdsa_pub_file,
        msg_cb, conn_cb
    );

    client.start();

    std::cout << "[Agent] Running. Press Ctrl+C to stop.\n";

    // Main loop: poll special.md, handle reconnection, etc.
    while (g_running.load()) {
        std::this_thread::sleep_for(std::chrono::seconds(5));

        // TODO: Check special.md for changes
        // TODO: Poll in-progress builds and send BUILD_RESULT
    }

    std::cout << "[Agent] Shutting down...\n";
    client.stop();

    return 0;
}
