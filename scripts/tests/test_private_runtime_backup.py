from __future__ import annotations

import io
import json
import os
import tarfile
from pathlib import Path
from uuid import uuid4

import pytest
from familycare_worker.archive.crypto import ArchiveMetadata
from familycare_worker.archive.keys import MasterKey
from familycare_worker.archive.store import ArchiveStore

from scripts import private_runtime_backup as backup
from scripts.private_runtime_backup import (
    REPOSITORY_ROOT,
    BackupContractError,
    capture_backup_set,
    materialize_restore_inputs,
    verify_backup_set,
)

SYNTHETIC_KEY = b"synthetic-backup-master-key-0001"
SYNTHETIC_PLAINTEXT = b"synthetic policy payload"


def _private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def _private_file(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    path.chmod(0o600)
    return path


def _synthetic_sources(tmp_path: Path) -> tuple[Path, Path, Path, ArchiveMetadata]:
    source_root = _private_directory(tmp_path / "synthetic-sources")
    key_file = _private_file(source_root / "archive.key", SYNTHETIC_KEY)
    database_dump = _private_file(
        source_root / "database.pgcustom",
        b"PGDMP\x01\x0f synthetic database dump",
    )
    archive_root = _private_directory(source_root / "archive")
    key = MasterKey.from_file(key_file)
    metadata = ArchiveStore(archive_root).put(
        uuid4(),
        io.BytesIO(SYNTHETIC_PLAINTEXT),
        master_key=key,
    )
    return database_dump, archive_root, key_file, metadata


def test_capture_verify_and_materialize_synthetic_backup_set(tmp_path: Path) -> None:
    database_dump, archive_root, key_file, metadata = _synthetic_sources(tmp_path)
    destination = tmp_path / "synthetic-backup"

    captured = capture_backup_set(
        database_dump=database_dump,
        archive_root=archive_root,
        master_key_file=key_file,
        destination=destination,
    )

    assert captured.archive.object_count == 1
    assert captured.key_version == MasterKey.from_file(key_file).key_version
    assert destination.stat().st_mode & 0o777 == 0o700
    assert {path.name for path in destination.iterdir()} == {
        "archive.tar",
        "database.pgcustom",
        "manifest.json",
    }
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in destination.iterdir())
    manifest_text = (destination / "manifest.json").read_text(encoding="utf-8")
    assert metadata.object_key not in manifest_text
    assert "synthetic policy payload" not in manifest_text

    assert verify_backup_set(destination, master_key_file=key_file) == captured

    restore_destination = tmp_path / "synthetic-restore-inputs"
    restored = materialize_restore_inputs(
        backup_root=destination,
        master_key_file=key_file,
        destination=restore_destination,
    )

    assert restored.database_dump.read_bytes() == database_dump.read_bytes()
    restored_store = ArchiveStore(restored.archive_root)
    with restored_store.open(metadata, master_key=MasterKey.from_file(key_file)) as source:
        assert source.read() == SYNTHETIC_PLAINTEXT
    assert restore_destination.stat().st_mode & 0o777 == 0o700
    assert restored.archive_root.stat().st_mode & 0o777 == 0o700
    assert restored.database_dump.stat().st_mode & 0o777 == 0o600


def test_verify_rejects_tampered_artifact_with_sanitized_error(tmp_path: Path) -> None:
    database_dump, archive_root, key_file, _ = _synthetic_sources(tmp_path)
    destination = tmp_path / "synthetic-backup"
    capture_backup_set(
        database_dump=database_dump,
        archive_root=archive_root,
        master_key_file=key_file,
        destination=destination,
    )
    with (destination / "database.pgcustom").open("ab") as handle:
        handle.write(b"tampered")

    with pytest.raises(BackupContractError) as raised:
        verify_backup_set(destination, master_key_file=key_file)

    assert str(raised.value) == "BACKUP_INTEGRITY_ERROR"
    assert str(destination) not in str(raised.value)


def test_verify_authenticates_manifest_before_trusting_counts(tmp_path: Path) -> None:
    database_dump, archive_root, key_file, _ = _synthetic_sources(tmp_path)
    destination = tmp_path / "synthetic-backup"
    capture_backup_set(
        database_dump=database_dump,
        archive_root=archive_root,
        master_key_file=key_file,
        destination=destination,
    )
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive"]["object_count"] = 9
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_path.chmod(0o600)

    with pytest.raises(BackupContractError, match="^BACKUP_INTEGRITY_ERROR$"):
        verify_backup_set(destination, master_key_file=key_file)


def test_verify_rejects_a_different_recovery_key(tmp_path: Path) -> None:
    database_dump, archive_root, key_file, _ = _synthetic_sources(tmp_path)
    destination = tmp_path / "synthetic-backup"
    capture_backup_set(
        database_dump=database_dump,
        archive_root=archive_root,
        master_key_file=key_file,
        destination=destination,
    )
    other_key = _private_file(tmp_path / "other.key", b"synthetic-backup-master-key-0002")

    with pytest.raises(BackupContractError, match="^BACKUP_KEY_MISMATCH$"):
        verify_backup_set(destination, master_key_file=other_key)


def test_capture_rejects_non_custom_database_dump_without_output(tmp_path: Path) -> None:
    database_dump, archive_root, key_file, _ = _synthetic_sources(tmp_path)
    database_dump.write_bytes(b"synthetic plain SQL")
    database_dump.chmod(0o600)
    destination = tmp_path / "synthetic-backup"

    with pytest.raises(BackupContractError, match="^BACKUP_SOURCE_INVALID$"):
        capture_backup_set(
            database_dump=database_dump,
            archive_root=archive_root,
            master_key_file=key_file,
            destination=destination,
        )

    assert not destination.exists()


def test_capture_rejects_unexpected_archive_entry_without_output(tmp_path: Path) -> None:
    database_dump, archive_root, key_file, _ = _synthetic_sources(tmp_path)
    _private_file(archive_root / ".tmp-synthetic", b"incomplete")
    destination = tmp_path / "synthetic-backup"

    with pytest.raises(BackupContractError, match="^BACKUP_SOURCE_INVALID$"):
        capture_backup_set(
            database_dump=database_dump,
            archive_root=archive_root,
            master_key_file=key_file,
            destination=destination,
        )

    assert not destination.exists()


def test_capture_never_bundles_a_master_key_placed_inside_the_archive(tmp_path: Path) -> None:
    database_dump, archive_root, _, _ = _synthetic_sources(tmp_path)
    misplaced_key = _private_file(archive_root / ("f" * 32), SYNTHETIC_KEY)
    destination = tmp_path / "synthetic-backup"

    with pytest.raises(BackupContractError, match="^BACKUP_SOURCE_INVALID$"):
        capture_backup_set(
            database_dump=database_dump,
            archive_root=archive_root,
            master_key_file=misplaced_key,
            destination=destination,
        )

    assert not destination.exists()


def test_capture_rejects_repository_destination_before_creating_it(tmp_path: Path) -> None:
    database_dump, archive_root, key_file, _ = _synthetic_sources(tmp_path)
    destination = REPOSITORY_ROOT / "synthetic-backup-must-not-exist"

    with pytest.raises(BackupContractError, match="^BACKUP_DESTINATION_INVALID$"):
        capture_backup_set(
            database_dump=database_dump,
            archive_root=archive_root,
            master_key_file=key_file,
            destination=destination,
        )

    assert not destination.exists()


def test_materialize_refuses_to_replace_an_existing_destination(tmp_path: Path) -> None:
    database_dump, archive_root, key_file, _ = _synthetic_sources(tmp_path)
    backup_root = tmp_path / "synthetic-backup"
    capture_backup_set(
        database_dump=database_dump,
        archive_root=archive_root,
        master_key_file=key_file,
        destination=backup_root,
    )
    destination = _private_directory(tmp_path / "existing-restore")
    marker = _private_file(destination / "keep.txt", b"keep")

    with pytest.raises(BackupContractError, match="^BACKUP_DESTINATION_INVALID$"):
        materialize_restore_inputs(
            backup_root=backup_root,
            master_key_file=key_file,
            destination=destination,
        )

    assert marker.read_bytes() == b"keep"


def test_materialize_rechecks_the_archive_opened_after_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_dump, archive_root, key_file, metadata = _synthetic_sources(tmp_path)
    backup_root = tmp_path / "synthetic-backup"
    capture_backup_set(
        database_dump=database_dump,
        archive_root=archive_root,
        master_key_file=key_file,
        destination=backup_root,
    )
    original_verify = backup.verify_backup_set

    def verify_then_replace(
        candidate: Path,
        *,
        master_key_file: Path,
    ) -> backup.BackupManifest:
        manifest = original_verify(candidate, master_key_file=master_key_file)
        member = tarfile.TarInfo(metadata.object_key)
        member.size = metadata.ciphertext_size
        member.mode = 0o600
        member.uid = 0
        member.gid = 0
        member.mtime = 0
        with tarfile.open(candidate / "archive.tar", mode="w") as replacement:
            replacement.addfile(member, io.BytesIO(b"x" * metadata.ciphertext_size))
        (candidate / "archive.tar").chmod(0o600)
        return manifest

    monkeypatch.setattr(backup, "verify_backup_set", verify_then_replace)
    destination = tmp_path / "synthetic-restore-inputs"

    with pytest.raises(BackupContractError, match="^BACKUP_INTEGRITY_ERROR$"):
        materialize_restore_inputs(
            backup_root=backup_root,
            master_key_file=key_file,
            destination=destination,
        )

    assert not destination.exists()


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks are unavailable")
def test_capture_rejects_symlinked_archive_objects(tmp_path: Path) -> None:
    database_dump, archive_root, key_file, _ = _synthetic_sources(tmp_path)
    object_path = next(archive_root.iterdir())
    target = _private_file(tmp_path / "synthetic-target", b"ciphertext")
    object_path.unlink()
    object_path.symlink_to(target)

    with pytest.raises(BackupContractError, match="^BACKUP_SOURCE_INVALID$"):
        capture_backup_set(
            database_dump=database_dump,
            archive_root=archive_root,
            master_key_file=key_file,
            destination=tmp_path / "synthetic-backup",
        )
