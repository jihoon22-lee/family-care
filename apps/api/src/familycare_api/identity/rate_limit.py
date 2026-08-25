"""Bounded in-process throttling for local credential verification."""

from __future__ import annotations

import hashlib
import threading
from collections import deque
from datetime import datetime, timedelta


class LoginRateLimiter:
    """Limit repeated attempts without retaining submitted usernames."""

    def __init__(
        self,
        *,
        maximum_attempts: int = 5,
        window: timedelta = timedelta(minutes=5),
        maximum_keys: int = 1024,
    ) -> None:
        self.maximum_attempts = maximum_attempts
        self.window = window
        self.maximum_keys = maximum_keys
        self._attempts: dict[str, deque[datetime]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(username: str, client_key: str) -> str:
        value = f"{username.casefold()}\x00{client_key}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def is_limited(self, username: str, client_key: str, now: datetime) -> bool:
        key = self._key(username, client_key)
        with self._lock:
            attempts = self._attempts.get(key, deque())
            cutoff = now - self.window
            while attempts and attempts[0] < cutoff:
                attempts.popleft()
            if attempts:
                self._attempts[key] = attempts
            else:
                self._attempts.pop(key, None)
            return len(attempts) >= self.maximum_attempts

    def record_failure(self, username: str, client_key: str, now: datetime) -> None:
        key = self._key(username, client_key)
        with self._lock:
            if key not in self._attempts and len(self._attempts) >= self.maximum_keys:
                oldest = min(
                    self._attempts,
                    key=lambda item: self._attempts[item][-1],
                )
                self._attempts.pop(oldest, None)
            self._attempts.setdefault(key, deque()).append(now)

    def reset(self, username: str, client_key: str) -> None:
        with self._lock:
            self._attempts.pop(self._key(username, client_key), None)


__all__ = ["LoginRateLimiter"]
