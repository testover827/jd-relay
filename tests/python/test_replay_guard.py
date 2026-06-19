"""Tests for ReplayGuard. Mirrors C++ test_replay_guard.cpp."""

import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import pytest
from forwarder.crypto.replay_guard import ReplayGuard


class TestReplayGuard:
    """Matches C++ ReplayGuard test cases."""

    @pytest.fixture
    def guard(self):
        return ReplayGuard(window_seconds=300)

    def test_fresh_message_accepted(self, guard):
        now = ReplayGuard.now_ms()
        assert guard.check_and_record(now, "nonce-001")

    def test_expired_timestamp_rejected(self, guard):
        # Timestamp from 10 minutes ago
        past = ReplayGuard.now_ms() - 10 * 60 * 1000
        assert not guard.check_and_record(past, "nonce-old")

    def test_future_timestamp_rejected(self, guard):
        # Timestamp from 10 minutes in the future
        future = ReplayGuard.now_ms() + 10 * 60 * 1000
        assert not guard.check_and_record(future, "nonce-future")

    def test_duplicate_nonce_rejected(self, guard):
        now = ReplayGuard.now_ms()
        assert guard.check_and_record(now, "nonce-dup")
        assert guard.is_replay("nonce-dup")
        assert not guard.check_and_record(now, "nonce-dup")

    def test_different_nonces_accepted(self, guard):
        now = ReplayGuard.now_ms()
        assert guard.check_and_record(now, "nonce-1")
        assert guard.check_and_record(now, "nonce-2")
        assert guard.check_and_record(now, "nonce-3")

    def test_boundary_timestamps(self, guard):
        window_ms = 300 * 1000
        now = ReplayGuard.now_ms()

        # Exactly at the boundary — should be accepted
        assert guard.check_and_record(now - window_ms, "nonce-lower")
        assert guard.check_and_record(now + window_ms, "nonce-upper")

        # Just outside — should be rejected
        just_outside_lower = now - window_ms - 1
        just_outside_upper = now + window_ms + 1
        # Create new guards to avoid nonce collision
        g2 = ReplayGuard(window_seconds=300)
        assert not g2.check_and_record(just_outside_lower, "nonce-outside-1")
        g3 = ReplayGuard(window_seconds=300)
        assert not g3.check_and_record(just_outside_upper, "nonce-outside-2")

    def test_purge_expired(self, guard):
        # Manually insert expired nonces
        past = ReplayGuard.now_ms() - 10 * 60 * 1000
        guard._nonces["expired-1"] = past
        guard._nonces["expired-2"] = past - 1
        guard._nonces["fresh"] = ReplayGuard.now_ms() + 3600 * 1000

        guard.purge_expired()
        assert "expired-1" not in guard._nonces
        assert "expired-2" not in guard._nonces
        assert "fresh" in guard._nonces

    def test_cache_size(self, guard):
        assert guard.cache_size == 0
        now = ReplayGuard.now_ms()
        guard.check_and_record(now, "n1")
        assert guard.cache_size == 1
        guard.check_and_record(now, "n2")
        assert guard.cache_size == 2

    def test_is_within_window(self, guard):
        now = ReplayGuard.now_ms()
        assert guard.is_within_window(now)
        assert guard.is_within_window(now - 60 * 1000)
        assert guard.is_within_window(now + 60 * 1000)
        assert not guard.is_within_window(now - 10 * 60 * 1000)
        assert not guard.is_within_window(now + 10 * 60 * 1000)
