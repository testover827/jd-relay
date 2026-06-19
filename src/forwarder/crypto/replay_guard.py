"""Anti-replay protection — wire-compatible with C++ ReplayGuard.

Uses timestamp window (±5 min default) + nonce cache to detect replays.
Validation order: timestamp → nonce uniqueness (matches C++ exactly).
"""

import time


class ReplayGuard:
    """Anti-replay protection using timestamp window + nonce cache.

    Matches C++ ReplayGuard behavior:
    - Timestamp window: ±window_seconds (default 300 = 5 minutes)
    - Nonce cache: nonce → expiry timestamp (ms)
    - Auto-purge when cache exceeds 10,000 entries
    """

    def __init__(self, window_seconds: int = 300):
        self._window_seconds = window_seconds
        self._nonces: dict[str, int] = {}  # nonce → expiry timestamp (ms)

    @staticmethod
    def now_ms() -> int:
        """Get current time as Unix milliseconds."""
        return int(time.time() * 1000)

    def is_within_window(self, timestamp: int) -> bool:
        """Check if timestamp is within the acceptable window."""
        now = self.now_ms()
        window_ms = self._window_seconds * 1000
        return (timestamp >= now - window_ms) and (timestamp <= now + window_ms)

    def is_replay(self, nonce: str) -> bool:
        """Check if a nonce has already been seen."""
        return nonce in self._nonces

    def record_nonce(self, nonce: str) -> None:
        """Add a nonce to the cache with an expiry timestamp."""
        expiry = self.now_ms() + self._window_seconds * 1000
        self._nonces[nonce] = expiry

    def check_and_record(self, timestamp: int, nonce: str) -> bool:
        """Check timestamp window + nonce uniqueness, record if fresh.

        Returns True if message is fresh (within window AND nonce not seen).
        On success, the nonce is recorded. Auto-purges when > 10k entries.
        """
        if not self.is_within_window(timestamp):
            return False
        if self.is_replay(nonce):
            return False

        self.record_nonce(nonce)

        if len(self._nonces) > 10000:
            self.purge_expired()

        return True

    def purge_expired(self) -> None:
        """Remove all nonces whose expiry time has passed."""
        now = self.now_ms()
        self._nonces = {
            nonce: expiry
            for nonce, expiry in self._nonces.items()
            if expiry >= now
        }

    @property
    def cache_size(self) -> int:
        """Number of nonces currently cached."""
        return len(self._nonces)
