"""Count-only dry-run reconciliation for reviewed rule publications."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from contextlib import suppress
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Protocol, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from familycare_api.private_knowledge.publication_models import (
    PublicationCounts,
    PublicationCountsV2,
)
from familycare_api.private_knowledge.publication_package import (
    RulePublicationPackage,
    validate_loaded_rule_publication_package,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonNegativeInt = Annotated[int, Field(ge=0)]


class PublicationReconciliationErrorCode(StrEnum):
    REPORT_PATH_INVALID = "REPORT_PATH_INVALID"
    REPORT_PARENT_MODE_INVALID = "REPORT_PARENT_MODE_INVALID"
    REPORT_FILE_MODE_INVALID = "REPORT_FILE_MODE_INVALID"
    REPORT_IO_ERROR = "REPORT_IO_ERROR"
    REPORT_INVALID = "REPORT_INVALID"
    REPORT_DIGEST_MISMATCH = "REPORT_DIGEST_MISMATCH"


class PublicationReconciliationError(ValueError):
    """Sanitized report failure without a path or private value."""

    def __init__(self, code: PublicationReconciliationErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class StrictReconciliationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DispositionCounts(StrictReconciliationModel):
    published: NonNegativeInt
    blocked: NonNegativeInt
    not_applicable: NonNegativeInt

    @classmethod
    def zero(cls) -> DispositionCounts:
        return cls(published=0, blocked=0, not_applicable=0)


class DispositionCountsV2(DispositionCounts):
    advisory: NonNegativeInt

    @classmethod
    def zero(cls) -> DispositionCountsV2:
        return cls(published=0, advisory=0, blocked=0, not_applicable=0)


class PublicationBlockCounts(StrictReconciliationModel):
    snapshot_mismatch: NonNegativeInt
    historical_digest_conflict: NonNegativeInt
    disposition_closure_mismatch: NonNegativeInt
    missing_current_confirmation: NonNegativeInt
    subject_binding_mismatch: NonNegativeInt
    coverage_authority_mismatch: NonNegativeInt
    citation_mismatch: NonNegativeInt

    @property
    def total(self) -> int:
        return sum(cast(dict[str, int], self.model_dump(mode="python")).values())


class PublicationCoverageBaseline(StrictReconciliationModel):
    knowledge_contract_id: UUID
    knowledge_coverage_id: UUID
    source_subject_key: str
    family_alias: str
    canonical_policy_id: str
    canonical_coverage_id: str
    subject_binding_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    enrollment_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    component_classification: Literal[
        "BENEFIT_COVERAGE",
        "NON_BENEFIT_CONTRACT_COMPONENT",
        "UNKNOWN",
    ]
    benefit_type: Literal["FIXED", "INDEMNITY", "UNKNOWN", "NOT_APPLICABLE"]
    mapping_applicability: Literal["APPLICABLE", "NOT_APPLICABLE", "UNKNOWN"]
    mapping_enrollment_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    document_identity_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    edition_applicability_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    section_mapping_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    overall_mapping_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    current_confirmation_decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"] | None
    current_confirmed_status: (
        Literal["active", "inactive", "lapsed", "terminated", "unknown"] | None
    )


class PublicationEvidenceBaseline(StrictReconciliationModel):
    terms_section_id: UUID
    source_clause_id: UUID | None
    fact_id: UUID | None
    canonical_policy_id: str
    terms_source_alias: str
    source_section_key: str
    source_clause_index: int | None
    source_fact_key: str | None
    page_start: NonNegativeInt
    page_end: NonNegativeInt
    source_text_sha256: Sha256


class PublicationDatabaseBaseline(StrictReconciliationModel):
    """Private baseline; only hashes and aggregate counts enter the report."""

    household_space_id: UUID
    baseline_digest_sha256: Sha256
    knowledge_import_run_id: UUID
    knowledge_package_digest_sha256: Sha256
    knowledge_projection_digest_sha256: Sha256
    known_publication_digests: tuple[Sha256, ...]
    current_publication_run_id: UUID | None
    current_publication_package_digest_sha256: Sha256 | None
    current_publication_counts: PublicationCounts | PublicationCountsV2
    current_disposition_counts: DispositionCounts | DispositionCountsV2
    coverage_authorities: tuple[PublicationCoverageBaseline, ...]
    evidence: tuple[PublicationEvidenceBaseline, ...]
    actor_identity_digest_sha256: Sha256


class RulePublicationDryRunReport(StrictReconciliationModel):
    schema_version: Literal["private-knowledge-rule-dry-run.v1"]
    package_schema_version: Literal[
        "private-knowledge-rule-publication.sol-v1",
        "private-knowledge-rule-publication.sol-v2",
    ]
    package_digest_sha256: Sha256
    knowledge_package_digest_sha256: Sha256
    knowledge_snapshot_digest_sha256: Sha256
    baseline_digest_sha256: Sha256
    operation: Literal["CREATE", "NO_OP", "SUPERSEDE", "BLOCKED"]
    input_counts: PublicationCounts | PublicationCountsV2
    expected_insert_counts: PublicationCounts | PublicationCountsV2
    expected_current_counts: PublicationCounts | PublicationCountsV2
    dispositions: DispositionCounts | DispositionCountsV2
    expected_current_dispositions: DispositionCounts | DispositionCountsV2
    block_counts: PublicationBlockCounts
    apply_block_count: NonNegativeInt
    report_digest_sha256: Sha256


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _report_payload(report: RulePublicationDryRunReport) -> dict[str, object]:
    return cast(
        dict[str, object],
        report.model_dump(mode="json", exclude={"report_digest_sha256"}),
    )


def canonical_rule_publication_report_digest(
    report: RulePublicationDryRunReport,
) -> str:
    return _sha256(
        b"familycare-private-rule-dry-run-v1\x00" + _canonical_json(_report_payload(report))
    )


def _disposition_counts(
    package: RulePublicationPackage,
) -> DispositionCounts | DispositionCountsV2:
    values = [record.value.disposition for record in package.coverage_dispositions]
    if package.schema_version == "private-knowledge-rule-publication.sol-v2":
        return DispositionCountsV2(
            published=values.count("PUBLISHED"),
            advisory=values.count("ADVISORY"),
            blocked=values.count("BLOCKED"),
            not_applicable=values.count("NOT_APPLICABLE"),
        )
    return DispositionCounts(
        published=values.count("PUBLISHED"),
        blocked=values.count("BLOCKED"),
        not_applicable=values.count("NOT_APPLICABLE"),
    )


class _CitationIdentity(Protocol):
    canonical_policy_id: str
    terms_source_alias: str
    source_section_key: str
    source_clause_index: int | None
    source_fact_key: str | None


def _citation_identity(value: _CitationIdentity) -> tuple[object, ...]:
    return (
        value.canonical_policy_id,
        value.terms_source_alias,
        value.source_section_key,
        value.source_clause_index,
        value.source_fact_key,
    )


def build_rule_publication_dry_run(
    package: RulePublicationPackage,
    baseline: PublicationDatabaseBaseline,
) -> RulePublicationDryRunReport:
    """Compare a reviewed package to one exact current knowledge snapshot."""

    validate_loaded_rule_publication_package(package)
    snapshot_mismatch = int(
        baseline.knowledge_package_digest_sha256
        != package.manifest.source_knowledge_package_digest_sha256
        or baseline.knowledge_projection_digest_sha256
        != package.manifest.source_knowledge_projection_digest_sha256
    )
    historical_conflict = int(
        package.package_digest_sha256 in baseline.known_publication_digests
        and package.package_digest_sha256 != baseline.current_publication_package_digest_sha256
    )

    disposition_by_coverage = {
        record.value.canonical_coverage_id: record.value for record in package.coverage_dispositions
    }
    baseline_by_coverage = {
        record.canonical_coverage_id: record for record in baseline.coverage_authorities
    }
    closure_mismatch = len(set(disposition_by_coverage).symmetric_difference(baseline_by_coverage))
    missing_confirmation = 0
    subject_binding_mismatch = 0
    coverage_authority_mismatch = 0
    for coverage_key in set(disposition_by_coverage) & set(baseline_by_coverage):
        disposition = disposition_by_coverage[coverage_key]
        authority = baseline_by_coverage[coverage_key]
        if (
            disposition.canonical_policy_id != authority.canonical_policy_id
            or disposition.source_subject_key != authority.source_subject_key
            or disposition.family_alias != authority.family_alias
        ):
            closure_mismatch += 1
        if disposition.disposition != "ADVISORY" and (
            authority.current_confirmation_decision is None
            or authority.current_confirmed_status is None
        ):
            missing_confirmation += 1
        if authority.subject_binding_decision != "MATCH":
            subject_binding_mismatch += 1
        if disposition.disposition == "PUBLISHED":
            enrollment_authority = getattr(
                disposition,
                "enrollment_authority",
                "CERTIFICATE_SNAPSHOT",
            )
            expected = (
                authority.enrollment_decision == "MATCH"
                and authority.component_classification == "BENEFIT_COVERAGE"
                and authority.benefit_type == disposition.benefit_type
                and enrollment_authority == "CERTIFICATE_SNAPSHOT"
                and authority.mapping_applicability == "APPLICABLE"
                and authority.mapping_enrollment_decision == "MATCH"
                and authority.document_identity_decision == "MATCH"
                and authority.edition_applicability_decision == "MATCH"
                and authority.section_mapping_decision == "MATCH"
                and authority.overall_mapping_decision == "MATCH"
                and authority.current_confirmation_decision == "MATCH"
                and authority.current_confirmed_status == "active"
            )
            if not expected:
                coverage_authority_mismatch += 1
        elif disposition.disposition == "ADVISORY":
            enrollment_authority_matches = (
                authority.enrollment_decision == "MATCH"
                and disposition.enrollment_authority == "CERTIFICATE_SNAPSHOT"
            ) or (
                authority.enrollment_decision == "UNKNOWN"
                and disposition.enrollment_authority == "USER_CONFIRMED_COVERAGE_ENROLLMENT"
            )
            expected = (
                authority.component_classification == "BENEFIT_COVERAGE"
                and authority.benefit_type == disposition.benefit_type
                and enrollment_authority_matches
            )
            if not expected:
                coverage_authority_mismatch += 1
        elif (
            disposition.disposition == "NOT_APPLICABLE"
            and authority.component_classification != "NON_BENEFIT_CONTRACT_COMPONENT"
        ):
            coverage_authority_mismatch += 1

    evidence_by_identity = {_citation_identity(value): value for value in baseline.evidence}
    citation_mismatch = 0
    citation_groups = (package.rule_citations, package.calculation_citations)
    for citations in citation_groups:
        for record in citations:
            citation = record.value
            evidence = evidence_by_identity.get(_citation_identity(citation))
            if evidence is None or (
                evidence.page_start != citation.page_start
                or evidence.page_end != citation.page_end
                or evidence.source_text_sha256 != citation.source_text_sha256
            ):
                citation_mismatch += 1

    block_counts = PublicationBlockCounts(
        snapshot_mismatch=snapshot_mismatch,
        historical_digest_conflict=historical_conflict,
        disposition_closure_mismatch=closure_mismatch,
        missing_current_confirmation=missing_confirmation,
        subject_binding_mismatch=subject_binding_mismatch,
        coverage_authority_mismatch=coverage_authority_mismatch,
        citation_mismatch=citation_mismatch,
    )
    target_current = (
        baseline.current_publication_package_digest_sha256 == package.package_digest_sha256
    )
    if block_counts.total:
        operation: Literal["CREATE", "NO_OP", "SUPERSEDE", "BLOCKED"] = "BLOCKED"
    elif target_current:
        operation = "NO_OP"
    elif baseline.current_publication_run_id is None:
        operation = "CREATE"
    else:
        operation = "SUPERSEDE"

    input_counts = package.reconciliation
    dispositions = _disposition_counts(package)
    if operation in {"CREATE", "SUPERSEDE"}:
        insert_counts = input_counts
        current_counts = input_counts
        current_dispositions = dispositions
    elif operation == "NO_OP":
        insert_counts = type(input_counts)(**{key: 0 for key in type(input_counts).model_fields})
        current_counts = baseline.current_publication_counts
        current_dispositions = baseline.current_disposition_counts
    else:
        insert_counts = type(input_counts)(**{key: 0 for key in type(input_counts).model_fields})
        current_counts = baseline.current_publication_counts
        current_dispositions = baseline.current_disposition_counts

    provisional = RulePublicationDryRunReport(
        schema_version="private-knowledge-rule-dry-run.v1",
        package_schema_version=package.schema_version,
        package_digest_sha256=package.package_digest_sha256,
        knowledge_package_digest_sha256=(package.manifest.source_knowledge_package_digest_sha256),
        knowledge_snapshot_digest_sha256=(
            package.manifest.source_knowledge_projection_digest_sha256
        ),
        baseline_digest_sha256=baseline.baseline_digest_sha256,
        operation=operation,
        input_counts=input_counts,
        expected_insert_counts=insert_counts,
        expected_current_counts=current_counts,
        dispositions=dispositions,
        expected_current_dispositions=current_dispositions,
        block_counts=block_counts,
        apply_block_count=block_counts.total,
        report_digest_sha256="0" * 64,
    )
    return provisional.model_copy(
        update={"report_digest_sha256": canonical_rule_publication_report_digest(provisional)}
    )


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def write_rule_publication_dry_run_report(
    report: RulePublicationDryRunReport,
    path: Path,
    *,
    repository_root: Path,
) -> None:
    """Atomically persist a mode-0600 count-only approval artifact."""

    if not path.is_absolute() or _is_inside(
        path.resolve(strict=False),
        repository_root.resolve(strict=False),
    ):
        raise PublicationReconciliationError(PublicationReconciliationErrorCode.REPORT_PATH_INVALID)
    try:
        parent = path.parent.resolve(strict=True)
        parent_stat = os.lstat(parent)
    except OSError:
        raise PublicationReconciliationError(
            PublicationReconciliationErrorCode.REPORT_PATH_INVALID
        ) from None
    if not stat.S_ISDIR(parent_stat.st_mode) or stat.S_IMODE(parent_stat.st_mode) != 0o700:
        raise PublicationReconciliationError(
            PublicationReconciliationErrorCode.REPORT_PARENT_MODE_INVALID
        )
    try:
        destination = os.lstat(path)
    except FileNotFoundError:
        destination = None
    if destination is not None and (
        not stat.S_ISREG(destination.st_mode) or stat.S_IMODE(destination.st_mode) != 0o600
    ):
        raise PublicationReconciliationError(
            PublicationReconciliationErrorCode.REPORT_FILE_MODE_INVALID
        )

    payload = _canonical_json(report.model_dump(mode="json")) + b"\n"
    temporary_name = f".familycare-rule-dry-run-{uuid4().hex}.tmp"
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        parent_fd = os.open(parent, flags)
    except OSError:
        raise PublicationReconciliationError(
            PublicationReconciliationErrorCode.REPORT_PATH_INVALID
        ) from None
    file_fd: int | None = None
    try:
        file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        file_fd = os.open(temporary_name, file_flags, 0o600, dir_fd=parent_fd)
        os.fchmod(file_fd, 0o600)
        offset = 0
        while offset < len(payload):
            offset += os.write(file_fd, payload[offset:])
        os.fsync(file_fd)
        os.close(file_fd)
        file_fd = None
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        os.fsync(parent_fd)
    except OSError:
        if file_fd is not None:
            os.close(file_fd)
        with suppress(OSError):
            os.unlink(temporary_name, dir_fd=parent_fd)
        raise PublicationReconciliationError(
            PublicationReconciliationErrorCode.REPORT_IO_ERROR
        ) from None
    finally:
        os.close(parent_fd)


def load_rule_publication_dry_run_report(
    path: Path,
    *,
    repository_root: Path,
) -> RulePublicationDryRunReport:
    """Descriptor-read and authenticate one bounded approval report."""

    if not path.is_absolute() or _is_inside(
        path.resolve(strict=False),
        repository_root.resolve(strict=False),
    ):
        raise PublicationReconciliationError(PublicationReconciliationErrorCode.REPORT_PATH_INVALID)
    try:
        parent = path.parent.resolve(strict=True)
        parent_stat = os.lstat(parent)
    except OSError:
        raise PublicationReconciliationError(
            PublicationReconciliationErrorCode.REPORT_PATH_INVALID
        ) from None
    if not stat.S_ISDIR(parent_stat.st_mode) or stat.S_IMODE(parent_stat.st_mode) != 0o700:
        raise PublicationReconciliationError(
            PublicationReconciliationErrorCode.REPORT_PARENT_MODE_INVALID
        )
    parent_flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        parent_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        parent_flags |= os.O_NOFOLLOW
    try:
        parent_fd = os.open(parent, parent_flags)
        try:
            observed = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(observed.st_mode)
                or stat.S_IMODE(observed.st_mode) != 0o600
                or observed.st_size > 1024 * 1024
            ):
                raise PublicationReconciliationError(
                    PublicationReconciliationErrorCode.REPORT_FILE_MODE_INVALID
                )
            flags = os.O_RDONLY | os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(path.name, flags, dir_fd=parent_fd)
            try:
                opened = os.fstat(fd)
                identity = (
                    observed.st_dev,
                    observed.st_ino,
                    observed.st_mode,
                    observed.st_size,
                    observed.st_mtime_ns,
                )
                if identity != (
                    opened.st_dev,
                    opened.st_ino,
                    opened.st_mode,
                    opened.st_size,
                    opened.st_mtime_ns,
                ):
                    raise PublicationReconciliationError(
                        PublicationReconciliationErrorCode.REPORT_INVALID
                    )
                payload = os.read(fd, 1024 * 1024 + 1)
                after_open = os.fstat(fd)
            finally:
                os.close(fd)
            after_path = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            after_identity = (
                after_open.st_dev,
                after_open.st_ino,
                after_open.st_mode,
                after_open.st_size,
                after_open.st_mtime_ns,
            )
            path_identity = (
                after_path.st_dev,
                after_path.st_ino,
                after_path.st_mode,
                after_path.st_size,
                after_path.st_mtime_ns,
            )
            if identity != after_identity or identity != path_identity:
                raise PublicationReconciliationError(
                    PublicationReconciliationErrorCode.REPORT_INVALID
                )
        finally:
            os.close(parent_fd)
    except PublicationReconciliationError:
        raise
    except OSError:
        raise PublicationReconciliationError(
            PublicationReconciliationErrorCode.REPORT_INVALID
        ) from None
    try:
        report = RulePublicationDryRunReport.model_validate_json(payload)
    except ValidationError:
        raise PublicationReconciliationError(
            PublicationReconciliationErrorCode.REPORT_INVALID
        ) from None
    if report.report_digest_sha256 != canonical_rule_publication_report_digest(report):
        raise PublicationReconciliationError(
            PublicationReconciliationErrorCode.REPORT_DIGEST_MISMATCH
        )
    return report
