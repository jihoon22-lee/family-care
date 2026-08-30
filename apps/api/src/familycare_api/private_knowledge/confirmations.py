"""Protected exact subject bindings and append-only enrollment confirmations."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
SubjectKey = Annotated[str, StringConstraints(min_length=1, max_length=160)]
ContractKey = Annotated[str, StringConstraints(min_length=1, max_length=240)]
ReasonCode = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$"),
]
NonNegativeInt = Annotated[int, Field(ge=0)]

_SCHEMA_VERSION = "private-knowledge-confirmation.sol-v1"
_REPORT_SCHEMA_VERSION = "private-knowledge-confirmation-dry-run.v1"
_AUTHORITY = "USER_CONFIRMED_CURRENT_ENROLLMENT"
_MAX_FILE_BYTES = 4 * 1024 * 1024


class ConfirmationErrorCode(StrEnum):
    MANIFEST_PATH_INVALID = "MANIFEST_PATH_INVALID"
    MANIFEST_PARENT_MODE_INVALID = "MANIFEST_PARENT_MODE_INVALID"
    MANIFEST_FILE_MODE_INVALID = "MANIFEST_FILE_MODE_INVALID"
    MANIFEST_FILE_NOT_REGULAR = "MANIFEST_FILE_NOT_REGULAR"
    MANIFEST_FILE_SIZE_INVALID = "MANIFEST_FILE_SIZE_INVALID"
    MANIFEST_FILE_CHANGED = "MANIFEST_FILE_CHANGED"
    MANIFEST_INVALID = "MANIFEST_INVALID"
    DUPLICATE_SUBJECT_KEY = "DUPLICATE_SUBJECT_KEY"
    DUPLICATE_CONTRACT_KEY = "DUPLICATE_CONTRACT_KEY"
    MANIFEST_SCOPE_MISMATCH = "MANIFEST_SCOPE_MISMATCH"
    REPORT_PATH_INVALID = "REPORT_PATH_INVALID"
    REPORT_PARENT_MODE_INVALID = "REPORT_PARENT_MODE_INVALID"
    REPORT_FILE_MODE_INVALID = "REPORT_FILE_MODE_INVALID"
    REPORT_INVALID = "REPORT_INVALID"
    REPORT_DIGEST_MISMATCH = "REPORT_DIGEST_MISMATCH"
    APPROVAL_INVALID = "APPROVAL_INVALID"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    CURRENT_SNAPSHOT_NOT_FOUND = "CURRENT_SNAPSHOT_NOT_FOUND"
    PACKAGE_DIGEST_MISMATCH = "PACKAGE_DIGEST_MISMATCH"
    ACTOR_NOT_FOUND = "ACTOR_NOT_FOUND"
    FAMILY_MEMBER_NOT_FOUND = "FAMILY_MEMBER_NOT_FOUND"
    SUBJECT_SET_MISMATCH = "SUBJECT_SET_MISMATCH"
    CONTRACT_SET_MISMATCH = "CONTRACT_SET_MISMATCH"
    STALE_DRY_RUN = "STALE_DRY_RUN"
    APPLY_FAILED = "APPLY_FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


class ConfirmationError(RuntimeError):
    """Sanitized workflow error that never contains manifest values or paths."""

    def __init__(self, code: ConfirmationErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SubjectBinding(_StrictModel):
    source_subject_key: SubjectKey
    family_member_id: UUID


class ContractConfirmation(_StrictModel):
    canonical_policy_id: ContractKey
    decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    confirmed_status: Literal["active", "inactive", "lapsed", "terminated", "unknown"]
    reason_code: ReasonCode


class ConfirmationManifest(_StrictModel):
    schema_version: Literal["private-knowledge-confirmation.sol-v1"]
    package_digest_sha256: Sha256
    household_space_id: UUID
    confirmed_by: UUID
    status_as_of: date
    authority: Literal["USER_CONFIRMED_CURRENT_ENROLLMENT"]
    subjects: Annotated[list[SubjectBinding], Field(min_length=1, max_length=1_000)]
    contracts: Annotated[
        list[ContractConfirmation],
        Field(min_length=1, max_length=10_000),
    ]


@dataclass(frozen=True, slots=True)
class LoadedConfirmationManifest:
    value: ConfirmationManifest
    manifest_digest_sha256: str

    @property
    def package_digest_sha256(self) -> str:
        return self.value.package_digest_sha256

    @property
    def household_space_id(self) -> UUID:
        return self.value.household_space_id

    @property
    def confirmed_by(self) -> UUID:
        return self.value.confirmed_by

    @property
    def status_as_of(self) -> date:
        return self.value.status_as_of

    @property
    def authority(self) -> str:
        return self.value.authority

    @property
    def subjects(self) -> Sequence[SubjectBinding]:
        return self.value.subjects

    @property
    def contracts(self) -> Sequence[ContractConfirmation]:
        return self.value.contracts


class ConfirmationDryRunReport(_StrictModel):
    schema_version: Literal["private-knowledge-confirmation-dry-run.v1"]
    manifest_digest_sha256: Sha256
    package_digest_sha256: Sha256
    household_space_id: UUID
    current_run_id: UUID
    baseline_digest_sha256: Sha256
    operation: Literal["APPLY", "NO_OP"]
    subject_count: NonNegativeInt
    contract_count: NonNegativeInt
    binding_change_count: NonNegativeInt
    confirmation_insert_count: NonNegativeInt
    confirmation_supersede_count: NonNegativeInt
    report_digest_sha256: Sha256


class AppliedConfirmationSet(_StrictModel):
    run_id: UUID
    package_digest_sha256: Sha256
    subject_count: NonNegativeInt
    contract_count: NonNegativeInt
    current_confirmation_count: NonNegativeInt


class ConfirmationRepository(Protocol):
    def prepare_confirmation_dry_run(
        self,
        manifest: LoadedConfirmationManifest,
    ) -> ConfirmationDryRunReport: ...

    def apply_confirmations(
        self,
        manifest: LoadedConfirmationManifest,
        *,
        approved_report: ConfirmationDryRunReport,
    ) -> AppliedConfirmationSet: ...


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _read_protected_file(
    path: Path,
    *,
    repository_root: Path,
    is_report: bool,
) -> bytes:
    path_error = (
        ConfirmationErrorCode.REPORT_PATH_INVALID
        if is_report
        else ConfirmationErrorCode.MANIFEST_PATH_INVALID
    )
    parent_error = (
        ConfirmationErrorCode.REPORT_PARENT_MODE_INVALID
        if is_report
        else ConfirmationErrorCode.MANIFEST_PARENT_MODE_INVALID
    )
    mode_error = (
        ConfirmationErrorCode.REPORT_FILE_MODE_INVALID
        if is_report
        else ConfirmationErrorCode.MANIFEST_FILE_MODE_INVALID
    )
    if not path.is_absolute():
        raise ConfirmationError(path_error)
    try:
        repository = repository_root.resolve(strict=True)
        parent = path.parent.resolve(strict=True)
        parent_lstat = os.lstat(path.parent)
    except OSError:
        raise ConfirmationError(path_error) from None
    if _is_inside(path.resolve(strict=False), repository):
        raise ConfirmationError(path_error)
    if (
        not stat.S_ISDIR(parent_lstat.st_mode)
        or stat.S_ISLNK(parent_lstat.st_mode)
        or parent != path.parent
        or stat.S_IMODE(parent_lstat.st_mode) != 0o700
    ):
        raise ConfirmationError(parent_error)
    try:
        before = os.lstat(path)
    except OSError:
        raise ConfirmationError(path_error) from None
    if not stat.S_ISREG(before.st_mode):
        code = (
            ConfirmationErrorCode.REPORT_INVALID
            if is_report
            else ConfirmationErrorCode.MANIFEST_FILE_NOT_REGULAR
        )
        raise ConfirmationError(code)
    if stat.S_IMODE(before.st_mode) != 0o600:
        raise ConfirmationError(mode_error)
    if before.st_size > _MAX_FILE_BYTES:
        raise ConfirmationError(ConfirmationErrorCode.MANIFEST_FILE_SIZE_INVALID)

    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        code = (
            ConfirmationErrorCode.REPORT_INVALID
            if is_report
            else ConfirmationErrorCode.MANIFEST_FILE_NOT_REGULAR
        )
        raise ConfirmationError(code) from None
    try:
        opened = os.fstat(descriptor)
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        opened_identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        )
        if identity != opened_identity:
            raise ConfirmationError(ConfirmationErrorCode.MANIFEST_FILE_CHANGED)
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_FILE_BYTES:
                raise ConfirmationError(ConfirmationErrorCode.MANIFEST_FILE_SIZE_INVALID)
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        path_after = os.lstat(path)
    except OSError:
        raise ConfirmationError(ConfirmationErrorCode.MANIFEST_FILE_CHANGED) from None
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    path_identity = (
        path_after.st_dev,
        path_after.st_ino,
        path_after.st_size,
        path_after.st_mtime_ns,
    )
    if identity != after_identity or identity != path_identity:
        raise ConfirmationError(ConfirmationErrorCode.MANIFEST_FILE_CHANGED)
    return b"".join(chunks)


def load_confirmation_manifest(
    path: Path,
    *,
    repository_root: Path,
) -> LoadedConfirmationManifest:
    payload = _read_protected_file(
        path,
        repository_root=repository_root,
        is_report=False,
    )
    try:
        manifest = ConfirmationManifest.model_validate_json(payload)
    except ValidationError, ValueError:
        raise ConfirmationError(ConfirmationErrorCode.MANIFEST_INVALID) from None
    subject_keys = [row.source_subject_key for row in manifest.subjects]
    if len(subject_keys) != len(set(subject_keys)):
        raise ConfirmationError(ConfirmationErrorCode.DUPLICATE_SUBJECT_KEY)
    contract_keys = [row.canonical_policy_id for row in manifest.contracts]
    if len(contract_keys) != len(set(contract_keys)):
        raise ConfirmationError(ConfirmationErrorCode.DUPLICATE_CONTRACT_KEY)
    for row in manifest.contracts:
        if (row.decision == "MATCH") != (row.confirmed_status != "unknown"):
            raise ConfirmationError(ConfirmationErrorCode.MANIFEST_INVALID)
    digest = hashlib.sha256(
        b"familycare-private-confirmation-sol-v1\x00"
        + _canonical_json(manifest.model_dump(mode="json"))
    ).hexdigest()
    return LoadedConfirmationManifest(value=manifest, manifest_digest_sha256=digest)


def canonical_confirmation_report_digest(report: ConfirmationDryRunReport) -> str:
    payload = report.model_dump(mode="json")
    payload["report_digest_sha256"] = "0" * 64
    return hashlib.sha256(
        b"familycare-private-confirmation-report-v1\x00" + _canonical_json(payload)
    ).hexdigest()


def _validate_report(report: ConfirmationDryRunReport) -> None:
    if not hmac.compare_digest(
        report.report_digest_sha256,
        canonical_confirmation_report_digest(report),
    ):
        raise ConfirmationError(ConfirmationErrorCode.REPORT_DIGEST_MISMATCH)


def _write_report(
    report: ConfirmationDryRunReport,
    path: Path,
    *,
    repository_root: Path,
) -> None:
    _validate_report(report)
    if not path.is_absolute() or _is_inside(
        path.resolve(strict=False), repository_root.resolve(strict=True)
    ):
        raise ConfirmationError(ConfirmationErrorCode.REPORT_PATH_INVALID)
    try:
        parent = path.parent.resolve(strict=True)
        parent_stat = os.lstat(path.parent)
    except OSError:
        raise ConfirmationError(ConfirmationErrorCode.REPORT_PATH_INVALID) from None
    if (
        parent != path.parent
        or not stat.S_ISDIR(parent_stat.st_mode)
        or stat.S_ISLNK(parent_stat.st_mode)
        or stat.S_IMODE(parent_stat.st_mode) != 0o700
    ):
        raise ConfirmationError(ConfirmationErrorCode.REPORT_PARENT_MODE_INVALID)
    try:
        existing = os.lstat(path)
    except FileNotFoundError:
        existing = None
    if existing is not None and (
        not stat.S_ISREG(existing.st_mode) or stat.S_IMODE(existing.st_mode) != 0o600
    ):
        raise ConfirmationError(ConfirmationErrorCode.REPORT_FILE_MODE_INVALID)
    temporary = parent / f".confirmation-report-{uuid4().hex}.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        0o600,
    )
    try:
        payload = _canonical_json(report.model_dump(mode="json")) + b"\n"
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def _load_report(
    path: Path,
    *,
    repository_root: Path,
) -> ConfirmationDryRunReport:
    payload = _read_protected_file(
        path,
        repository_root=repository_root,
        is_report=True,
    )
    try:
        report = ConfirmationDryRunReport.model_validate_json(payload)
    except ValidationError, ValueError:
        raise ConfirmationError(ConfirmationErrorCode.REPORT_INVALID) from None
    _validate_report(report)
    return report


def prepare_confirmation_dry_run(
    *,
    manifest_path: Path,
    report_path: Path,
    repository_root: Path,
    expected_household_space_id: UUID,
    repository: ConfirmationRepository,
) -> ConfirmationDryRunReport:
    manifest = load_confirmation_manifest(manifest_path, repository_root=repository_root)
    if manifest.household_space_id != expected_household_space_id:
        raise ConfirmationError(ConfirmationErrorCode.MANIFEST_SCOPE_MISMATCH)
    report = repository.prepare_confirmation_dry_run(manifest)
    if (
        report.household_space_id != expected_household_space_id
        or report.manifest_digest_sha256 != manifest.manifest_digest_sha256
        or report.package_digest_sha256 != manifest.package_digest_sha256
    ):
        raise ConfirmationError(ConfirmationErrorCode.REPORT_INVALID)
    _write_report(report, report_path, repository_root=repository_root)
    return report


def apply_confirmation_manifest(
    *,
    manifest_path: Path,
    report_path: Path,
    repository_root: Path,
    expected_household_space_id: UUID,
    approved_report_digest_sha256: str,
    repository: ConfirmationRepository,
) -> AppliedConfirmationSet:
    manifest = load_confirmation_manifest(manifest_path, repository_root=repository_root)
    report = _load_report(report_path, repository_root=repository_root)
    if (
        manifest.household_space_id != expected_household_space_id
        or report.household_space_id != expected_household_space_id
        or report.manifest_digest_sha256 != manifest.manifest_digest_sha256
        or report.package_digest_sha256 != manifest.package_digest_sha256
        or not hmac.compare_digest(
            report.report_digest_sha256,
            approved_report_digest_sha256,
        )
    ):
        raise ConfirmationError(ConfirmationErrorCode.APPROVAL_INVALID)
    return repository.apply_confirmations(manifest, approved_report=report)


__all__ = [
    "AppliedConfirmationSet",
    "ConfirmationDryRunReport",
    "ConfirmationError",
    "ConfirmationErrorCode",
    "LoadedConfirmationManifest",
    "apply_confirmation_manifest",
    "canonical_confirmation_report_digest",
    "load_confirmation_manifest",
    "prepare_confirmation_dry_run",
]
