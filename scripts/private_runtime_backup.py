#!/usr/bin/env python3
"""Build and verify offline private-runtime backup sets without touching a live database.

The caller must quiesce writers and create a PostgreSQL custom-format dump before capture.
This tool bundles that dump with the encrypted archive, authenticates a path-free manifest,
and can materialize fresh restore inputs. It never invokes pg_dump or pg_restore, copies the
archive master key, replaces an existing destination, or writes inside the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import sys
import tarfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Never, cast

from familycare_worker.archive.crypto import MAX_ARCHIVE_BYTES
from familycare_worker.archive.keys import MasterKey, MasterKeyError

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKUP_SCHEMA_VERSION = 1
DATABASE_ARTIFACT_NAME = "database.pgcustom"
ARCHIVE_ARTIFACT_NAME = "archive.tar"
MANIFEST_NAME = "manifest.json"
POSTGRES_CUSTOM_MAGIC = b"PGDMP"
MAX_DATABASE_DUMP_BYTES = 64 * 1024 * 1024 * 1024
MAX_ARCHIVE_OBJECTS = 100_000
MAX_MANIFEST_BYTES = 32 * 1024
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
_READ_CHUNK_BYTES = 1024 * 1024
_OBJECT_KEY_PATTERN = re.compile(r"^[a-f0-9]{32}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_KEY_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MANIFEST_AUTH_CONTEXT = b"familycare-private-backup-manifest-v1"


class BackupContractError(RuntimeError):
    """One sanitized backup contract failure safe for operator output."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ArtifactDigest:
    """Integrity metadata for one fixed-name backup artifact."""

    file: str
    sha256: str
    size: int


@dataclass(frozen=True)
class ArchiveDigest:
    """Integrity metadata for the opaque flat archive snapshot."""

    file: str
    sha256: str
    size: int
    object_count: int


@dataclass(frozen=True)
class BackupManifest:
    """Authenticated metadata needed to verify one backup set."""

    schema_version: int
    key_version: str
    database: ArtifactDigest
    archive: ArchiveDigest
    manifest_hmac_sha256: str


@dataclass(frozen=True)
class RestoreInputs:
    """Fresh paths prepared for a separately approved restore procedure."""

    database_dump: Path
    archive_root: Path


@dataclass(frozen=True)
class _ArchiveSource:
    name: str
    size: int
    device: int
    inode: int
    modified_ns: int
    changed_ns: int


def _raise(code: str) -> Never:
    raise BackupContractError(code) from None


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _resolved(path: Path, *, strict: bool, code: str) -> Path:
    try:
        return path.resolve(strict=strict)
    except OSError, RuntimeError:
        _raise(code)


def _require_outside_repository(path: Path, *, strict: bool, code: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        _raise(code)
    resolved = _resolved(candidate, strict=strict, code=code)
    if _is_within(resolved, REPOSITORY_ROOT):
        _raise(code)
    return resolved


def _mode(details: os.stat_result) -> int:
    return stat.S_IMODE(details.st_mode)


def _lstat(path: Path, *, code: str) -> os.stat_result:
    try:
        return path.lstat()
    except OSError:
        _raise(code)


def _require_private_directory(path: Path, *, code: str) -> Path:
    resolved = _require_outside_repository(path, strict=True, code=code)
    details = _lstat(path, code=code)
    if not stat.S_ISDIR(details.st_mode) or _mode(details) != PRIVATE_DIRECTORY_MODE:
        _raise(code)
    return resolved


def _path_exists_without_following(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _require_new_private_destination(path: Path) -> Path:
    candidate = Path(path)
    resolved = _require_outside_repository(
        candidate, strict=False, code="BACKUP_DESTINATION_INVALID"
    )
    if _path_exists_without_following(candidate):
        _raise("BACKUP_DESTINATION_INVALID")
    parent = candidate.parent
    parent_resolved = _require_private_directory(parent, code="BACKUP_DESTINATION_INVALID")
    if resolved.parent != parent_resolved:
        _raise("BACKUP_DESTINATION_INVALID")
    return candidate


def _open_private_regular(path: Path, *, code: str) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or _mode(details) != PRIVATE_FILE_MODE:
            _raise(code)
        return descriptor, details
    except BackupContractError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _raise(code)


@contextmanager
def _private_reader(path: Path, *, code: str) -> Iterator[tuple[BinaryIO, os.stat_result]]:
    descriptor, details = _open_private_regular(path, code=code)
    with os.fdopen(descriptor, "rb", closefd=True) as source:
        yield source, details


@contextmanager
def _exclusive_private_writer(path: Path) -> Iterator[BinaryIO]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(path, flags, PRIVATE_FILE_MODE)
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "wb", closefd=True) as destination:
            descriptor = -1
            yield destination
            destination.flush()
            os.fsync(destination.fileno())
    except BackupContractError:
        raise
    except OSError:
        _raise("BACKUP_OPERATION_FAILED")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _load_master_key(path: Path) -> MasterKey:
    _require_outside_repository(path, strict=True, code="BACKUP_KEY_UNAVAILABLE")
    try:
        return MasterKey.from_file(path)
    except MasterKeyError:
        _raise("BACKUP_KEY_UNAVAILABLE")


def _copy_database_dump(source_path: Path, destination_path: Path) -> ArtifactDigest:
    digest = hashlib.sha256()
    total = 0
    with _private_reader(source_path, code="BACKUP_SOURCE_INVALID") as (source, initial):
        if (
            initial.st_size < len(POSTGRES_CUSTOM_MAGIC)
            or initial.st_size > MAX_DATABASE_DUMP_BYTES
        ):
            _raise("BACKUP_SOURCE_INVALID")
        with _exclusive_private_writer(destination_path) as destination:
            first = True
            while chunk := source.read(_READ_CHUNK_BYTES):
                if first:
                    first = False
                    if not chunk.startswith(POSTGRES_CUSTOM_MAGIC):
                        _raise("BACKUP_SOURCE_INVALID")
                total += len(chunk)
                if total > MAX_DATABASE_DUMP_BYTES:
                    _raise("BACKUP_SOURCE_INVALID")
                digest.update(chunk)
                destination.write(chunk)
            final = os.fstat(source.fileno())
        if (
            total != initial.st_size
            or final.st_size != initial.st_size
            or final.st_dev != initial.st_dev
            or final.st_ino != initial.st_ino
            or final.st_mtime_ns != initial.st_mtime_ns
            or final.st_ctime_ns != initial.st_ctime_ns
        ):
            _raise("BACKUP_SOURCE_INVALID")
    return ArtifactDigest(DATABASE_ARTIFACT_NAME, digest.hexdigest(), total)


def _archive_source_matches(details: os.stat_result, source: _ArchiveSource) -> bool:
    return (
        stat.S_ISREG(details.st_mode)
        and _mode(details) == PRIVATE_FILE_MODE
        and details.st_nlink == 1
        and details.st_size == source.size
        and details.st_dev == source.device
        and details.st_ino == source.inode
        and details.st_mtime_ns == source.modified_ns
        and details.st_ctime_ns == source.changed_ns
    )


def _scan_archive_sources(directory_descriptor: int) -> tuple[list[_ArchiveSource], os.stat_result]:
    initial_directory = os.fstat(directory_descriptor)
    if (
        not stat.S_ISDIR(initial_directory.st_mode)
        or _mode(initial_directory) != PRIVATE_DIRECTORY_MODE
    ):
        _raise("BACKUP_SOURCE_INVALID")
    sources: list[_ArchiveSource] = []
    try:
        with os.scandir(directory_descriptor) as entries:
            for entry in entries:
                if len(sources) >= MAX_ARCHIVE_OBJECTS:
                    _raise("BACKUP_SOURCE_INVALID")
                if _OBJECT_KEY_PATTERN.fullmatch(entry.name) is None:
                    _raise("BACKUP_SOURCE_INVALID")
                details = entry.stat(follow_symlinks=False)
                if (
                    not entry.is_file(follow_symlinks=False)
                    or not stat.S_ISREG(details.st_mode)
                    or _mode(details) != PRIVATE_FILE_MODE
                    or details.st_nlink != 1
                    or details.st_size > MAX_ARCHIVE_BYTES
                ):
                    _raise("BACKUP_SOURCE_INVALID")
                sources.append(
                    _ArchiveSource(
                        name=entry.name,
                        size=details.st_size,
                        device=details.st_dev,
                        inode=details.st_ino,
                        modified_ns=details.st_mtime_ns,
                        changed_ns=details.st_ctime_ns,
                    )
                )
    except BackupContractError:
        raise
    except OSError:
        _raise("BACKUP_SOURCE_INVALID")
    sources.sort(key=lambda source: source.name)
    return sources, initial_directory


def _open_archive_directory(path: Path) -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        return os.open(path, flags)
    except OSError:
        _raise("BACKUP_SOURCE_INVALID")


def _write_archive_tar(archive_root: Path, destination_path: Path) -> ArchiveDigest:
    directory_descriptor = _open_archive_directory(archive_root)
    try:
        sources, initial_directory = _scan_archive_sources(directory_descriptor)
        with (
            _exclusive_private_writer(destination_path) as destination,
            tarfile.open(
                fileobj=destination,
                mode="w",
                format=tarfile.USTAR_FORMAT,
            ) as bundle,
        ):
            for source in sources:
                flags = os.O_RDONLY | os.O_CLOEXEC
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                source_descriptor = -1
                try:
                    source_descriptor = os.open(source.name, flags, dir_fd=directory_descriptor)
                    before = os.fstat(source_descriptor)
                    if not _archive_source_matches(before, source):
                        _raise("BACKUP_SOURCE_INVALID")
                    with os.fdopen(source_descriptor, "rb", closefd=True) as archive_object:
                        source_descriptor = -1
                        member = tarfile.TarInfo(source.name)
                        member.size = source.size
                        member.mode = PRIVATE_FILE_MODE
                        member.uid = 0
                        member.gid = 0
                        member.uname = ""
                        member.gname = ""
                        member.mtime = 0
                        bundle.addfile(member, archive_object)
                        after = os.fstat(archive_object.fileno())
                        if archive_object.tell() != source.size or not _archive_source_matches(
                            after, source
                        ):
                            _raise("BACKUP_SOURCE_INVALID")
                except BackupContractError:
                    raise
                except OSError, tarfile.TarError:
                    _raise("BACKUP_SOURCE_INVALID")
                finally:
                    if source_descriptor >= 0:
                        os.close(source_descriptor)
        final_directory = os.fstat(directory_descriptor)
        if (
            final_directory.st_dev != initial_directory.st_dev
            or final_directory.st_ino != initial_directory.st_ino
            or final_directory.st_mtime_ns != initial_directory.st_mtime_ns
            or final_directory.st_ctime_ns != initial_directory.st_ctime_ns
        ):
            _raise("BACKUP_SOURCE_INVALID")
    finally:
        os.close(directory_descriptor)
    sha256, size = _digest_private_file(destination_path, code="BACKUP_OPERATION_FAILED")
    return ArchiveDigest(ARCHIVE_ARTIFACT_NAME, sha256, size, len(sources))


def _digest_private_file(
    path: Path,
    *,
    code: str,
    required_magic: bytes | None = None,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    first = True
    with _private_reader(path, code=code) as (source, initial):
        while chunk := source.read(_READ_CHUNK_BYTES):
            if first:
                first = False
                if required_magic is not None and not chunk.startswith(required_magic):
                    _raise(code)
            total += len(chunk)
            digest.update(chunk)
        final = os.fstat(source.fileno())
        if (
            total != initial.st_size
            or final.st_size != initial.st_size
            or final.st_dev != initial.st_dev
            or final.st_ino != initial.st_ino
            or final.st_mtime_ns != initial.st_mtime_ns
            or final.st_ctime_ns != initial.st_ctime_ns
        ):
            _raise(code)
    if required_magic is not None and total < len(required_magic):
        _raise(code)
    return digest.hexdigest(), total


def _artifact_payload(artifact: ArtifactDigest) -> dict[str, object]:
    return {"file": artifact.file, "sha256": artifact.sha256, "size": artifact.size}


def _archive_payload(archive: ArchiveDigest) -> dict[str, object]:
    return {
        "file": archive.file,
        "object_count": archive.object_count,
        "sha256": archive.sha256,
        "size": archive.size,
    }


def _unsigned_manifest_payload(
    *,
    key_version: str,
    database: ArtifactDigest,
    archive: ArchiveDigest,
) -> dict[str, object]:
    return {
        "archive": _archive_payload(archive),
        "database": _artifact_payload(database),
        "key_version": key_version,
        "schema_version": BACKUP_SCHEMA_VERSION,
    }


def _canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _manifest_hmac(master_key: MasterKey, payload: dict[str, object]) -> str:
    authentication_key = hmac.new(
        master_key.material,
        _MANIFEST_AUTH_CONTEXT,
        hashlib.sha256,
    ).digest()
    return hmac.new(authentication_key, _canonical_json(payload), hashlib.sha256).hexdigest()


def _build_manifest(
    *,
    master_key: MasterKey,
    database: ArtifactDigest,
    archive: ArchiveDigest,
) -> BackupManifest:
    unsigned = _unsigned_manifest_payload(
        key_version=master_key.key_version,
        database=database,
        archive=archive,
    )
    return BackupManifest(
        schema_version=BACKUP_SCHEMA_VERSION,
        key_version=master_key.key_version,
        database=database,
        archive=archive,
        manifest_hmac_sha256=_manifest_hmac(master_key, unsigned),
    )


def _write_manifest(path: Path, manifest: BackupManifest) -> None:
    payload = _unsigned_manifest_payload(
        key_version=manifest.key_version,
        database=manifest.database,
        archive=manifest.archive,
    )
    payload["manifest_hmac_sha256"] = manifest.manifest_hmac_sha256
    encoded = _canonical_json(payload) + b"\n"
    if len(encoded) > MAX_MANIFEST_BYTES:
        _raise("BACKUP_OPERATION_FAILED")
    with _exclusive_private_writer(path) as destination:
        destination.write(encoded)


def _cleanup_created_directory(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        return
    except OSError:
        _raise("BACKUP_CLEANUP_FAILED")


def _create_destination(path: Path) -> None:
    try:
        os.mkdir(path, PRIVATE_DIRECTORY_MODE)
        os.chmod(path, PRIVATE_DIRECTORY_MODE)
    except OSError:
        _raise("BACKUP_DESTINATION_INVALID")


def _fsync_directory(path: Path) -> None:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        os.fsync(descriptor)
    except OSError:
        _raise("BACKUP_OPERATION_FAILED")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def capture_backup_set(
    *,
    database_dump: Path,
    archive_root: Path,
    master_key_file: Path,
    destination: Path,
) -> BackupManifest:
    """Capture pre-created, quiesced inputs into a new authenticated backup directory."""

    database_resolved = _require_outside_repository(
        database_dump,
        strict=True,
        code="BACKUP_SOURCE_INVALID",
    )
    archive_resolved = _require_private_directory(archive_root, code="BACKUP_SOURCE_INVALID")
    key_resolved = _require_outside_repository(
        master_key_file,
        strict=True,
        code="BACKUP_KEY_UNAVAILABLE",
    )
    destination_path = _require_new_private_destination(destination)
    destination_resolved = _resolved(
        destination_path,
        strict=False,
        code="BACKUP_DESTINATION_INVALID",
    )
    if (
        _is_within(database_resolved, archive_resolved)
        or _is_within(archive_resolved, database_resolved)
        or _is_within(key_resolved, archive_resolved)
        or _is_within(archive_resolved, key_resolved)
        or key_resolved == database_resolved
    ):
        _raise("BACKUP_SOURCE_INVALID")
    if (
        _is_within(destination_resolved, archive_resolved)
        or _is_within(archive_resolved, destination_resolved)
        or _is_within(destination_resolved, database_resolved)
    ):
        _raise("BACKUP_DESTINATION_INVALID")
    master_key = _load_master_key(master_key_file)
    _create_destination(destination_path)
    try:
        database = _copy_database_dump(
            database_dump,
            destination_path / DATABASE_ARTIFACT_NAME,
        )
        archive = _write_archive_tar(
            archive_root,
            destination_path / ARCHIVE_ARTIFACT_NAME,
        )
        manifest = _build_manifest(
            master_key=master_key,
            database=database,
            archive=archive,
        )
        _write_manifest(destination_path / MANIFEST_NAME, manifest)
        _fsync_directory(destination_path)
        _fsync_directory(destination_path.parent)
        return manifest
    except BackupContractError:
        _cleanup_created_directory(destination_path)
        raise
    except OSError, tarfile.TarError:
        _cleanup_created_directory(destination_path)
        _raise("BACKUP_OPERATION_FAILED")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _mapping(value: object, expected_keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected_keys:
        _raise("BACKUP_INTEGRITY_ERROR")
    return cast(dict[str, object], value)


def _nonnegative_integer(value: object, *, maximum: int | None = None) -> int:
    if type(value) is not int:
        _raise("BACKUP_INTEGRITY_ERROR")
    number = value
    if number < 0:
        _raise("BACKUP_INTEGRITY_ERROR")
    if maximum is not None and number > maximum:
        _raise("BACKUP_INTEGRITY_ERROR")
    return number


def _parse_artifact(value: object, *, expected_file: str) -> ArtifactDigest:
    payload = _mapping(value, {"file", "sha256", "size"})
    filename = payload["file"]
    sha256 = payload["sha256"]
    if not isinstance(filename, str) or filename != expected_file:
        _raise("BACKUP_INTEGRITY_ERROR")
    if not isinstance(sha256, str):
        _raise("BACKUP_INTEGRITY_ERROR")
    if _SHA256_PATTERN.fullmatch(sha256) is None:
        _raise("BACKUP_INTEGRITY_ERROR")
    return ArtifactDigest(filename, sha256, _nonnegative_integer(payload["size"]))


def _parse_archive(value: object) -> ArchiveDigest:
    payload = _mapping(value, {"file", "object_count", "sha256", "size"})
    filename = payload["file"]
    sha256 = payload["sha256"]
    if not isinstance(filename, str) or filename != ARCHIVE_ARTIFACT_NAME:
        _raise("BACKUP_INTEGRITY_ERROR")
    if not isinstance(sha256, str):
        _raise("BACKUP_INTEGRITY_ERROR")
    if _SHA256_PATTERN.fullmatch(sha256) is None:
        _raise("BACKUP_INTEGRITY_ERROR")
    return ArchiveDigest(
        filename,
        sha256,
        _nonnegative_integer(payload["size"]),
        _nonnegative_integer(payload["object_count"], maximum=MAX_ARCHIVE_OBJECTS),
    )


def _read_manifest(path: Path) -> BackupManifest:
    try:
        with _private_reader(path, code="BACKUP_INTEGRITY_ERROR") as (source, details):
            if details.st_size > MAX_MANIFEST_BYTES:
                _raise("BACKUP_INTEGRITY_ERROR")
            encoded = source.read(MAX_MANIFEST_BYTES + 1)
        raw: object = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except BackupContractError:
        raise
    except OSError, UnicodeError, ValueError, json.JSONDecodeError:
        _raise("BACKUP_INTEGRITY_ERROR")
    payload = _mapping(
        raw,
        {
            "archive",
            "database",
            "key_version",
            "manifest_hmac_sha256",
            "schema_version",
        },
    )
    if payload["schema_version"] != BACKUP_SCHEMA_VERSION:
        _raise("BACKUP_INTEGRITY_ERROR")
    key_version = payload["key_version"]
    manifest_hmac = payload["manifest_hmac_sha256"]
    if not isinstance(key_version, str) or _KEY_VERSION_PATTERN.fullmatch(key_version) is None:
        _raise("BACKUP_INTEGRITY_ERROR")
    if not isinstance(manifest_hmac, str) or _SHA256_PATTERN.fullmatch(manifest_hmac) is None:
        _raise("BACKUP_INTEGRITY_ERROR")
    return BackupManifest(
        schema_version=BACKUP_SCHEMA_VERSION,
        key_version=key_version,
        database=_parse_artifact(payload["database"], expected_file=DATABASE_ARTIFACT_NAME),
        archive=_parse_archive(payload["archive"]),
        manifest_hmac_sha256=manifest_hmac,
    )


def _expected_tar_size(member_sizes: Sequence[int]) -> int:
    body = sum(
        tarfile.BLOCKSIZE
        + ((size + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE) * tarfile.BLOCKSIZE
        for size in member_sizes
    )
    minimum = body + (2 * tarfile.BLOCKSIZE)
    return ((minimum + tarfile.RECORDSIZE - 1) // tarfile.RECORDSIZE) * tarfile.RECORDSIZE


def _inspect_archive_tar(path: Path, *, expected_count: int, expected_size: int) -> None:
    seen: set[str] = set()
    sizes: list[int] = []
    try:
        with (
            _private_reader(path, code="BACKUP_INTEGRITY_ERROR") as (
                source,
                _,
            ),
            tarfile.open(fileobj=source, mode="r:") as bundle,
        ):
            while member := bundle.next():
                if len(seen) >= MAX_ARCHIVE_OBJECTS:
                    _raise("BACKUP_INTEGRITY_ERROR")
                if (
                    _OBJECT_KEY_PATTERN.fullmatch(member.name) is None
                    or member.name in seen
                    or not member.isreg()
                    or member.mode != PRIVATE_FILE_MODE
                    or member.uid != 0
                    or member.gid != 0
                    or member.size > MAX_ARCHIVE_BYTES
                ):
                    _raise("BACKUP_INTEGRITY_ERROR")
                extracted = bundle.extractfile(member)
                if extracted is None:
                    _raise("BACKUP_INTEGRITY_ERROR")
                remaining = member.size
                while remaining:
                    chunk = extracted.read(min(_READ_CHUNK_BYTES, remaining))
                    if not chunk:
                        _raise("BACKUP_INTEGRITY_ERROR")
                    remaining -= len(chunk)
                if extracted.read(1):
                    _raise("BACKUP_INTEGRITY_ERROR")
                seen.add(member.name)
                sizes.append(member.size)
    except BackupContractError:
        raise
    except OSError, tarfile.TarError, EOFError:
        _raise("BACKUP_INTEGRITY_ERROR")
    if len(seen) != expected_count or _expected_tar_size(sizes) != expected_size:
        _raise("BACKUP_INTEGRITY_ERROR")


def _verify_directory_shape(root: Path) -> None:
    expected = {ARCHIVE_ARTIFACT_NAME, DATABASE_ARTIFACT_NAME, MANIFEST_NAME}
    try:
        names = {entry.name for entry in os.scandir(root)}
    except OSError:
        _raise("BACKUP_INTEGRITY_ERROR")
    if names != expected:
        _raise("BACKUP_INTEGRITY_ERROR")
    for name in expected:
        details = _lstat(root / name, code="BACKUP_INTEGRITY_ERROR")
        if not stat.S_ISREG(details.st_mode) or _mode(details) != PRIVATE_FILE_MODE:
            _raise("BACKUP_INTEGRITY_ERROR")


def verify_backup_set(backup_root: Path, *, master_key_file: Path) -> BackupManifest:
    """Verify modes, manifest authentication, hashes, dump magic, and archive shape."""

    root = _require_private_directory(backup_root, code="BACKUP_INTEGRITY_ERROR")
    _verify_directory_shape(root)
    master_key = _load_master_key(master_key_file)
    manifest = _read_manifest(root / MANIFEST_NAME)
    if manifest.key_version != master_key.key_version:
        _raise("BACKUP_KEY_MISMATCH")
    unsigned = _unsigned_manifest_payload(
        key_version=manifest.key_version,
        database=manifest.database,
        archive=manifest.archive,
    )
    expected_hmac = _manifest_hmac(master_key, unsigned)
    if not hmac.compare_digest(manifest.manifest_hmac_sha256, expected_hmac):
        _raise("BACKUP_INTEGRITY_ERROR")
    database_sha256, database_size = _digest_private_file(
        root / manifest.database.file,
        code="BACKUP_INTEGRITY_ERROR",
        required_magic=POSTGRES_CUSTOM_MAGIC,
    )
    if (database_sha256, database_size) != (manifest.database.sha256, manifest.database.size):
        _raise("BACKUP_INTEGRITY_ERROR")
    archive_sha256, archive_size = _digest_private_file(
        root / manifest.archive.file,
        code="BACKUP_INTEGRITY_ERROR",
    )
    if (archive_sha256, archive_size) != (manifest.archive.sha256, manifest.archive.size):
        _raise("BACKUP_INTEGRITY_ERROR")
    _inspect_archive_tar(
        root / manifest.archive.file,
        expected_count=manifest.archive.object_count,
        expected_size=manifest.archive.size,
    )
    return manifest


def _copy_verified_file(
    source_path: Path,
    destination_path: Path,
    *,
    expected: ArtifactDigest,
) -> None:
    digest = hashlib.sha256()
    total = 0
    with (
        _private_reader(source_path, code="BACKUP_INTEGRITY_ERROR") as (
            source,
            initial,
        ),
        _exclusive_private_writer(destination_path) as destination,
    ):
        while chunk := source.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
            total += len(chunk)
            destination.write(chunk)
        final = os.fstat(source.fileno())
    if (
        digest.hexdigest() != expected.sha256
        or total != expected.size
        or final.st_dev != initial.st_dev
        or final.st_ino != initial.st_ino
        or final.st_size != initial.st_size
        or final.st_mtime_ns != initial.st_mtime_ns
        or final.st_ctime_ns != initial.st_ctime_ns
    ):
        _raise("BACKUP_INTEGRITY_ERROR")


def _extract_archive_tar(
    source_path: Path,
    destination_root: Path,
    *,
    expected: ArchiveDigest,
) -> int:
    count = 0
    try:
        with _private_reader(source_path, code="BACKUP_INTEGRITY_ERROR") as (source, initial):
            digest = hashlib.sha256()
            total = 0
            while chunk := source.read(_READ_CHUNK_BYTES):
                digest.update(chunk)
                total += len(chunk)
            after_digest = os.fstat(source.fileno())
            if (
                digest.hexdigest() != expected.sha256
                or total != expected.size
                or after_digest.st_dev != initial.st_dev
                or after_digest.st_ino != initial.st_ino
                or after_digest.st_size != initial.st_size
                or after_digest.st_mtime_ns != initial.st_mtime_ns
                or after_digest.st_ctime_ns != initial.st_ctime_ns
            ):
                _raise("BACKUP_INTEGRITY_ERROR")
            source.seek(0)
            with tarfile.open(fileobj=source, mode="r:") as bundle:
                while member := bundle.next():
                    if (
                        count >= MAX_ARCHIVE_OBJECTS
                        or _OBJECT_KEY_PATTERN.fullmatch(member.name) is None
                        or not member.isreg()
                        or member.mode != PRIVATE_FILE_MODE
                        or member.size > MAX_ARCHIVE_BYTES
                    ):
                        _raise("BACKUP_INTEGRITY_ERROR")
                    extracted = bundle.extractfile(member)
                    if extracted is None:
                        _raise("BACKUP_INTEGRITY_ERROR")
                    with _exclusive_private_writer(destination_root / member.name) as destination:
                        remaining = member.size
                        while remaining:
                            chunk = extracted.read(min(_READ_CHUNK_BYTES, remaining))
                            if not chunk:
                                _raise("BACKUP_INTEGRITY_ERROR")
                            remaining -= len(chunk)
                            destination.write(chunk)
                        if extracted.read(1):
                            _raise("BACKUP_INTEGRITY_ERROR")
                    count += 1
            final = os.fstat(source.fileno())
            if (
                final.st_dev != initial.st_dev
                or final.st_ino != initial.st_ino
                or final.st_size != initial.st_size
                or final.st_mtime_ns != initial.st_mtime_ns
                or final.st_ctime_ns != initial.st_ctime_ns
            ):
                _raise("BACKUP_INTEGRITY_ERROR")
    except BackupContractError:
        raise
    except OSError, tarfile.TarError, EOFError:
        _raise("BACKUP_INTEGRITY_ERROR")
    return count


def materialize_restore_inputs(
    *,
    backup_root: Path,
    master_key_file: Path,
    destination: Path,
) -> RestoreInputs:
    """Copy verified artifacts into a fresh location; do not restore a database."""

    manifest = verify_backup_set(backup_root, master_key_file=master_key_file)
    destination_path = _require_new_private_destination(destination)
    backup_resolved = _resolved(backup_root, strict=True, code="BACKUP_INTEGRITY_ERROR")
    destination_resolved = _resolved(
        destination_path,
        strict=False,
        code="BACKUP_DESTINATION_INVALID",
    )
    if _is_within(destination_resolved, backup_resolved) or _is_within(
        backup_resolved, destination_resolved
    ):
        _raise("BACKUP_DESTINATION_INVALID")
    _create_destination(destination_path)
    archive_destination = destination_path / "archive"
    try:
        os.mkdir(archive_destination, PRIVATE_DIRECTORY_MODE)
        os.chmod(archive_destination, PRIVATE_DIRECTORY_MODE)
        database_destination = destination_path / DATABASE_ARTIFACT_NAME
        _copy_verified_file(
            Path(backup_root) / manifest.database.file,
            database_destination,
            expected=manifest.database,
        )
        extracted = _extract_archive_tar(
            Path(backup_root) / manifest.archive.file,
            archive_destination,
            expected=manifest.archive,
        )
        if extracted != manifest.archive.object_count:
            _raise("BACKUP_INTEGRITY_ERROR")
        _fsync_directory(archive_destination)
        _fsync_directory(destination_path)
        _fsync_directory(destination_path.parent)
        return RestoreInputs(database_dump=database_destination, archive_root=archive_destination)
    except BackupContractError:
        _cleanup_created_directory(destination_path)
        raise
    except OSError, tarfile.TarError:
        _cleanup_created_directory(destination_path)
        _raise("BACKUP_OPERATION_FAILED")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)

    subparsers.add_parser("capture", help="bundle already-quiesced backup inputs")
    subparsers.add_parser("verify", help="verify one backup set")
    subparsers.add_parser(
        "materialize",
        help="prepare fresh inputs for a separately approved restore",
    )
    return parser


def _environment_path(environ: Mapping[str, str], name: str) -> Path:
    value = environ.get(name)
    if not value:
        _raise("BACKUP_CONFIGURATION_ERROR")
    return Path(value)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Run the offline operator interface with path-free status output."""

    arguments = _parser().parse_args(argv)
    values = os.environ if environ is None else environ
    try:
        if arguments.operation == "capture":
            capture_backup_set(
                database_dump=_environment_path(values, "FAMILYCARE_BACKUP_DATABASE_DUMP"),
                archive_root=_environment_path(
                    values,
                    "FAMILYCARE_BACKUP_ARCHIVE_SNAPSHOT_ROOT",
                ),
                master_key_file=_environment_path(
                    values,
                    "FAMILYCARE_ARCHIVE_MASTER_KEY_FILE",
                ),
                destination=_environment_path(values, "FAMILYCARE_BACKUP_DESTINATION"),
            )
            print("BACKUP_CAPTURED")
        elif arguments.operation == "verify":
            verify_backup_set(
                _environment_path(values, "FAMILYCARE_BACKUP_ROOT"),
                master_key_file=_environment_path(
                    values,
                    "FAMILYCARE_ARCHIVE_MASTER_KEY_FILE",
                ),
            )
            print("BACKUP_VERIFIED")
        else:
            materialize_restore_inputs(
                backup_root=_environment_path(values, "FAMILYCARE_BACKUP_ROOT"),
                master_key_file=_environment_path(
                    values,
                    "FAMILYCARE_ARCHIVE_MASTER_KEY_FILE",
                ),
                destination=_environment_path(
                    values,
                    "FAMILYCARE_RESTORE_INPUT_DESTINATION",
                ),
            )
            print("RESTORE_INPUTS_MATERIALIZED")
    except BackupContractError as error:
        print(error.code, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "REPOSITORY_ROOT",
    "ArchiveDigest",
    "ArtifactDigest",
    "BackupContractError",
    "BackupManifest",
    "RestoreInputs",
    "capture_backup_set",
    "main",
    "materialize_restore_inputs",
    "verify_backup_set",
]
