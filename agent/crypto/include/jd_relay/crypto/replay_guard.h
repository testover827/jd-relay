#pragma once

#include <cstdint>
#include <chrono>
#include <string>
#include <unordered_map>

namespace jd_relay::crypto {

/// Anti-replay protection using timestamp window + nonce cache.
/// Validation order: timestamp → nonce uniqueness.
class ReplayGuard {
public:
    /// @param window_seconds  Max age for timestamps (default 300 = 5 minutes).
    explicit ReplayGuard(int window_seconds = 300);

    /// Check and record a message.
    /// @param timestamp  Message timestamp (Unix milliseconds).
    /// @param nonce      Unique nonce string (base64).
    /// @return true if message is fresh (within window AND nonce not seen before).
    ///         On success, the nonce is added to the cache.
    bool check_and_record(int64_t timestamp, const std::string& nonce);

    /// Check only, without recording. Useful for pre-validation.
    bool is_within_window(int64_t timestamp) const;

    /// Check if a nonce has been seen.
    bool is_replay(const std::string& nonce) const;

    /// Manually add a nonce to the cache.
    void record_nonce(const std::string& nonce);

    /// Purge expired nonces from the cache.
    /// Called automatically on check_and_record, but can be called manually.
    void purge_expired();

    /// Get current time as Unix milliseconds.
    static int64_t now_ms();

    /// Number of nonces currently in the cache.
    size_t cache_size() const { return nonces_.size(); }

private:
    int window_seconds_;
    // nonce → expiry timestamp (ms)
    std::unordered_map<std::string, int64_t> nonces_;
};

} // namespace jd_relay::crypto
