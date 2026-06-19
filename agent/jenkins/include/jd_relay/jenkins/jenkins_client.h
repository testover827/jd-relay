/// Jenkins REST API client — Phase 3.2
///
/// Provides:
/// - Build trigger with parameters
/// - Build status polling
/// - Build log retrieval and truncation
/// - special.md change detection via git diff
///
/// Uses libcurl for HTTP. Thread-safe for status polling.

#pragma once

#include <chrono>
#include <functional>
#include <string>
#include <vector>
#include <memory>

namespace jd_relay::jenkins {

/// Result of a build trigger request.
struct BuildTriggerResult {
    bool   ok{false};
    int    queue_id{0};
    int    build_number{0};
    std::string error;
    std::string queue_url;
};

/// Build status returned by polling.
enum class BuildStatus {
    UNKNOWN,
    QUEUED,
    BUILDING,
    SUCCESS,
    FAILED,
    ABORTED,
    NOT_BUILT,
};

const char* to_string(BuildStatus s);

/// Result of a build status query.
struct BuildStatusResult {
    bool        ok{false};
    BuildStatus status{BuildStatus::UNKNOWN};
    int         build_number{0};
    int         estimated_duration_ms{0};
    std::string error;
    std::string url;
};

/// Result of a build log retrieval.
struct BuildLogResult {
    bool        ok{false};
    std::string log_text;
    int         log_size_bytes{0};
    bool        truncated{false};
    std::string error;
};

/// Callback type for async status polling.
using StatusCallback = std::function<void(const BuildStatusResult&)>;
using LogCallback    = std::function<void(const BuildLogResult&)>;

/// Jenkins REST API client.
class JenkinsClient {
public:
    /// @param jenkins_url   Base URL (e.g. "https://jenkins.example.com")
    /// @param user          API username
    /// @param token         API token
    JenkinsClient(std::string jenkins_url,
                  std::string user,
                  std::string token);

    ~JenkinsClient();

    // Non-copyable
    JenkinsClient(const JenkinsClient&) = delete;
    JenkinsClient& operator=(const JenkinsClient&) = delete;

    /// Trigger a parameterized build.
    /// @param job_name   Jenkins job/pipeline name
    /// @param params     Build parameters [{key, value}, ...]
    /// @return           Result with queue ID and build number
    BuildTriggerResult trigger_build(
        const std::string& job_name,
        const std::vector<std::pair<std::string, std::string>>& params);

    /// Query build status.
    /// @param job_name      Job name
    /// @param build_number  Build number
    BuildStatusResult get_build_status(
        const std::string& job_name,
        int build_number);

    /// Get build console output.
    /// @param job_name      Job name
    /// @param build_number  Build number
    /// @param max_bytes     Maximum bytes to fetch (0 = no limit)
    BuildLogResult get_build_log(
        const std::string& job_name,
        int build_number,
        size_t max_bytes = 0);

    /// Poll build status until completion, calling callback each time.
    /// @param job_name       Job name
    /// @param build_number   Build number
    /// @param callback       Called with each status update
    /// @param interval       Polling interval
    /// @param timeout        Maximum polling time (0 = no timeout)
    /// @return Final status
    BuildStatusResult poll_build(
        const std::string& job_name,
        int build_number,
        StatusCallback callback = nullptr,
        std::chrono::milliseconds interval = std::chrono::milliseconds(5000),
        std::chrono::seconds timeout = std::chrono::seconds(0));

    /// Check if special.md has changed in the given git repo.
    /// Compares HEAD against origin/main or specified base ref.
    /// @param repo_path   Path to git repository
    /// @param base_ref    Base ref to diff against (default: "origin/main")
    /// @return true if special.md was changed
    static bool detect_special_md_change(
        const std::string& repo_path,
        const std::string& base_ref = "origin/main");

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace jd_relay::jenkins
