/// agent_main.cpp — JD-Relay Agent main entry point (Phase 3 complete)
///
/// Reads agent.conf (INI-style), connects to Forwarder via WebSocket,
/// handles BUILD_TRIGGER → Jenkins → BUILD_RESULT flow,
/// monitors special.md for secondary review,
/// and sends SENSITIVE_REVIEW_REQ when sensitive files change.
///
/// Build lifecycle:
///   Forwarder ──BUILD_TRIGGER──► Agent
///   Agent ──trigger──► Jenkins
///   Agent ──poll_build──► Jenkins (background thread)
///   Agent ──BUILD_RESULT──► Forwarder
///   (if special.md changed:)
///   Agent ──SENSITIVE_REVIEW_REQ──► Forwarder
///   Forwarder ──SECOND_REVIEW_RESULT──► Agent
///   Agent ──BUILD_RESULT (with review result)──► Forwarder

#include "jd_relay/agent/ws_client.h"
#include "jd_relay/jenkins/jenkins_client.h"
#include "jd_relay/crypto/ecdsa_signer.h"
#include "jd_relay/crypto/envelope.h"
#include "jd_relay/crypto/key_manager.h"

#include <nlohmann/json.hpp>

#include <atomic>
#include <chrono>
#include <csignal>
#include <functional>
#include <iostream>
#include <fstream>
#include <sstream>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace crypto = jd_relay::crypto;
using namespace jd_relay::agent;
using json = nlohmann::json;

// ── Globals ─────────────────────────────────────────────────────────────────────

static std::atomic<bool> g_running{true};

static void signal_handler(int) {
    g_running.store(false);
}

// ── Config ──────────────────────────────────────────────────────────────────────

struct AgentConfig {
    std::string forwarder_host  = "127.0.0.1";
    uint16_t    forwarder_port  = 8000;
    std::string agent_id        = "agent-001";
    std::vector<std::string> projects;

    std::string ecdsa_priv_file;
    std::string ecdsa_pub_file;

    std::string jenkins_url;
    std::string jenkins_user;
    std::string jenkins_token;

    // Project → Jenkins job mapping  (key: project_name, value: jenkins_job)
    std::unordered_map<std::string, std::string> job_mapping;

    int poll_interval_sec    = 5;
    int build_timeout_sec    = 3600;
    int max_concurrent       = 4;

    // Path to local git repo for special.md detection
    std::string repo_path;
    // How often to check special.md (seconds)
    int special_md_interval_sec = 30;
    // Base ref for git diff (default: origin/main)
    std::string special_md_base_ref = "origin/main";
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
        std::cerr << "Warning: Cannot open config " << path
                  << ", using defaults\n";
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
            else if (key == "port")
                cfg.forwarder_port = static_cast<uint16_t>(std::stoi(value));
        } else if (section == "crypto") {
            if (key == "ecdsa_private_key_file") cfg.ecdsa_priv_file = value;
            else if (key == "ecdsa_public_key_file")  cfg.ecdsa_pub_file  = value;
        } else if (section == "agent") {
            if (key == "agent_id") cfg.agent_id = value;
            else if (key == "projects") {
                std::istringstream ss(value);
                std::string proj;
                while (std::getline(ss, proj, ',')) {
                    cfg.projects.push_back(trim(proj));
                }
            } else if (key == "max_concurrent_builds")
                cfg.max_concurrent = std::stoi(value);
            else if (key == "repo_path")
                cfg.repo_path = value;
            else if (key == "special_md_interval_sec")
                cfg.special_md_interval_sec = std::stoi(value);
            else if (key == "special_md_base_ref")
                cfg.special_md_base_ref = value;
        } else if (section == "jenkins") {
            if (key == "url")             cfg.jenkins_url     = value;
            else if (key == "user")       cfg.jenkins_user    = value;
            else if (key == "token")      cfg.jenkins_token   = value;
            else if (key == "poll_interval")
                cfg.poll_interval_sec = std::stoi(value);
            else if (key == "build_timeout")
                cfg.build_timeout_sec = std::stoi(value);
        } else if (section == "projects") {
            cfg.job_mapping[key] = value;
        }
    }
    return cfg;
}

// ── Build tracking ─────────────────────────────────────────────────────────────

struct BuildContext {
    std::string issue;           // e.g. "ISSUE-123"
    std::string project;          // e.g. "myproject"
    std::string job_name;         // e.g. "myproject-build"
    int         build_number = 0;
    std::chrono::steady_clock::time_point trigger_time;
    bool       waiting_review = false;  // true after SENSITIVE_REVIEW_REQ sent
};

// Keyed by build_number (once known). Protected by g_builds_mutex.
static std::unordered_map<int, BuildContext> g_active_builds;
static std::mutex                           g_builds_mutex;

// Forward-declaration (WsClient pointer, set after construction)
static WsClient* g_ws_client = nullptr;

// ── Helpers ────────────────────────────────────────────────────────────────────

/// Resolve Jenkins job name from project name using config mapping.
static std::string resolve_job_name(
    const std::unordered_map<std::string, std::string>& mapping,
    const std::string& project)
{
    auto it = mapping.find(project);
    return (it != mapping.end()) ? it->second : project;
}

/// Build polling thread: poll Jenkins until build completes, then send BUILD_RESULT.
///
/// Creates its own JenkinsClient from cfg (thread-safe; no shared state).
/// If special.md changed (detected after build completes), send SENSITIVE_REVIEW_REQ
/// first and wait for SECOND_REVIEW_RESULT before sending BUILD_RESULT.
static void build_poll_thread(
    AgentConfig   cfg,
    BuildContext  ctx)
{
    using namespace std::chrono;
    using Status = jd_relay::jenkins::BuildStatus;

    // Create thread-local Jenkins client (thread-safe: no shared state)
    std::unique_ptr<jd_relay::jenkins::JenkinsClient> jk;
    if (!cfg.jenkins_url.empty()) {
        jk = std::make_unique<jd_relay::jenkins::JenkinsClient>(
            cfg.jenkins_url, cfg.jenkins_user, cfg.jenkins_token);
    } else {
        std::cerr << "[BuildPoll] Cannot poll: Jenkins URL not configured\n";
        return;
    }

    std::cout << "[BuildPoll] Started: issue=" << ctx.issue
              << " job=" << ctx.job_name
              << " #" << ctx.build_number << "\n";

    auto deadline = steady_clock::now() + seconds(cfg.build_timeout_sec);
    bool done = false;
    jd_relay::jenkins::BuildStatusResult final_status{};

    while (!done && g_running.load() && steady_clock::now() < deadline) {
        auto st = jk->get_build_status(ctx.job_name, ctx.build_number);
        if (!st.ok) {
            std::cerr << "[BuildPoll] Status query failed: "
                      << st.error << "\n";
            std::this_thread::sleep_for(seconds(cfg.poll_interval_sec));
            continue;
        }

        if (st.status == Status::SUCCESS ||
            st.status == Status::FAILED  ||
            st.status == Status::ABORTED ||
            st.status == Status::NOT_BUILT) {
            done = true;
            final_status = st;
            break;
        }

        std::this_thread::sleep_for(seconds(cfg.poll_interval_sec));
    }

    if (!done) {
        // Timeout
        std::cerr << "[BuildPoll] Timeout: issue=" << ctx.issue
                  << " build #" << ctx.build_number << "\n";
        final_status.status = Status::ABORTED;
        final_status.ok    = true;
    }

    // Fetch build log (last 8 KB)
    auto log_result = jk->get_build_log(ctx.job_name, ctx.build_number, 8192);

    // ── Check special.md ─────────────────────────────────────────────────────
    // After build completes, check if special.md changed in the repo.
    // If repo_path is configured and special.md changed, request secondary review.
    bool special_changed = false;
    if (!cfg.repo_path.empty()) {
        special_changed = jd_relay::jenkins::JenkinsClient::detect_special_md_change(
            cfg.repo_path, cfg.special_md_base_ref);
    }

    if (special_changed && g_ws_client) {
        std::cout << "[BuildPoll] special.md changed, sending SENSITIVE_REVIEW_REQ"
                  << " issue=" << ctx.issue << "\n";

        json review_req = {
            {"issue",    ctx.issue},
            {"project",  ctx.project},
            {"agent_id", cfg.agent_id},
            {"reason",   "special.md changed"}
        };
        g_ws_client->send(crypto::MessageType::SENSITIVE_REVIEW_REQ,
                           review_req.dump());

        // Mark build as waiting for review
        {
            std::lock_guard<std::mutex> lock(g_builds_mutex);
            auto it = g_active_builds.find(ctx.build_number);
            if (it != g_active_builds.end()) {
                it->second.waiting_review = true;
            }
        }
        // BUILD_RESULT will be sent after SECOND_REVIEW_RESULT arrives
        // (see msg_cb handler for SECOND_REVIEW_RESULT)
        return;
    }

    // ── Send BUILD_RESULT ────────────────────────────────────────────────────
    if (g_ws_client) {
        json result = {
            {"issue",         ctx.issue},
            {"project",       ctx.project},
            {"build_number",  ctx.build_number},
            {"status",        jd_relay::jenkins::to_string(final_status.status)},
            {"log_url",       final_status.url + "/console"},
            {"agent_id",      cfg.agent_id}
        };
        if (final_status.estimated_duration_ms > 0) {
            (*result)["duration_ms"] = final_status.estimated_duration_ms;
        }
        if (log_result.ok && !log_result.log_text.empty()) {
            (*result)["log_snippet"] = log_result.log_text.substr(0, 2000);
        }

        bool sent = g_ws_client->send(crypto::MessageType::BUILD_RESULT,
                                       result.dump());
        if (sent) {
            std::cout << "[BuildPoll] BUILD_RESULT sent: issue=" << ctx.issue
                      << " status=" << (*result)["status"].get<std::string>()
                      << "\n";
        } else {
            std::cerr << "[BuildPoll] Failed to send BUILD_RESULT for issue="
                      << ctx.issue << "\n";
        }
    }

    // Remove from active builds
    {
        std::lock_guard<std::mutex> lock(g_builds_mutex);
        g_active_builds.erase(ctx.build_number);
    }

    std::cout << "[BuildPoll] Finished: issue=" << ctx.issue << "\n";
}

// ── Main ──────────────────────────────────────────────────────────────────────

int main(int argc, char* argv[]) {
    std::string config_path = "agent.conf";
    if (argc > 1) config_path = argv[1];

    std::signal(SIGINT,  signal_handler);
    std::signal(SIGTERM, signal_handler);

    auto cfg = load_config(config_path);

    std::cout << "[Agent] Starting: " << cfg.agent_id << "\n";
    std::cout << "[Agent] Forwarder: " << cfg.forwarder_host << ":"
              << cfg.forwarder_port << "\n";
    std::cout << "[Agent] Projects: ";
    for (size_t i = 0; i < cfg.projects.size(); ++i) {
        if (i > 0) std::cout << ", ";
        std::cout << cfg.projects[i];
    }
    std::cout << "\n";
    if (!cfg.repo_path.empty()) {
        std::cout << "[Agent] Repo (special.md): " << cfg.repo_path << "\n";
    }

    // ── Message callback: handle messages from Forwarder ─────────────────────
    //
    // BUILD_TRIGGER  → trigger Jenkins, spawn poll thread
    // SECOND_REVIEW_RESULT → unblock waiting build, send BUILD_RESULT
    // HEARTBEAT      → (handled in WsClient::io_loop, just ACK)
    //
    auto msg_cb = [&](crypto::MessageType type, const std::string& plaintext) {
        std::cout << "[Agent] Received: " << crypto::to_string(type)
                  << " payload=" << plaintext.substr(0, 200) << "\n";

        if (type == crypto::MessageType::SECOND_REVIEW_RESULT) {
            // Forwarder says the secondary review is done.
            // Find the waiting build and send BUILD_RESULT.
            try {
                auto j = json::parse(plaintext);
                std::string issue  = j.value("issue", "");
                std::string result = j.value("result", "UNKNOWN");  // APPROVED / REJECTED

                std::cout << "[Agent] SECOND_REVIEW_RESULT: issue=" << issue
                          << " result=" << result << "\n";

                // Find the waiting build by issue.
                // Copy needed fields, remove from map, then release lock
                // before making network calls (avoid holding mutex long).
                int         wait_build_num = 0;
                std::string wait_issue;
                std::string wait_project;
                std::string wait_job;
                {
                    std::lock_guard<std::mutex> lock(g_builds_mutex);
                    for (auto& [bn, bctx] : g_active_builds) {
                        if (bctx.waiting_review && bctx.issue == issue) {
                            wait_build_num = bn;
                            wait_issue   = bctx.issue;
                            wait_project = bctx.project;
                            wait_job     = bctx.job_name;
                            break;
                        }
                    }
                    if (wait_build_num != 0) {
                        g_active_builds.erase(wait_build_num);
                    }
                }

                if (wait_build_num == 0) {
                    std::cerr << "[Agent] No waiting build found for issue="
                              << issue << "\n";
                    return;
                }

                // Create temporary Jenkins client (thread-local, no shared state)
                if (!cfg.jenkins_url.empty()) {
                    jd_relay::jenkins::JenkinsClient jk(
                        cfg.jenkins_url, cfg.jenkins_user, cfg.jenkins_token);

                    auto st  = jk.get_build_status(wait_job, wait_build_num);
                    auto log = jk.get_build_log(wait_job, wait_build_num, 8192);

                    json build_result = {
                        {"issue",         wait_issue},
                        {"project",       wait_project},
                        {"build_number",  wait_build_num},
                        {"status",        st.ok ? jd_relay::jenkins::to_string(st.status)
                                                : "UNKNOWN"},
                        {"second_review", result},
                        {"agent_id",      cfg.agent_id}
                    };
                    if (log.ok && !log.log_text.empty()) {
                        (*build_result)["log_snippet"] =
                            log.log_text.substr(0, 2000);
                    }

                    if (g_ws_client) {
                        g_ws_client->send(crypto::MessageType::BUILD_RESULT,
                                           build_result.dump());
                        std::cout << "[Agent] BUILD_RESULT sent (after review): "
                                  << "issue=" << wait_issue << "\n";
                    }
                } else {
                    std::cerr << "[Agent] Cannot re-fetch build: Jenkins not configured\n";
                }

            } catch (const std::exception& e) {
                std::cerr << "[Agent] Error processing SECOND_REVIEW_RESULT: "
                          << e.what() << "\n";
            }
            return;
        }

        if (type == crypto::MessageType::BUILD_TRIGGER) {
            // Create a thread-local Jenkins client for this trigger
            if (cfg.jenkins_url.empty()) {
                std::cerr << "[Agent] BUILD_TRIGGER: no Jenkins configured\n";
                return;
            }
            try {
                jd_relay::jenkins::JenkinsClient jk(
                    cfg.jenkins_url, cfg.jenkins_user, cfg.jenkins_token);

                auto j = json::parse(plaintext);
                std::string project   = j.value("project", "");
                std::string branch    = j.value("branch", "main");
                std::string build_cmd = j.value("build_cmd", "");
                std::string issue     = j.value("issue", "");

                std::string job_name =
                    resolve_job_name(cfg.job_mapping, project);

                std::cout << "[Agent] Triggering Jenkins job: " << job_name
                          << " (issue=" << issue << ")\n";

                std::vector<std::pair<std::string, std::string>> params = {
                    {"ISSUE",    issue},
                    {"PROJECT",  project},
                    {"BRANCH",   branch},
                    {"BUILD_CMD", build_cmd},
                };

                auto result = jk.trigger_build(job_name, params);
                if (!result.ok) {
                    std::cerr << "[Agent] Build trigger failed: "
                              << result.error << "\n";
                    if (g_ws_client) {
                        json err = {
                            {"issue",    issue},
                            {"project",  project},
                            {"status",   "FAILED"},
                            {"error",    result.error},
                            {"agent_id", cfg.agent_id}
                        };
                        g_ws_client->send(crypto::MessageType::BUILD_RESULT,
                                           err.dump());
                    }
                    return;
                }

                std::cout << "[Agent] Build queued: queue_id="
                          << result.queue_id
                          << " build_number=" << result.build_number << "\n";

                if (result.build_number == 0) {
                    std::cerr << "[Agent] Warning: build_number=0 (still in queue). "
                              << "Queue polling not yet implemented. "
                              << "BUILD_RESULT will not be sent automatically.\n";
                    return;
                }

                // Build context (captured by value for the poll thread)
                BuildContext ctx{
                    issue,
                    project,
                    job_name,
                    result.build_number,
                    std::chrono::steady_clock::now(),
                    false
                };
                {
                    std::lock_guard<std::mutex> lock(g_builds_mutex);
                    g_active_builds[result.build_number] = ctx;
                }

                // Spawn background thread to poll build status.
                // Each thread creates its own JenkinsClient from cfg (thread-safe).
                std::thread(build_poll_thread,
                            cfg,
                            ctx).detach();

            } catch (const std::exception& e) {
                std::cerr << "[Agent] Error processing BUILD_TRIGGER: "
                          << e.what() << "\n";
            }
        }
    };

    auto conn_cb = [](bool connected) {
        std::cout << "[Agent] "
                  << (connected ? "Connected" : "Disconnected") << "\n";
    };

    // Create and start WebSocket client
    WsClient client(
        cfg.forwarder_host, cfg.forwarder_port,
        cfg.agent_id, cfg.projects,
        cfg.ecdsa_priv_file, cfg.ecdsa_pub_file,
        msg_cb, conn_cb
    );
    g_ws_client = &client;

    client.start();

    std::cout << "[Agent] Running. Press Ctrl+C to stop.\n";

    // ── Main loop ────────────────────────────────────────────────────────────
    //
    // - Periodically check special.md (if repo_path configured)
    //   and send SENSITIVE_REVIEW_REQ for any new changes.
    //   (The actual per-build special.md check is done in build_poll_thread;
    //    this loop-level check is for repo-level monitoring.)
    //
    // - The WebSocket I/O runs in its own thread (inside WsClient).
    // - Build polling runs in detached threads.
    //
    auto last_special_check = std::chrono::steady_clock::now();

    while (g_running.load()) {
        std::this_thread::sleep_for(std::chrono::seconds(5));

        // ── Repo-level special.md monitoring (independent of builds) ────────
        if (!cfg.repo_path.empty()) {
            auto now = std::chrono::steady_clock::now();
            auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(
                now - last_special_check).count();

            if (elapsed >= cfg.special_md_interval_sec) {
                last_special_check = now;

                bool changed =
                    jd_relay::jenkins::JenkinsClient::detect_special_md_change(
                        cfg.repo_path, cfg.special_md_base_ref);

                if (changed) {
                    std::cout << "[Agent] Repo-level special.md change detected\n";

                    // Find the most recent active build to associate with
                    std::string active_issue;
                    std::string active_project;
                    {
                        std::lock_guard<std::mutex> lock(g_builds_mutex);
                        if (!g_active_builds.empty()) {
                            // Pick the most recently triggered build
                            auto newest = g_active_builds.begin();
                            for (auto it = g_active_builds.begin();
                                 it != g_active_builds.end(); ++it) {
                                if (it->second.trigger_time >
                                    newest->second.trigger_time) {
                                    newest = it;
                                }
                            }
                            active_issue   = newest->second.issue;
                            active_project = newest->second.project;
                        }
                    }

                    if (g_ws_client) {
                        json req = {
                            {"agent_id", cfg.agent_id},
                            {"reason",   "repo-level special.md change detected"}
                        };
                        if (!active_issue.empty()) {
                            (*req)["issue"]   = active_issue;
                            (*req)["project"] = active_project;
                        }
                        g_ws_client->send(
                            crypto::MessageType::SENSITIVE_REVIEW_REQ,
                            req.dump());
                        std::cout << "[Agent] SENSITIVE_REVIEW_REQ sent "
                                  << "(repo-level)\n";
                    }
                }
            }
        }
    }

    std::cout << "[Agent] Shutting down...\n";
    g_ws_client = nullptr;
    client.stop();

    // Wait for active build threads (best-effort)
    {
        std::lock_guard<std::mutex> lock(g_builds_mutex);
        if (!g_active_builds.empty()) {
            std::cout << "[Agent] Waiting for "
                      << g_active_builds.size()
                      << " active build(s) to finish...\n";
        }
    }
    // (In production, use a joinable thread pool instead of detach.)

    return 0;
}
