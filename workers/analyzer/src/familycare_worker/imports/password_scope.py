"""Batch-local PDF password ownership with best-effort buffer disposal."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock
from uuid import UUID

MAX_PASSWORD_BYTES = 8 * 1024


class PasswordScopeDisposed(RuntimeError):
    """Raised when a terminal scope is reused."""


class PasswordScope:
    """Own one mutable UTF-8 password buffer for every item in a batch."""

    def __init__(self, *, batch_id: UUID, password: str, expires_at: datetime) -> None:
        if not isinstance(batch_id, UUID) or batch_id.int == 0:
            raise ValueError("invalid password scope")
        self.batch_id = batch_id
        self._lock = Lock()
        self._buffer: bytearray | None = None
        self._expires_at = expires_at
        self._disposed = False
        self.replace(password, expires_at=expires_at)

    def __repr__(self) -> str:
        state = "disposed" if self._disposed else "active"
        return f"PasswordScope(batch_id={self.batch_id!r}, state={state!r})"

    @staticmethod
    def _validate(password: str, expires_at: datetime) -> bytearray:
        if not isinstance(password, str):
            raise ValueError("invalid password scope")
        encoded = password.encode("utf-8")
        if not encoded or len(encoded) > MAX_PASSWORD_BYTES:
            raise ValueError("invalid password scope")
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("invalid password scope")
        return bytearray(encoded)

    @staticmethod
    def _wipe(buffer: bytearray | None) -> None:
        if buffer is not None:
            buffer[:] = b"\x00" * len(buffer)
            buffer.clear()

    def password_for(self, item_id: UUID) -> str | None:
        if not isinstance(item_id, UUID) or item_id.int == 0:
            raise ValueError("invalid password scope")
        with self._lock:
            if self._disposed or self._buffer is None or self._expires_at <= datetime.now(UTC):
                self._wipe(self._buffer)
                self._buffer = None
                return None
            return self._buffer.decode("utf-8")

    def replace(self, password: str, *, expires_at: datetime) -> None:
        replacement = self._validate(password, expires_at)
        with self._lock:
            if self._disposed:
                self._wipe(replacement)
                raise PasswordScopeDisposed("password scope is disposed")
            old = self._buffer
            self._buffer = replacement
            self._expires_at = expires_at.astimezone(UTC)
            self._wipe(old)

    def dispose(self) -> None:
        with self._lock:
            self._wipe(self._buffer)
            self._buffer = None
            self._disposed = True


__all__ = ["MAX_PASSWORD_BYTES", "PasswordScope", "PasswordScopeDisposed"]
