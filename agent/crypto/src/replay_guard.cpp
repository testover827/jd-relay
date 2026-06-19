#include "jd_relay/crypto/replay_guard.h"
#include <chrono>
#include <algorithm>

namespace jd_relay::crypto {

ReplayGuard::ReplayGuard(int window_seconds)
    : window_seconds_(window_seconds) {}

int64_t ReplayGuard::now_ms() {
    auto now = std::chrono::system_clock::now();
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        now.time_since_epoch()).count();
}

bool ReplayGuard::is_within_window(int64_t timestamp) const {
    int64_t now = now_ms();
    int64_t window_ms = static_cast<int64_t>(window_seconds_) * 1000;
    // Check timestamp is within [now - window, now + window]
    // We allow a small future skew (±window) to handle clock drift
    return (timestamp >= now - window_ms) && (timestamp <= now + window_ms);
}

bool ReplayGuard::is_replay(const std::string& nonce) const {
    return nonces_.find(nonce) != nonces_.end();
}

void ReplayGuard::record_nonce(const std::string& nonce) {
    int64_t expiry = now_ms() + static_cast<int64_t>(window_seconds_) * 1000;
    nonces_[nonce] = expiry;
}

bool ReplayGuard::check_and_record(int64_t timestamp, const std::string& nonce) {
    // 1. Check timestamp window
    if (!is_within_window(timestamp)) {
        return false;
    }

    // 2. Check nonce uniqueness
    if (is_replay(nonce)) {
        return false;
    }

    // 3. Record nonce
    record_nonce(nonce);

    // 4. Purge expired nonces periodically
    if (nonces_.size() > 10000) {
        purge_expired();
    }

    return true;
}

void ReplayGuard::purge_expired() {
    int64_t now = now_ms();
    for (auto it = nonces_.begin(); it != nonces_.end(); ) {
        if (it->second < now) {
            it = nonces_.erase(it);
        } else {
            ++it;
        }
    }
}

} // namespace jd_relay::crypto
