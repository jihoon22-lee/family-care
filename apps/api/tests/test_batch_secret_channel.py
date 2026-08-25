"""Unix-domain one-time password handoff tests."""

from __future__ import annotations

import json
import logging
import socket
import stat
import struct
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from familycare_api.documents.secret_channel import (
    MAX_FRAME_BYTES,
    BatchSecretSocketClient,
    SecretHandoff,
)
from familycare_worker.imports.secret_channel import BatchSecretSocketServer, SecretChannelError

SYNTHETIC_BATCH_ID = UUID("00000000-0000-4000-8000-000000000005")
SYNTHETIC_OTHER_BATCH_ID = UUID("00000000-0000-4000-8000-000000000006")
SYNTHETIC_HANDOFF_ID = UUID("00000000-0000-4000-8000-000000000007")
SYNTHETIC_REPLAY_HANDOFF_ID = UUID("00000000-0000-4000-8000-000000000008")
SYNTHETIC_PASSWORD = "synthetic-batch-password"
SYNTHETIC_REPLACEMENT_PASSWORD = "synthetic-replacement-password"


def _handoff(
    *,
    batch_id: UUID = SYNTHETIC_BATCH_ID,
    handoff_id: UUID = SYNTHETIC_HANDOFF_ID,
    password: str = SYNTHETIC_PASSWORD,
    expires_at: datetime | None = None,
) -> SecretHandoff:
    return SecretHandoff(
        batch_id=batch_id,
        handoff_id=handoff_id,
        password=password,
        expires_at=expires_at or datetime.now(UTC) + timedelta(minutes=1),
    )


@contextmanager
def _running_server(
    tmp_path: Path,
    *,
    active_batches: set[UUID] | None = None,
) -> Iterator[tuple[BatchSecretSocketServer, Path]]:
    socket_path = tmp_path / "secret.sock"
    server = BatchSecretSocketServer(
        socket_path,
        active_batches=active_batches if active_batches is not None else {SYNTHETIC_BATCH_ID},
    )
    server.start()
    try:
        yield server, socket_path
    finally:
        server.close()


def _payload(frame: SecretHandoff) -> bytes:
    return json.dumps(
        {
            "batch_id": str(frame.batch_id),
            "handoff_id": str(frame.handoff_id),
            "password": frame.password,
            "expires_at": frame.expires_at.isoformat(),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _wire(payload: bytes) -> bytes:
    return struct.pack("!I", len(payload)) + payload


def _receive_raw(
    server: BatchSecretSocketServer,
    socket_path: Path,
    wire: bytes,
    *,
    split_at: int | None = None,
) -> object:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.connect(str(socket_path))
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(server.receive_once)
            if split_at is None:
                connection.sendall(wire)
            else:
                connection.sendall(wire[:split_at])
                connection.sendall(wire[split_at:])
            connection.shutdown(socket.SHUT_WR)
            return future.result(timeout=2)
    finally:
        connection.close()


def _receive_client(
    server: BatchSecretSocketServer,
    socket_path: Path,
    frame: SecretHandoff,
) -> object:
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(server.receive_once)
        BatchSecretSocketClient(socket_path).send_once(frame)
        return future.result(timeout=2)


def _assert_stable_error(raised: pytest.ExceptionInfo[SecretChannelError], code: str) -> None:
    assert str(raised.value) == code
    assert getattr(raised.value, "code", code) == code
    assert SYNTHETIC_PASSWORD not in str(raised.value)
    assert SYNTHETIC_PASSWORD not in repr(raised.value)


def test_socket_uses_bounded_length_prefix_and_mode_0660(tmp_path: Path) -> None:
    assert MAX_FRAME_BYTES == 64 * 1024

    frame = _handoff()
    with _running_server(tmp_path) as (server, socket_path):
        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o660

        received = _receive_raw(server, socket_path, _wire(_payload(frame)), split_at=2)

        assert received == (
            frame.batch_id,
            frame.handoff_id,
            frame.password,
            frame.expires_at,
        )
        assert server.take(frame.batch_id, frame.handoff_id, datetime.now(UTC)) == frame.password


def test_socket_rejects_handoff_for_batch_without_active_scope(tmp_path: Path) -> None:
    frame = _handoff(batch_id=SYNTHETIC_OTHER_BATCH_ID)
    with (
        _running_server(tmp_path, active_batches=set()) as (server, socket_path),
        pytest.raises(SecretChannelError) as raised,
    ):
        _receive_raw(server, socket_path, _wire(_payload(frame)))

    _assert_stable_error(raised, "BATCH_NOT_ACTIVE")


def test_take_is_atomic_and_replay_is_rejected(tmp_path: Path) -> None:
    frame = _handoff()
    with _running_server(tmp_path) as (server, socket_path):
        _receive_client(server, socket_path, frame)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _: server.take(frame.batch_id, frame.handoff_id, datetime.now(UTC)),
                    range(2),
                )
            )

        assert results.count(frame.password) == 1
        assert results.count(None) == 1
        assert server.take(frame.batch_id, frame.handoff_id, datetime.now(UTC)) is None


def test_expired_entries_are_stale_and_discarded_before_a_fresh_handoff(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    expired = _handoff(expires_at=now + timedelta(seconds=1))
    fresh = _handoff(
        handoff_id=SYNTHETIC_REPLAY_HANDOFF_ID,
        password=SYNTHETIC_REPLACEMENT_PASSWORD,
        expires_at=now + timedelta(minutes=1),
    )

    with _running_server(tmp_path) as (server, socket_path):
        _receive_client(server, socket_path, expired)
        assert server.take(expired.batch_id, expired.handoff_id, now + timedelta(seconds=2)) is None

        _receive_client(server, socket_path, fresh)
        assert server.take(fresh.batch_id, fresh.handoff_id, now) == fresh.password
        server.discard(fresh.batch_id)
        assert server.take(fresh.batch_id, fresh.handoff_id, now) is None


def test_already_expired_handoff_is_rejected_before_storage(tmp_path: Path) -> None:
    expired = _handoff(expires_at=datetime.now(UTC) - timedelta(seconds=1))

    with (
        _running_server(tmp_path) as (server, socket_path),
        pytest.raises(SecretChannelError) as raised,
    ):
        _receive_raw(server, socket_path, _wire(_payload(expired)))

    _assert_stable_error(raised, "HANDOFF_EXPIRED")


def test_frame_with_trailing_bytes_is_rejected(tmp_path: Path) -> None:
    frame = _handoff()

    with (
        _running_server(tmp_path) as (server, socket_path),
        pytest.raises(SecretChannelError) as raised,
    ):
        _receive_raw(server, socket_path, _wire(_payload(frame)) + b"x")

    _assert_stable_error(raised, "FRAME_MALFORMED")


@pytest.mark.parametrize(
    ("wire", "expected_code"),
    [
        (
            struct.pack("!I", len(b'{"password":"synthetic-batch-password"}') + 10)
            + b'{"password":"synthetic-batch-password"}',
            "FRAME_TRUNCATED",
        ),
        (struct.pack("!I", MAX_FRAME_BYTES + 1), "FRAME_TOO_LARGE"),
        (
            _wire(b'{"batch_id":"not-a-uuid","password":"synthetic-batch-password"}'),
            "FRAME_MALFORMED",
        ),
    ],
)
def test_invalid_frames_have_stable_password_free_errors(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    wire: bytes,
    expected_code: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    with (
        _running_server(tmp_path) as (server, socket_path),
        pytest.raises(SecretChannelError) as raised,
    ):
        _receive_raw(server, socket_path, wire)

    _assert_stable_error(raised, expected_code)
    assert SYNTHETIC_PASSWORD not in caplog.text


def test_handoff_repr_and_channel_logs_never_include_password(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    frame = _handoff()
    assert SYNTHETIC_PASSWORD not in repr(frame)

    with _running_server(tmp_path) as (server, socket_path):
        _receive_client(server, socket_path, frame)
        assert server.take(frame.batch_id, frame.handoff_id, datetime.now(UTC)) == frame.password

    assert SYNTHETIC_PASSWORD not in caplog.text
