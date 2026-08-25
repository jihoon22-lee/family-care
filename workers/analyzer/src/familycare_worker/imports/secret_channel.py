"""Worker-owned Unix-domain receiver for one-time batch secrets."""

from __future__ import annotations

import hmac
import json
import os
import socket
import stat
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any
from uuid import UUID

from familycare_worker.imports.password_scope import PasswordScope

MAX_FRAME_BYTES = 64 * 1024
MAX_PASSWORD_BYTES = 8 * 1024
_ACK = b"\x00"


class SecretChannelError(RuntimeError):
    """Sanitized secret-channel failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class _Entry:
    batch_id: UUID
    handoff_id: UUID
    password: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True)
class _RegistryEntry:
    handoff_id: UUID
    scope: PasswordScope
    expires_at: datetime


class BatchPasswordRegistry:
    """Own expiring batch-local password scopes without durable projections."""

    def __init__(self) -> None:
        self._entries: dict[UUID, _RegistryEntry] = {}
        self._lock = Lock()
        self._disposed = False

    def __repr__(self) -> str:
        state = "disposed" if self._disposed else "active"
        return f"BatchPasswordRegistry(state={state!r})"

    def _purge(self, now: datetime) -> None:
        expired = [batch_id for batch_id, entry in self._entries.items() if entry.expires_at <= now]
        for batch_id in expired:
            self._entries.pop(batch_id).scope.dispose()

    def replace(
        self,
        batch_id: UUID,
        handoff_id: UUID,
        password: str,
        expires_at: datetime,
    ) -> None:
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("invalid registry expiry")
        replacement = PasswordScope(
            batch_id=batch_id,
            password=password,
            expires_at=expires_at,
        )
        with self._lock:
            if self._disposed:
                replacement.dispose()
                raise ValueError("password registry is disposed")
            now = datetime.now(UTC)
            self._purge(now)
            old = self._entries.pop(batch_id, None)
            if old is not None:
                old.scope.dispose()
            self._entries[batch_id] = _RegistryEntry(
                handoff_id=handoff_id,
                scope=replacement,
                expires_at=expires_at.astimezone(UTC),
            )

    def password_for(self, batch_id: UUID, item_id: UUID) -> str | None:
        with self._lock:
            if self._disposed:
                return None
            self._purge(datetime.now(UTC))
            entry = self._entries.get(batch_id)
            return entry.scope.password_for(item_id) if entry is not None else None

    def discard(self, batch_id: UUID) -> None:
        with self._lock:
            entry = self._entries.pop(batch_id, None)
            if entry is not None:
                entry.scope.dispose()

    def dispose(self) -> None:
        with self._lock:
            entries = tuple(self._entries.values())
            self._entries.clear()
            self._disposed = True
        for entry in entries:
            entry.scope.dispose()


def _read_exact(connection: socket.socket, count: int) -> bytes:
    chunks = bytearray()
    try:
        while len(chunks) < count:
            chunk = connection.recv(count - len(chunks))
            if not chunk:
                raise SecretChannelError("FRAME_TRUNCATED")
            chunks.extend(chunk)
    except SecretChannelError:
        raise
    except OSError, TimeoutError:
        raise SecretChannelError("FRAME_TRUNCATED") from None
    return bytes(chunks)


def _decode(payload: bytes) -> _Entry:
    try:
        value: Any = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict) or set(value) != {
            "batch_id",
            "expires_at",
            "handoff_id",
            "password",
        }:
            raise ValueError
        batch_id = UUID(value["batch_id"])
        handoff_id = UUID(value["handoff_id"])
        password = value["password"]
        expires_at = datetime.fromisoformat(value["expires_at"])
        if batch_id.int == 0 or handoff_id.int == 0 or not isinstance(password, str):
            raise ValueError
        encoded = password.encode("utf-8")
        if not encoded or len(encoded) > MAX_PASSWORD_BYTES:
            raise ValueError
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError
    except json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError, KeyError:
        raise SecretChannelError("FRAME_MALFORMED") from None
    return _Entry(batch_id, handoff_id, password, expires_at.astimezone(UTC))


class BatchSecretSocketServer:
    """Receive, validate, and atomically consume one secret per handoff ID."""

    def __init__(
        self,
        socket_path: Path,
        *,
        active_batches: set[UUID] | None = None,
        receive_timeout_seconds: float = 3.0,
        on_handoff: Callable[[UUID, UUID, str, datetime], None] | None = None,
    ) -> None:
        path = Path(socket_path)
        if not path.is_absolute() or receive_timeout_seconds <= 0:
            raise ValueError("invalid secret channel configuration")
        self._socket_path = path
        self._active_batches = set(active_batches or set())
        self._entries: dict[UUID, _Entry] = {}
        self._used: dict[UUID, datetime] = {}
        self._lock = Lock()
        self._socket: socket.socket | None = None
        self._receive_timeout_seconds = receive_timeout_seconds
        self._on_handoff = on_handoff

    def __repr__(self) -> str:
        return "BatchSecretSocketServer(state='active')"

    def start(self) -> None:
        if self._socket is not None:
            return
        try:
            existing = self._socket_path.lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISSOCK(existing.st_mode):
                raise SecretChannelError("SECRET_SOCKET_INVALID")
            self._socket_path.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self._socket_path))
            os.chmod(self._socket_path, 0o660)
            listener.listen(8)
            listener.settimeout(0.5)
        except OSError:
            listener.close()
            raise SecretChannelError("SECRET_CHANNEL_UNAVAILABLE") from None
        self._socket = listener

    def activate(self, batch_id: UUID) -> None:
        if not isinstance(batch_id, UUID) or batch_id.int == 0:
            raise ValueError("invalid batch")
        with self._lock:
            self._active_batches.add(batch_id)

    def deactivate(self, batch_id: UUID) -> None:
        with self._lock:
            self._active_batches.discard(batch_id)
            self._entries.pop(batch_id, None)

    def _purge(self, now: datetime) -> None:
        expired_batches = [
            batch_id for batch_id, entry in self._entries.items() if entry.expires_at <= now
        ]
        for batch_id in expired_batches:
            self._entries.pop(batch_id, None)
        expired_ids = [handoff_id for handoff_id, expiry in self._used.items() if expiry <= now]
        for handoff_id in expired_ids:
            self._used.pop(handoff_id, None)

    def receive_once(self) -> tuple[UUID, UUID, str, datetime]:
        listener = self._socket
        if listener is None:
            raise SecretChannelError("SECRET_CHANNEL_UNAVAILABLE")
        connection, _ = listener.accept()
        with connection:
            connection.settimeout(self._receive_timeout_seconds)
            prefix = _read_exact(connection, 4)
            (size,) = struct.unpack("!I", prefix)
            if size > MAX_FRAME_BYTES:
                raise SecretChannelError("FRAME_TOO_LARGE")
            if size == 0:
                raise SecretChannelError("FRAME_MALFORMED")
            payload = _read_exact(connection, size)
            try:
                trailing = connection.recv(1)
            except OSError, TimeoutError:
                raise SecretChannelError("FRAME_TRUNCATED") from None
            if trailing:
                raise SecretChannelError("FRAME_MALFORMED")
            entry = _decode(payload)
            now = datetime.now(UTC)
            if entry.expires_at <= now:
                raise SecretChannelError("HANDOFF_EXPIRED")
            with self._lock:
                self._purge(now)
                if entry.batch_id not in self._active_batches:
                    raise SecretChannelError("BATCH_NOT_ACTIVE")
                if entry.handoff_id in self._used or any(
                    current.handoff_id == entry.handoff_id for current in self._entries.values()
                ):
                    raise SecretChannelError("HANDOFF_REPLAYED")
                self._entries[entry.batch_id] = entry
            if self._on_handoff is not None:
                try:
                    self._on_handoff(
                        entry.batch_id,
                        entry.handoff_id,
                        entry.password,
                        entry.expires_at,
                    )
                except Exception:
                    with self._lock:
                        self._entries.pop(entry.batch_id, None)
                    raise SecretChannelError("SECRET_CHANNEL_REJECTED") from None
                with self._lock:
                    self._entries.pop(entry.batch_id, None)
                    self._used[entry.handoff_id] = entry.expires_at
            connection.sendall(_ACK)
            return entry.batch_id, entry.handoff_id, entry.password, entry.expires_at

    def take(self, batch_id: UUID, handoff_id: UUID, now: datetime) -> str | None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("invalid handoff time")
        current = now.astimezone(UTC)
        with self._lock:
            self._purge(current)
            entry = self._entries.get(batch_id)
            if entry is None or not hmac.compare_digest(
                entry.handoff_id.bytes,
                handoff_id.bytes,
            ):
                return None
            self._entries.pop(batch_id, None)
            self._used[entry.handoff_id] = entry.expires_at
            return entry.password

    def discard(self, batch_id: UUID) -> None:
        with self._lock:
            self._entries.pop(batch_id, None)

    def close(self) -> None:
        listener = self._socket
        self._socket = None
        if listener is not None:
            listener.close()
        with self._lock:
            self._entries.clear()
            self._used.clear()
            self._active_batches.clear()
        try:
            details = self._socket_path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISSOCK(details.st_mode):
            self._socket_path.unlink()


class BatchSecretReceiver:
    """Bounded background accept loop owned by the Worker process."""

    def __init__(self, server: BatchSecretSocketServer) -> None:
        self._server = server
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._server.start()
        thread = Thread(target=self._run, name="familycare-secret-receiver", daemon=True)
        self._thread = thread
        thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._server.receive_once()
            except OSError, TimeoutError, SecretChannelError:
                continue

    def close(self) -> None:
        self._stop.set()
        self._server.close()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=3)


__all__ = [
    "MAX_FRAME_BYTES",
    "MAX_PASSWORD_BYTES",
    "BatchPasswordRegistry",
    "BatchSecretReceiver",
    "BatchSecretSocketServer",
    "SecretChannelError",
]
