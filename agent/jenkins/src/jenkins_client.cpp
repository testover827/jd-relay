/// JenkinsClient implementation — Phase 3.2

#include "jd_relay/jenkins/jenkins_client.h"

#include <nlohmann/json.hpp>
#include <curl/curl.h>

#include <cstdio>
#include <cstdlib>
#include <sstream>
#include <stdexcept>
#include <thread>

namespace jd_relay::jenkins {

using json = nlohmann::json;

// ── Helpers ──────────────────────────────────────────────────────

const char* to_string(BuildStatus s) {
    switch (s) {
        case BuildStatus::UNKNOWN:  return "UNKNOWN";
        case BuildStatus::QUEUED:   return "QUEUED";
        case BuildStatus::BUILDING: return "BUILDING";
        case BuildStatus::SUCCESS:  return "SUCCESS";
        case BuildStatus::FAILED:   return "FAILED";
        case BuildStatus::ABORTED:  return "ABORTED";
        case BuildStatus::NOT_BUILT:return "NOT_BUILT";
    }
    return "?";
}

static BuildStatus parse_build_result(const std::string& s) {
    if (s == "SUCCESS")  return BuildStatus::SUCCESS;
    if (s == "FAILURE")  return BuildStatus::FAILED;
    if (s == "ABORTED")  return BuildStatus::ABORTED;
    if (s == "NOT_BUILT") return BuildStatus::NOT_BUILT;
    if (s == "BUILDING") return BuildStatus::BUILDING;
    if (s == "QUEUED")   return BuildStatus::QUEUED;
    return BuildStatus::UNKNOWN;
}

// libcurl write callback
static size_t write_cb(void* data, size_t size, size_t nmemb, void* userp) {
    auto* buf = static_cast<std::string*>(userp);
    buf->append(static_cast<const char*>(data), size * nmemb);
    return size * nmemb;
}

// Simple HTTP GET/POST wrapper
static std::string http_get(CURL* curl, const std::string& url,
                             const std::string& user, const std::string& token,
                             std::string* error_out) {
    std::string response;
    curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    curl_easy_setopt(curl, CURLOPT_HTTPGET, 1L);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, write_cb);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
    curl_easy_setopt(curl, CURLOPT_USERNAME, user.c_str());
    curl_easy_setopt(curl, CURLOPT_PASSWORD, token.c_str());
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 30L);
    curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);

    CURLcode res = curl_easy_perform(curl);
    if (res != CURLE_OK) {
        if (error_out) *error_out = curl_easy_strerror(res);
        return "";
    }

    long http_code = 0;
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &http_code);
    if (http_code >= 400) {
        if (error_out) *error_out = "HTTP " + std::to_string(http_code);
        return "";
    }

    return response;
}

static std::string http_post(CURL* curl, const std::string& url,
                              const std::string& user, const std::string& token,
                              const std::string& post_data,
                              std::string* error_out) {
    std::string response;
    curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    curl_easy_setopt(curl, CURLOPT_POST, 1L);
    curl_easy_setopt(curl, CURLOPT_POSTFIELDS, post_data.c_str());
    curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, post_data.size());
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, write_cb);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
    curl_easy_setopt(curl, CURLOPT_USERNAME, user.c_str());
    curl_easy_setopt(curl, CURLOPT_PASSWORD, token.c_str());
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 30L);
    curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);

    CURLcode res = curl_easy_perform(curl);
    if (res != CURLE_OK) {
        if (error_out) *error_out = curl_easy_strerror(res);
        return "";
    }

    long http_code = 0;
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &http_code);
    if (http_code >= 400) {
        if (error_out) *error_out = "HTTP " + std::to_string(http_code);
        return "";
    }

    return response;
}

// ── Impl ─────────────────────────────────────────────────────────

struct JenkinsClient::Impl {
    std::string url;
    std::string user;
    std::string token;
    CURL*       curl{nullptr};

    Impl(std::string u, std::string usr, std::string tok)
        : url(std::move(u)), user(std::move(usr)), token(std::move(tok))
    {
        curl = curl_easy_init();
        if (!curl) throw std::runtime_error("curl_easy_init failed");
    }

    ~Impl() {
        if (curl) curl_easy_cleanup(curl);
    }

    std::string api_url(const std::string& path) const {
        // Ensure no double slash
        std::string base = url;
        while (!base.empty() && base.back() == '/') base.pop_back();
        return base + "/" + path;
    }
};

// ── Public API ───────────────────────────────────────────────────

JenkinsClient::JenkinsClient(std::string url, std::string user, std::string token)
    : impl_(std::make_unique<Impl>(std::move(url), std::move(user), std::move(token)))
{}

JenkinsClient::~JenkinsClient() = default;

BuildTriggerResult JenkinsClient::trigger_build(
        const std::string& job_name,
        const std::vector<std::pair<std::string, std::string>>& params) {
    BuildTriggerResult result;

    // Build the URL with parameters
    std::string path = "job/" + job_name + "/buildWithParameters";
    std::string url = impl_->api_url(path);

    // Build form-encoded parameter string
    std::string post_fields;
    for (const auto& [key, value] : params) {
        if (!post_fields.empty()) post_fields += "&";
        // URL-encode key and value (simple version — no full encoding)
        char* key_enc = curl_easy_escape(impl_->curl, key.c_str(), key.size());
        char* val_enc = curl_easy_escape(impl_->curl, value.c_str(), value.size());
        post_fields += key_enc;
        post_fields += "=";
        post_fields += val_enc;
        curl_free(key_enc);
        curl_free(val_enc);
    }

    std::string error;
    std::string resp = http_post(impl_->curl, url, impl_->user, impl_->token,
                                  post_fields, &error);
    if (!error.empty()) {
        result.error = error;
        return result;
    }

    // Jenkins returns 201 with Location header for queued builds
    result.ok = true;
    return result;
}

BuildStatusResult JenkinsClient::get_build_status(
        const std::string& job_name,
        int build_number) {
    BuildStatusResult result;
    result.build_number = build_number;

    std::string path = "job/" + job_name + "/" + std::to_string(build_number)
                     + "/api/json";
    std::string url = impl_->api_url(path);

    std::string error;
    std::string resp = http_get(impl_->curl, url, impl_->user, impl_->token, &error);
    if (!error.empty()) {
        result.error = error;
        return result;
    }

    try {
        auto j = json::parse(resp);
        result.status = parse_build_result(j.value("result", ""));
        result.url    = j.value("url", "");
        result.estimated_duration_ms = j.value("estimatedDuration", 0);

        // If no result yet, check if building
        if (result.status == BuildStatus::UNKNOWN && j.value("building", false)) {
            result.status = BuildStatus::BUILDING;
        }
        result.ok = true;
    } catch (const std::exception& e) {
        result.error = std::string("JSON parse error: ") + e.what();
    }

    return result;
}

BuildLogResult JenkinsClient::get_build_log(
        const std::string& job_name,
        int build_number,
        size_t max_bytes) {
    BuildLogResult result;

    std::string path = "job/" + job_name + "/" + std::to_string(build_number)
                     + "/consoleText";
    std::string url = impl_->api_url(path);

    std::string error;
    std::string resp = http_get(impl_->curl, url, impl_->user, impl_->token, &error);
    if (!error.empty()) {
        result.error = error;
        return result;
    }

    result.log_size_bytes = static_cast<int>(resp.size());
    if (max_bytes > 0 && resp.size() > max_bytes) {
        // Truncate: keep the LAST max_bytes (most recent output)
        result.log_text = resp.substr(resp.size() - max_bytes);
        result.truncated = true;
    } else {
        result.log_text = std::move(resp);
    }
    result.ok = true;
    return result;
}

BuildStatusResult JenkinsClient::poll_build(
        const std::string& job_name,
        int build_number,
        StatusCallback callback,
        std::chrono::milliseconds interval,
        std::chrono::seconds timeout) {
    auto start = std::chrono::steady_clock::now();

    while (true) {
        auto result = get_build_status(job_name, build_number);

        if (callback && result.ok) {
            callback(result);
        }

        // Terminal states
        if (result.status == BuildStatus::SUCCESS ||
            result.status == BuildStatus::FAILED ||
            result.status == BuildStatus::ABORTED ||
            result.status == BuildStatus::NOT_BUILT ||
            !result.ok) {
            return result;
        }

        // Check timeout
        if (timeout.count() > 0) {
            auto elapsed = std::chrono::steady_clock::now() - start;
            if (elapsed > timeout) {
                result.error = "Polling timeout";
                return result;
            }
        }

        std::this_thread::sleep_for(interval);
    }
}

bool JenkinsClient::detect_special_md_change(
        const std::string& repo_path,
        const std::string& base_ref) {
    // Execute: git diff --name-only <base_ref> HEAD
    std::string cmd = "git -C " + repo_path + " diff --name-only "
                    + base_ref + " HEAD 2>/dev/null";
    FILE* pipe = popen(cmd.c_str(), "r");
    if (!pipe) return false;

    std::string output;
    char buf[256];
    while (fgets(buf, sizeof(buf), pipe)) {
        output += buf;
    }
    pclose(pipe);

    // Check if special.md is in the changed files
    return output.find("special.md") != std::string::npos;
}

} // namespace jd_relay::jenkins
