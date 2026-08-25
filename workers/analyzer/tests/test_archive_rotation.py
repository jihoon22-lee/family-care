"""Synthetic managed-archive wrapped-key rotation tests."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from uuid import UUID

import pytest
from familycare_worker.archive.crypto import (
    ArchiveIntegrityError,
    decrypt_document,
    encrypt_document,
)
from familycare_worker.archive.keys import MasterKey
from familycare_worker.archive.rotation import rewrap_metadata
from familycare_worker.archive.store import ArchiveStore

SYNTHETIC_OLD_KEY = b"synthetic-master-key-00000000000"
SYNTHETIC_NEW_KEY = b"synthetic-new-master-key-0000000"
SYNTHETIC_VERSION_ID = UUID("00000000-0000-4000-8000-000000000203")
SYNTHETIC_PLAINTEXT = b"synthetic-rotation-plaintext"


def _master_key(raw: bytes, version: str) -> MasterKey:
    return MasterKey.synthetic(raw, key_version=version)


def _assert_sanitized(error: BaseException, *forbidden: str) -> None:
    rendered = f"{error!s}\n{error!r}"
    assert all(value not in rendered for value in forbidden)
    assert error.__cause__ is None


def test_rewrap_changes_only_wrapped_key_and_key_version() -> None:
    old_key = _master_key(SYNTHETIC_OLD_KEY, "synthetic-old-v1")
    new_key = _master_key(SYNTHETIC_NEW_KEY, "synthetic-new-v2")
    metadata, ciphertext = encrypt_document(
        SYNTHETIC_PLAINTEXT,
        document_version_id=SYNTHETIC_VERSION_ID,
        master_key=old_key,
    )

    rotated = rewrap_metadata(metadata, old_key=old_key, new_key=new_key)

    assert rotated.archive_id == metadata.archive_id
    assert rotated.document_version_id == metadata.document_version_id
    assert rotated.object_key == metadata.object_key
    assert rotated.scheme == metadata.scheme
    assert rotated.nonce == metadata.nonce
    assert rotated.ciphertext_size == metadata.ciphertext_size
    assert rotated.auth_tag == metadata.auth_tag
    assert rotated.wrapped_data_key != metadata.wrapped_data_key
    assert rotated.key_version == "synthetic-new-v2"

    assert decrypt_document(metadata, ciphertext, master_key=old_key) == SYNTHETIC_PLAINTEXT
    assert decrypt_document(rotated, ciphertext, master_key=new_key) == SYNTHETIC_PLAINTEXT
    with pytest.raises(ArchiveIntegrityError):
        decrypt_document(rotated, ciphertext, master_key=old_key)


def test_archive_store_rewrap_all_reports_each_old_key_object(tmp_path: Path) -> None:
    old_key = _master_key(SYNTHETIC_OLD_KEY, "synthetic-old-v1")
    new_key = _master_key(SYNTHETIC_NEW_KEY, "synthetic-new-v2")
    store = ArchiveStore(tmp_path)
    store.put(
        SYNTHETIC_VERSION_ID,
        io.BytesIO(SYNTHETIC_PLAINTEXT),
        master_key=old_key,
    )
    store.put(
        UUID("00000000-0000-4000-8000-000000000204"),
        io.BytesIO(SYNTHETIC_PLAINTEXT),
        master_key=old_key,
    )

    assert store.rewrap_all(old_key, new_key) == 2


def test_rewrap_failure_does_not_expose_key_plaintext_or_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    old_key = _master_key(SYNTHETIC_OLD_KEY, "synthetic-old-v1")
    wrong_old_key = _master_key(SYNTHETIC_NEW_KEY, "synthetic-wrong-old")
    new_key = _master_key(b"synthetic-rotate-key-00000000000", "synthetic-new-v2")
    metadata, ciphertext = encrypt_document(
        SYNTHETIC_PLAINTEXT,
        document_version_id=SYNTHETIC_VERSION_ID,
        master_key=old_key,
    )

    with pytest.raises(ArchiveIntegrityError) as raised:
        rewrap_metadata(metadata, old_key=wrong_old_key, new_key=new_key)

    _assert_sanitized(
        raised.value,
        SYNTHETIC_OLD_KEY.decode("ascii"),
        SYNTHETIC_NEW_KEY.decode("ascii"),
        SYNTHETIC_PLAINTEXT.decode("ascii"),
        ciphertext.hex(),
    )
    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert SYNTHETIC_OLD_KEY.decode("ascii") not in rendered_logs
    assert SYNTHETIC_NEW_KEY.decode("ascii") not in rendered_logs
    assert SYNTHETIC_PLAINTEXT.decode("ascii") not in rendered_logs
