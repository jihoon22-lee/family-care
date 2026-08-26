"""Synthetic key, envelope, and managed-archive boundary tests."""

from __future__ import annotations

import io
import logging
import os
import re
import stat
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

import pytest
from familycare_worker.archive.crypto import (
    ArchiveIntegrityError,
    decrypt_document,
    encrypt_document,
)
from familycare_worker.archive.keys import MasterKey
from familycare_worker.archive.store import ArchiveStore, ArchiveStoreError

SYNTHETIC_MASTER_KEY = b"synthetic-master-key-00000000000"
SYNTHETIC_OTHER_KEY = b"synthetic-new-master-key-0000000"
SYNTHETIC_VERSION_ID = UUID("00000000-0000-4000-8000-000000000201")
SYNTHETIC_OTHER_VERSION_ID = UUID("00000000-0000-4000-8000-000000000202")
SYNTHETIC_PLAINTEXT = b"synthetic-archive-plaintext"
SYNTHETIC_KEY_TEXT = SYNTHETIC_MASTER_KEY.decode("ascii")


def _synthetic_key(raw: bytes = SYNTHETIC_MASTER_KEY) -> MasterKey:
    return MasterKey.synthetic(raw, key_version="synthetic-v1")


def _assert_sanitized_error(error: BaseException, *forbidden: str) -> None:
    rendered = f"{error!s}\n{error!r}"
    assert all(value not in rendered for value in forbidden)
    assert error.__cause__ is None


def _assert_sanitized_logs(caplog: pytest.LogCaptureFixture, *forbidden: str) -> None:
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert all(value not in rendered for value in forbidden)


def _object_path(root: Path, object_key: str) -> Path:
    matches = [candidate for candidate in root.rglob(object_key) if candidate.is_file()]
    assert len(matches) == 1
    return matches[0]


def test_master_key_from_file_accepts_absolute_regular_mode_0600_exact_32_bytes(
    tmp_path: Path,
) -> None:
    key_path = tmp_path / "synthetic-master-key.bin"
    key_path.write_bytes(SYNTHETIC_MASTER_KEY)
    os.chmod(key_path, 0o600)

    loaded = MasterKey.from_file(key_path)
    metadata, ciphertext = encrypt_document(
        SYNTHETIC_PLAINTEXT,
        document_version_id=SYNTHETIC_VERSION_ID,
        master_key=loaded,
    )

    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert decrypt_document(metadata, ciphertext, master_key=loaded) == SYNTHETIC_PLAINTEXT
    assert SYNTHETIC_KEY_TEXT not in repr(loaded)


@pytest.mark.parametrize(
    ("name", "contents", "mode"),
    [
        ("synthetic-short-key.bin", SYNTHETIC_MASTER_KEY[:-1], 0o600),
        ("synthetic-long-key.bin", SYNTHETIC_MASTER_KEY + b"x", 0o600),
        ("synthetic-mode-key.bin", SYNTHETIC_MASTER_KEY, 0o640),
    ],
)
def test_master_key_from_file_rejects_invalid_file_shape_without_details(
    tmp_path: Path,
    name: str,
    contents: bytes,
    mode: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    key_path = tmp_path / name
    key_path.write_bytes(contents)
    os.chmod(key_path, mode)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(Exception) as raised:
        MasterKey.from_file(key_path)

    _assert_sanitized_error(raised.value, str(key_path), contents.decode("ascii"))
    _assert_sanitized_logs(caplog, str(key_path), contents.decode("ascii"))


def test_master_key_from_file_rejects_relative_missing_and_symlink_paths_without_details(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    missing_path = tmp_path / "synthetic-missing-master-key.bin"
    with pytest.raises(Exception) as missing:
        MasterKey.from_file(missing_path)
    _assert_sanitized_error(missing.value, str(missing_path))

    with pytest.raises(Exception) as relative:
        MasterKey.from_file(Path("synthetic-relative-master-key.bin"))
    _assert_sanitized_error(relative.value, "synthetic-relative-master-key.bin")

    target = tmp_path / "synthetic-key-target.bin"
    target.write_bytes(SYNTHETIC_MASTER_KEY)
    os.chmod(target, 0o600)
    link = tmp_path / "synthetic-key-link.bin"
    link.symlink_to(target)
    with pytest.raises(Exception) as symlink:
        MasterKey.from_file(link)
    _assert_sanitized_error(symlink.value, str(link), str(target), SYNTHETIC_KEY_TEXT)
    _assert_sanitized_logs(
        caplog,
        str(missing_path),
        "synthetic-relative-master-key.bin",
        str(link),
        str(target),
        SYNTHETIC_KEY_TEXT,
    )


def test_encrypt_document_round_trips_with_aes_gcm_and_aes_kw_metadata() -> None:
    key = _synthetic_key()

    metadata, ciphertext = encrypt_document(
        SYNTHETIC_PLAINTEXT,
        document_version_id=SYNTHETIC_VERSION_ID,
        master_key=key,
    )

    assert metadata.scheme == "aes-256-gcm+aes-kw-v1"
    assert metadata.document_version_id == SYNTHETIC_VERSION_ID
    assert len(metadata.nonce) == 12
    assert len(metadata.wrapped_data_key) == 40
    assert len(metadata.auth_tag) == 16
    assert metadata.ciphertext_size == len(ciphertext)
    assert ciphertext != SYNTHETIC_PLAINTEXT
    assert decrypt_document(metadata, ciphertext, master_key=key) == SYNTHETIC_PLAINTEXT


def test_encrypt_document_authenticates_document_version_as_aad() -> None:
    key = _synthetic_key()
    metadata, ciphertext = encrypt_document(
        SYNTHETIC_PLAINTEXT,
        document_version_id=SYNTHETIC_VERSION_ID,
        master_key=key,
    )

    metadata_for_another_document = replace(
        metadata,
        document_version_id=SYNTHETIC_OTHER_VERSION_ID,
    )
    with pytest.raises(ArchiveIntegrityError):
        decrypt_document(metadata_for_another_document, ciphertext, master_key=key)


def test_decrypt_document_rejects_ciphertext_tag_and_wrong_master_key_tampering() -> None:
    key = _synthetic_key()
    wrong_key = _synthetic_key(SYNTHETIC_OTHER_KEY)
    metadata, ciphertext = encrypt_document(
        SYNTHETIC_PLAINTEXT,
        document_version_id=SYNTHETIC_VERSION_ID,
        master_key=key,
    )

    tampered_ciphertext = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
    with pytest.raises(ArchiveIntegrityError):
        decrypt_document(metadata, tampered_ciphertext, master_key=key)

    tampered_metadata = replace(
        metadata,
        auth_tag=bytes([metadata.auth_tag[0] ^ 1]) + metadata.auth_tag[1:],
    )
    with pytest.raises(ArchiveIntegrityError):
        decrypt_document(tampered_metadata, ciphertext, master_key=key)

    with pytest.raises(ArchiveIntegrityError) as wrong_key_error:
        decrypt_document(metadata, ciphertext, master_key=wrong_key)
    _assert_sanitized_error(
        wrong_key_error.value,
        SYNTHETIC_KEY_TEXT,
        SYNTHETIC_PLAINTEXT.decode("ascii"),
    )


def test_archive_store_writes_opaque_mode_0600_ciphertext_atomically(tmp_path: Path) -> None:
    key = _synthetic_key()
    store = ArchiveStore(tmp_path)

    metadata = store.put(
        SYNTHETIC_VERSION_ID,
        io.BytesIO(SYNTHETIC_PLAINTEXT),
        master_key=key,
    )

    assert re.fullmatch(r"[0-9a-f]{32}", metadata.object_key)
    assert str(SYNTHETIC_VERSION_ID) not in metadata.object_key
    object_path = _object_path(tmp_path, metadata.object_key)
    assert stat.S_IMODE(object_path.stat().st_mode) == 0o600
    assert SYNTHETIC_PLAINTEXT not in object_path.read_bytes()
    assert all(
        not candidate.name.startswith((".tmp-", "tmp-"))
        for candidate in tmp_path.rglob("*")
        if candidate.is_file()
    )

    with store.open(metadata, master_key=key) as opened:
        assert opened.read() == SYNTHETIC_PLAINTEXT


def test_archive_store_removes_final_object_when_directory_sync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = _synthetic_key()
    store = ArchiveStore(tmp_path)
    real_fsync = os.fsync
    calls = 0

    def fail_directory_sync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic directory sync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_directory_sync)

    with pytest.raises(ArchiveStoreError, match="ARCHIVE_WRITE_FAILED"):
        store.put(
            SYNTHETIC_VERSION_ID,
            io.BytesIO(SYNTHETIC_PLAINTEXT),
            master_key=key,
        )

    assert not [candidate for candidate in tmp_path.rglob("*") if candidate.is_file()]


class _UnboundedReadError(BaseException):
    """Test-only signal that the implementation asked for an unbounded read."""


class _SyntheticOverflowStream(io.RawIOBase):
    def __init__(self, size: int) -> None:
        self._size = size
        self._position = 0

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            raise _UnboundedReadError
        if self._position >= self._size:
            return b""
        amount = min(size, 1024 * 1024, self._size - self._position)
        self._position += amount
        return b"x" * amount

    def readinto(self, buffer: bytearray) -> int:
        if self._position >= self._size:
            return 0
        amount = min(len(buffer), 1024 * 1024, self._size - self._position)
        buffer[:amount] = b"x" * amount
        self._position += amount
        return amount


def test_archive_store_rejects_more_than_64_mib_without_unbounded_source_read(
    tmp_path: Path,
) -> None:
    key = _synthetic_key()
    store = ArchiveStore(tmp_path)
    source: BinaryIO = _SyntheticOverflowStream(64 * 1024 * 1024 + 1)

    with pytest.raises(Exception) as raised:
        store.put(SYNTHETIC_VERSION_ID, source, master_key=key)

    assert not isinstance(raised.value, AssertionError)
    assert not [candidate for candidate in tmp_path.rglob("*") if candidate.is_file()]


def test_archive_failures_do_not_expose_key_plaintext_or_archive_path_in_error_or_log(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    key = _synthetic_key()
    store = ArchiveStore(tmp_path)
    metadata = store.put(
        SYNTHETIC_VERSION_ID,
        io.BytesIO(SYNTHETIC_PLAINTEXT),
        master_key=key,
    )
    object_path = _object_path(tmp_path, metadata.object_key)
    object_path.unlink()

    with pytest.raises(Exception) as raised:
        store.open(metadata, master_key=key)

    _assert_sanitized_error(
        raised.value,
        str(tmp_path),
        SYNTHETIC_KEY_TEXT,
        SYNTHETIC_PLAINTEXT.decode("ascii"),
    )
    _assert_sanitized_logs(
        caplog,
        str(tmp_path),
        SYNTHETIC_KEY_TEXT,
        SYNTHETIC_PLAINTEXT.decode("ascii"),
    )
