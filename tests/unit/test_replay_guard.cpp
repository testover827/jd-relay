// test_replay_guard.cpp — ReplayGuard timestamp window + nonce tests
#include <gtest/gtest.h>
#include "jd_relay/crypto/replay_guard.h"
#include <thread>
#include <chrono>

using namespace jd_relay::crypto;

TEST(ReplayGuardTest, FreshMessageAccepted) {
    ReplayGuard guard;
    int64_t now = ReplayGuard::now_ms();
    std::string nonce = "test-nonce-001";

    EXPECT_TRUE(guard.check_and_record(now, nonce));
}

TEST(ReplayGuardTest, ExpiredTimestampRejected) {
    ReplayGuard guard(300);  // 5 min window
    int64_t old = ReplayGuard::now_ms() - 600 * 1000;  // 10 minutes ago
    std::string nonce = "test-nonce-002";

    EXPECT_FALSE(guard.check_and_record(old, nonce));
}

TEST(ReplayGuardTest, FutureTimestampRejected) {
    ReplayGuard guard(300);
    int64_t future = ReplayGuard::now_ms() + 600 * 1000;  // 10 min in future
    std::string nonce = "test-nonce-003";

    EXPECT_FALSE(guard.check_and_record(future, nonce));
}

TEST(ReplayGuardTest, DuplicateNonceRejected) {
    ReplayGuard guard;
    int64_t now = ReplayGuard::now_ms();
    std::string nonce = "duplicate-nonce";

    // First time: accepted
    EXPECT_TRUE(guard.check_and_record(now, nonce));

    // Second time: rejected (replay)
    EXPECT_FALSE(guard.check_and_record(now, nonce));
}

TEST(ReplayGuardTest, DifferentNoncesAccepted) {
    ReplayGuard guard;
    int64_t now = ReplayGuard::now_ms();

    EXPECT_TRUE(guard.check_and_record(now, "nonce-a"));
    EXPECT_TRUE(guard.check_and_record(now, "nonce-b"));
    EXPECT_TRUE(guard.check_and_record(now, "nonce-c"));
}

TEST(ReplayGuardTest, IsWithinWindow) {
    ReplayGuard guard(60);  // 1 min window
    int64_t now = ReplayGuard::now_ms();

    EXPECT_TRUE(guard.is_within_window(now));
    EXPECT_TRUE(guard.is_within_window(now - 30 * 1000));  // 30s ago
    EXPECT_TRUE(guard.is_within_window(now + 30 * 1000));  // 30s future
    EXPECT_FALSE(guard.is_within_window(now - 120 * 1000)); // 2 min ago
    EXPECT_FALSE(guard.is_within_window(now + 120 * 1000)); // 2 min future
}

TEST(ReplayGuardTest, PurgeExpired) {
    ReplayGuard guard(1);  // 1 second window
    int64_t now = ReplayGuard::now_ms();

    guard.record_nonce("old-nonce");
    EXPECT_EQ(guard.cache_size(), 1u);

    // Wait for expiry
    std::this_thread::sleep_for(std::chrono::milliseconds(1100));

    guard.purge_expired();
    EXPECT_EQ(guard.cache_size(), 0u);
}

TEST(ReplayGuardTest, BorderlineTimestamp) {
    ReplayGuard guard(300);
    int64_t now = ReplayGuard::now_ms();

    // Exactly at window boundary (±5 min) should be accepted
    int64_t boundary_past = now - 300 * 1000;
    int64_t boundary_future = now + 300 * 1000;

    // These might be slightly outside due to time elapsed, so test close to boundary
    EXPECT_TRUE(guard.is_within_window(boundary_past));
    EXPECT_TRUE(guard.is_within_window(boundary_future));
}
