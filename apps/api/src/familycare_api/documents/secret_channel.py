"""Bounded client for one-time PDF password handoff to the analyzer Worker."""

from __future__ import annotations

import json
import socket
import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

MAX_FRAME_BYTES = 64 * 1024
MAX_PASSWORD_BYTES = 8 * 1024
_ACK = b"\x00"


class SecretChannelError(RuntimeError):
    """Sanitized secret-channel failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SecretHandoff:
    """One expiring handoff whose representation omits the password."""

    batch_id: UUID
    handoff_id: UUID
    password: str = field(repr=False)
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.batch_id.int == 0 or self.handoff_id.int == 0:
            raise ValueError("invalid secret handoff")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("invalid secret handoff")
        password_bytes = self.password.encode("utf-8")
        if not password_bytes or len(password_bytes) > MAX_PASSWORD_BYTES:
            raise ValueError("invalid secret handoff")


def _encode_handoff(handoff: SecretHandoff) -> bytes:
    payload = json.dumps(
        {
            "batch_id": str(handoff.batch_id),
            "expires_at": handoff.expires_at.astimezone(UTC).isoformat(),
            "handoff_id": str(handoff.handoff_id),
            "password": handoff.password,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise SecretChannelError("FRAME_TOO_LARGE")
    return struct.pack("!I", len(payload)) + payload


class BatchSecretSocketClient:
    """Send exactly one framed secret and require a Worker acknowledgement."""

    def __init__(self, socket_path: Path, *, timeout_seconds: float = 3.0) -> None:
        path = Path(socket_path)
        if not path.is_absolute() or timeout_seconds <= 0:
            raise ValueError("invalid secret channel configuration")
        self._socket_path = path
        self._timeout_seconds = timeout_seconds

    def send_once(self, handoff: SecretHandoff) -> None:
        frame = _encode_handoff(handoff)
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self._timeout_seconds)
        try:
            connection.connect(str(self._socket_path))
            connection.sendall(frame)
            connection.shutdown(socket.SHUT_WR)
            if connection.recv(1) != _ACK:
                raise SecretChannelError("SECRET_CHANNEL_REJECTED")
        except SecretChannelError:
            raise
        except OSError, TimeoutError:
            raise SecretChannelError("SECRET_CHANNEL_UNAVAILABLE") from None
        finally:
            connection.close()


__all__ = [
    "MAX_FRAME_BYTES",
    "MAX_PASSWORD_BYTES",
    "BatchSecretSocketClient",
    "SecretChannelError",
    "SecretHandoff",
]
