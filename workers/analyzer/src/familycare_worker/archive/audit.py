"""Count-only, read-only reconciliation of managed archive metadata and ciphertext files."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Never

import psycopg
from psycopg.rows import dict_row

from familycare_worker.archive.crypto import MAX_ARCHIVE_BYTES
from familycare_worker.jobs import psycopg_database_url

MAX_AUDIT_ENTRIES = 100_000
PRIVATE_ARCHIVE_DIRECTORY_MODE = 0o700
PRIVATE_ARCHIVE_FILE_MODE = 0o600
_OBJECT_KEY_PATTERN = re.compile(r"^[a-f0-9]{32}$")
_TEMPORARY_KEY_PATTERN = re.compile(r"^\.tmp-[a-f0-9]{32}$")
_READ_ONLY_OPTIONS = "-c default_transaction_read_only=on"
_READ_ONLY_TRANSACTION = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
_ARCHIVE_REFERENCE_QUERY = """
SELECT object_key, ciphertext_size
FROM managed_archives
ORDER BY object_key
LIMIT %s
"""


class ArchiveAuditError(RuntimeError):
    """One sanitized archive audit failure that never includes a key or path."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _raise(code: str) -> Never:
    raise ArchiveAuditError(code) from None


@dataclass(frozen=True)
class ArchiveReference:
    """One validated database reference, hidden from representations and reports."""

    object_key: str = field(repr=False)
    ciphertext_size: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.object_key, str)
            or _OBJECT_KEY_PATTERN.fullmatch(self.object_key) is None
            or isinstance(self.ciphertext_size, bool)
            or not isinstance(self.ciphertext_size, int)
            or not 0 <= self.ciphertext_size <= MAX_ARCHIVE_BYTES
        ):
            _raise("ARCHIVE_AUDIT_REFERENCE_INVALID")


@dataclass(frozen=True)
class ArchiveAuditReport:
    """Stable aggregate findings; object identifiers and paths are intentionally absent."""

    database_reference_count: int
    archive_object_count: int
    matched: int
    missing_references: int
    size_mismatches: int
    unreferenced_objects: int
    temporary_entries: int
    unexpected_entries: int

    @property
    def is_clean(self) -> bool:
        """Return true only when every durable reference and object agrees."""

        return not any(
            (
                self.missing_references,
                self.size_mismatches,
                self.unreferenced_objects,
                self.temporary_entries,
                self.unexpected_entries,
            )
        )

    def to_payload(self) -> dict[str, int | str]:
        """Return the only supported output shape: status and aggregate counts."""

        return {
            "archive_object_count": self.archive_object_count,
            "database_reference_count": self.database_reference_count,
            "matched": self.matched,
            "missing_references": self.missing_references,
            "size_mismatches": self.size_mismatches,
            "status": "clean" if self.is_clean else "findings",
            "temporary_entries": self.temporary_entries,
            "unexpected_entries": self.unexpected_entries,
            "unreferenced_objects": self.unreferenced_objects,
        }


@dataclass(frozen=True)
class _ArchiveScan:
    objects: dict[str, int] = field(repr=False)
    temporary_entries: int
    unexpected_entries: int


ReferenceLoader = Callable[[str], Iterable[ArchiveReference]]


def _mode(details: os.stat_result) -> int:
    return stat.S_IMODE(details.st_mode)


def _open_archive_root(path: Path) -> tuple[int, os.stat_result]:
    candidate = Path(path)
    if not candidate.is_absolute():
        _raise("ARCHIVE_AUDIT_SOURCE_INVALID")
    try:
        path_details = candidate.lstat()
        if (
            not stat.S_ISDIR(path_details.st_mode)
            or _mode(path_details) != PRIVATE_ARCHIVE_DIRECTORY_MODE
        ):
            _raise("ARCHIVE_AUDIT_SOURCE_INVALID")
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(candidate, flags)
        details = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(details.st_mode)
            or _mode(details) != PRIVATE_ARCHIVE_DIRECTORY_MODE
            or details.st_dev != path_details.st_dev
            or details.st_ino != path_details.st_ino
        ):
            os.close(descriptor)
            _raise("ARCHIVE_AUDIT_SOURCE_INVALID")
        return descriptor, details
    except ArchiveAuditError:
        raise
    except OSError:
        _raise("ARCHIVE_AUDIT_SOURCE_INVALID")


def _is_private_regular(details: os.stat_result) -> bool:
    return (
        stat.S_ISREG(details.st_mode)
        and _mode(details) == PRIVATE_ARCHIVE_FILE_MODE
        and details.st_nlink == 1
        and 0 <= details.st_size <= MAX_ARCHIVE_BYTES
    )


def _scan_archive(path: Path) -> _ArchiveScan:
    descriptor, initial = _open_archive_root(path)
    objects: dict[str, int] = {}
    temporary_entries = 0
    unexpected_entries = 0
    entry_count = 0
    try:
        try:
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    entry_count += 1
                    if entry_count > MAX_AUDIT_ENTRIES:
                        _raise("ARCHIVE_AUDIT_SOURCE_INVALID")
                    details = entry.stat(follow_symlinks=False)
                    private_regular = entry.is_file(follow_symlinks=False) and _is_private_regular(
                        details
                    )
                    if _OBJECT_KEY_PATTERN.fullmatch(entry.name) is not None:
                        if private_regular:
                            objects[entry.name] = details.st_size
                        else:
                            unexpected_entries += 1
                    elif _TEMPORARY_KEY_PATTERN.fullmatch(entry.name) is not None:
                        if private_regular:
                            temporary_entries += 1
                        else:
                            unexpected_entries += 1
                    else:
                        unexpected_entries += 1
            final = os.fstat(descriptor)
        except ArchiveAuditError:
            raise
        except OSError:
            _raise("ARCHIVE_AUDIT_SOURCE_INVALID")
        if (
            final.st_dev != initial.st_dev
            or final.st_ino != initial.st_ino
            or final.st_mtime_ns != initial.st_mtime_ns
            or final.st_ctime_ns != initial.st_ctime_ns
        ):
            _raise("ARCHIVE_AUDIT_SNAPSHOT_CHANGED")
    finally:
        os.close(descriptor)
    return _ArchiveScan(objects, temporary_entries, unexpected_entries)


def _reference_map(references: Iterable[ArchiveReference]) -> dict[str, int]:
    result: dict[str, int] = {}
    for reference in references:
        if not isinstance(reference, ArchiveReference) or len(result) >= MAX_AUDIT_ENTRIES:
            _raise("ARCHIVE_AUDIT_REFERENCE_INVALID")
        if reference.object_key in result:
            _raise("ARCHIVE_AUDIT_REFERENCE_INVALID")
        result[reference.object_key] = reference.ciphertext_size
    return result


def reconcile_archive(
    archive_root: Path,
    references: Iterable[ArchiveReference],
) -> ArchiveAuditReport:
    """Compare a read-only filesystem snapshot with validated database references."""

    reference_sizes = _reference_map(references)
    scan = _scan_archive(archive_root)
    matched = 0
    missing_references = 0
    size_mismatches = 0
    for object_key, expected_size in reference_sizes.items():
        actual_size = scan.objects.get(object_key)
        if actual_size is None:
            missing_references += 1
        elif actual_size != expected_size:
            size_mismatches += 1
        else:
            matched += 1
    unreferenced_objects = sum(object_key not in reference_sizes for object_key in scan.objects)
    return ArchiveAuditReport(
        database_reference_count=len(reference_sizes),
        archive_object_count=len(scan.objects),
        matched=matched,
        missing_references=missing_references,
        size_mismatches=size_mismatches,
        unreferenced_objects=unreferenced_objects,
        temporary_entries=scan.temporary_entries,
        unexpected_entries=scan.unexpected_entries,
    )


def _reference_from_row(row: Mapping[str, Any]) -> ArchiveReference:
    try:
        return ArchiveReference(
            object_key=row["object_key"],
            ciphertext_size=row["ciphertext_size"],
        )
    except KeyError, TypeError:
        _raise("ARCHIVE_AUDIT_REFERENCE_INVALID")


def load_archive_references(database_url: str) -> tuple[ArchiveReference, ...]:
    """Read only object keys and sizes in a bounded repeatable-read transaction."""

    try:
        connection_url = psycopg_database_url(database_url)
        with psycopg.connect(
            connection_url,
            row_factory=dict_row,
            options=_READ_ONLY_OPTIONS,
            application_name="familycare-archive-audit",
        ) as connection:
            connection.execute(_READ_ONLY_TRANSACTION)
            rows = connection.execute(
                _ARCHIVE_REFERENCE_QUERY,
                (MAX_AUDIT_ENTRIES + 1,),
            ).fetchall()
            connection.rollback()
    except psycopg.Error, ValueError:
        _raise("ARCHIVE_AUDIT_DATABASE_UNAVAILABLE")
    if len(rows) > MAX_AUDIT_ENTRIES:
        _raise("ARCHIVE_AUDIT_REFERENCE_INVALID")
    return tuple(_reference_from_row(row) for row in rows)


def audit_from_environment(
    environ: Mapping[str, str],
    *,
    reference_loader: ReferenceLoader = load_archive_references,
) -> ArchiveAuditReport:
    """Resolve the two existing Worker settings without logging their values."""

    database_url = environ.get("FAMILYCARE_DATABASE_URL")
    archive_root = environ.get("FAMILYCARE_ARCHIVE_ROOT")
    if not database_url or not archive_root:
        _raise("ARCHIVE_AUDIT_CONFIGURATION_ERROR")
    return reconcile_archive(Path(archive_root), reference_loader(database_url))


def _error_payload(error: ArchiveAuditError) -> str:
    return json.dumps(
        {"error": error.code, "status": "error"},
        separators=(",", ":"),
        sort_keys=True,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    reference_loader: ReferenceLoader = load_archive_references,
) -> int:
    """Print aggregate JSON only; return 1 for findings and 2 for audit errors."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        error = ArchiveAuditError("ARCHIVE_AUDIT_CONFIGURATION_ERROR")
        print(_error_payload(error), file=sys.stderr)
        return 2
    values = os.environ if environ is None else environ
    try:
        report = audit_from_environment(values, reference_loader=reference_loader)
    except ArchiveAuditError as error:
        print(_error_payload(error), file=sys.stderr)
        return 2
    print(json.dumps(report.to_payload(), separators=(",", ":"), sort_keys=True))
    return 0 if report.is_clean else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ArchiveAuditError",
    "ArchiveAuditReport",
    "ArchiveReference",
    "audit_from_environment",
    "load_archive_references",
    "main",
    "reconcile_archive",
]
