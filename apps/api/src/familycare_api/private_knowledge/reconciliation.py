"""Count-only dry-run reconciliation and report persistence."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import unicodedata
from contextlib import suppress
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, ValidationError

from familycare_api.private_knowledge.package import PrivateKnowledgePackage

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonNegativeInt = Annotated[int, Field(ge=0)]


class ReconciliationErrorCode(StrEnum):
    REPORT_PATH_INVALID = "REPORT_PATH_INVALID"
    REPORT_PARENT_MODE_INVALID = "REPORT_PARENT_MODE_INVALID"
    REPORT_FILE_MODE_INVALID = "REPORT_FILE_MODE_INVALID"
    REPORT_IO_ERROR = "REPORT_IO_ERROR"
    REPORT_INVALID = "REPORT_INVALID"
    REPORT_DIGEST_MISMATCH = "REPORT_DIGEST_MISMATCH"


class PrivateKnowledgeReconciliationError(ValueError):
    """Sanitized report error that never echoes a path or private value."""

    def __init__(self, code: ReconciliationErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class StrictReconciliationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class KnowledgeEntityCounts(StrictReconciliationModel):
    subjects: NonNegativeInt
    contracts: NonNegativeInt
    coverages: NonNegativeInt
    terms_assignments: NonNegativeInt
    terms_assignment_sources: NonNegativeInt
    terms_sections: NonNegativeInt
    source_clauses: NonNegativeInt
    semantic_reviews: NonNegativeInt
    facts: NonNegativeInt
    fact_citations: NonNegativeInt
    coverage_terms_mappings: NonNegativeInt
    document_bindings: NonNegativeInt

    @classmethod
    def zero(cls) -> KnowledgeEntityCounts:
        return cls(
            subjects=0,
            contracts=0,
            coverages=0,
            terms_assignments=0,
            terms_assignment_sources=0,
            terms_sections=0,
            source_clauses=0,
            semantic_reviews=0,
            facts=0,
            fact_citations=0,
            coverage_terms_mappings=0,
            document_bindings=0,
        )


class BaselineCounts(StrictReconciliationModel):
    family_members: NonNegativeInt
    policy_contracts: NonNegativeInt
    riders: NonNegativeInt
    document_versions: NonNegativeInt
    evidence: NonNegativeInt
    import_runs: NonNegativeInt
    current_import_runs: Annotated[int, Field(ge=0, le=1)]


class LabelKeyCount(StrictReconciliationModel):
    key: Sha256
    count: Annotated[int, Field(ge=1)]


class KnowledgeDatabaseBaseline(StrictReconciliationModel):
    """Private in-memory baseline; only its digest and counts enter a report."""

    household_space_id: UUID
    baseline_digest_sha256: Sha256
    current_run_id: UUID | None
    current_package_digest_sha256: Sha256 | None
    known_package_digests: tuple[Sha256, ...]
    counts: BaselineCounts
    current_snapshot_counts: KnowledgeEntityCounts
    policy_label_key_counts: tuple[LabelKeyCount, ...]
    coverage_label_key_counts: tuple[LabelKeyCount, ...]


class TriStateCounts(StrictReconciliationModel):
    match: NonNegativeInt
    no_match: NonNegativeInt
    unknown: NonNegativeInt


class BenefitTypeCounts(StrictReconciliationModel):
    fixed: NonNegativeInt
    indemnity: NonNegativeInt
    unknown: NonNegativeInt
    not_applicable: NonNegativeInt


class MappingApplicabilityCounts(StrictReconciliationModel):
    applicable: NonNegativeInt
    not_applicable: NonNegativeInt
    unknown: NonNegativeInt


class SourceMappingDecisionCounts(StrictReconciliationModel):
    match: NonNegativeInt
    unknown: NonNegativeInt
    not_applicable: NonNegativeInt


class CurrentStatusCounts(StrictReconciliationModel):
    active: NonNegativeInt
    inactive: NonNegativeInt
    lapsed: NonNegativeInt
    terminated: NonNegativeInt
    unknown: NonNegativeInt


class OperationalReconciliationCounts(StrictReconciliationModel):
    subject_exact_bindings: NonNegativeInt
    subject_unknown_bindings: NonNegativeInt
    policy_exact_bindings: NonNegativeInt
    policy_label_review_candidates: NonNegativeInt
    policy_label_ambiguous_candidates: NonNegativeInt
    policy_label_unmatched: NonNegativeInt
    coverage_exact_bindings: NonNegativeInt
    coverage_label_review_candidates: NonNegativeInt
    coverage_label_ambiguous_candidates: NonNegativeInt
    coverage_label_unmatched: NonNegativeInt
    document_exact_bindings: NonNegativeInt
    document_unknown_bindings: NonNegativeInt
    terms_edition_exact_bindings: NonNegativeInt
    operational_publish_blocked_coverages: NonNegativeInt


class KnowledgeDecisionCounts(StrictReconciliationModel):
    """Decision matrices that must still match the normalized persisted rows."""

    enrollment_decisions: TriStateCounts
    benefit_types: BenefitTypeCounts
    terms_document_identity: TriStateCounts
    terms_edition_applicability: TriStateCounts
    terms_overall_review: TriStateCounts
    mapping_source_decisions: SourceMappingDecisionCounts
    mapping_applicability: MappingApplicabilityCounts
    current_statuses: CurrentStatusCounts


class KnowledgeDryRunReport(StrictReconciliationModel):
    schema_version: Literal["private-knowledge-dry-run.v1"]
    package_schema_version: Literal["private-analysis-package.sol-v2"]
    package_digest_sha256: Sha256
    baseline_digest_sha256: Sha256
    operation: Literal["CREATE", "NO_OP", "SUPERSEDE", "BLOCKED"]
    target_already_current: bool
    input_counts: KnowledgeEntityCounts
    expected_insert_counts: KnowledgeEntityCounts
    expected_current_counts: KnowledgeEntityCounts
    enrollment_decisions: TriStateCounts
    benefit_types: BenefitTypeCounts
    terms_document_identity: TriStateCounts
    terms_edition_applicability: TriStateCounts
    terms_overall_review: TriStateCounts
    mapping_source_decisions: SourceMappingDecisionCounts
    mapping_applicability: MappingApplicabilityCounts
    current_statuses: CurrentStatusCounts
    operational_reconciliation: OperationalReconciliationCounts
    snapshot_conflict_count: NonNegativeInt
    apply_block_count: NonNegativeInt
    report_digest_sha256: Sha256


def report_decision_counts(report: KnowledgeDryRunReport) -> KnowledgeDecisionCounts:
    """Extract the immutable snapshot decision expectations from a dry run."""

    return KnowledgeDecisionCounts(
        enrollment_decisions=report.enrollment_decisions,
        benefit_types=report.benefit_types,
        terms_document_identity=report.terms_document_identity,
        terms_edition_applicability=report.terms_edition_applicability,
        terms_overall_review=report.terms_overall_review,
        mapping_source_decisions=report.mapping_source_decisions,
        mapping_applicability=report.mapping_applicability,
        current_statuses=report.current_statuses,
    )


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


def operational_label_key(*parts: str) -> str:
    """Hash an exact normalized display tuple without retaining its values."""

    normalized = []
    for part in parts:
        value = unicodedata.normalize("NFKC", part).casefold()
        normalized.append("".join(character for character in value if character.isalnum()))
    return _sha256("\x1f".join(normalized).encode("utf-8"))


_ALIAS_VALUE_KEYS = {
    "document_alias",
    "terms_alias",
    "selected_terms_alias",
    "previous_terms_alias",
}
_ALIAS_ARRAY_KEYS = {"pairing_aliases", "selected_terms_aliases"}


def _collect_source_aliases(value: JsonValue, target: set[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in _ALIAS_VALUE_KEYS and isinstance(child, str) and child:
                target.add(child)
            elif key in _ALIAS_ARRAY_KEYS and isinstance(child, list):
                target.update(item for item in child if isinstance(item, str) and item)
            _collect_source_aliases(child, target)
    elif isinstance(value, list):
        for child in value:
            _collect_source_aliases(child, target)


def package_source_aliases(package: PrivateKnowledgePackage) -> tuple[str, ...]:
    aliases: set[str] = set()
    record_groups = (
        package.contracts,
        package.coverages,
        package.pairings,
        package.mappings,
        package.sections,
        package.clauses,
        package.semantic_reviews,
    )
    for records in record_groups:
        for record in records:
            _collect_source_aliases(record.source_record, aliases)
    return tuple(sorted(aliases))


def _tri_state_counts(values: list[str]) -> TriStateCounts:
    return TriStateCounts(
        match=values.count("MATCH"),
        no_match=values.count("NO_MATCH"),
        unknown=values.count("UNKNOWN"),
    )


def package_entity_counts(package: PrivateKnowledgePackage) -> KnowledgeEntityCounts:
    return KnowledgeEntityCounts(
        subjects=len(package.subject_aliases),
        contracts=len(package.contracts),
        coverages=len(package.coverages),
        terms_assignments=len(package.pairings),
        terms_assignment_sources=sum(
            len(record.value.selected_terms_aliases) for record in package.pairings
        ),
        terms_sections=len(package.sections),
        source_clauses=len(package.clauses),
        semantic_reviews=len(package.semantic_reviews),
        facts=package.fact_count,
        fact_citations=sum(
            len(fact.citations)
            for review in package.semantic_reviews
            for fact in review.value.facts
        ),
        coverage_terms_mappings=len(package.mappings),
        document_bindings=len(package_source_aliases(package)),
    )


def _candidate_counts(keys: list[str], baseline: tuple[LabelKeyCount, ...]) -> tuple[int, int, int]:
    counts_by_key = {entry.key: entry.count for entry in baseline}
    review_candidates = sum(1 for key in keys if counts_by_key.get(key) == 1)
    ambiguous = sum(1 for key in keys if counts_by_key.get(key, 0) > 1)
    unmatched = len(keys) - review_candidates - ambiguous
    return review_candidates, ambiguous, unmatched


def _report_payload(report: KnowledgeDryRunReport) -> dict[str, object]:
    return cast(
        dict[str, object],
        report.model_dump(mode="json", exclude={"report_digest_sha256"}),
    )


def canonical_report_digest(report: KnowledgeDryRunReport) -> str:
    return _sha256(
        b"familycare-private-knowledge-dry-run-v1\x00" + _canonical_json(_report_payload(report))
    )


def build_dry_run_report(
    package: PrivateKnowledgePackage,
    baseline: KnowledgeDatabaseBaseline,
) -> KnowledgeDryRunReport:
    """Build a deterministic count-only report without mutating the database."""

    input_counts = package_entity_counts(package)
    known = package.package_digest_sha256 in baseline.known_package_digests
    target_already_current = baseline.current_package_digest_sha256 == package.package_digest_sha256
    if target_already_current:
        operation: Literal["CREATE", "NO_OP", "SUPERSEDE", "BLOCKED"] = "NO_OP"
        expected_insert_counts = KnowledgeEntityCounts.zero()
        expected_current_counts = baseline.current_snapshot_counts
    elif known:
        operation = "BLOCKED"
        expected_insert_counts = KnowledgeEntityCounts.zero()
        expected_current_counts = baseline.current_snapshot_counts
    elif baseline.current_package_digest_sha256 is None:
        operation = "CREATE"
        expected_insert_counts = input_counts
        expected_current_counts = input_counts
    else:
        operation = "SUPERSEDE"
        expected_insert_counts = input_counts
        expected_current_counts = input_counts

    contracts_by_id = {
        record.value.canonical_policy_id: record.value for record in package.contracts
    }
    policy_keys = [
        operational_label_key(record.value.insurer, record.value.product_name)
        for record in package.contracts
    ]
    coverage_keys = []
    for record in package.coverages:
        contract = contracts_by_id[record.value.canonical_policy_id]
        coverage_keys.append(
            operational_label_key(
                contract.insurer,
                contract.product_name,
                record.value.name,
            )
        )
    policy_candidates, policy_ambiguous, policy_unmatched = _candidate_counts(
        policy_keys,
        baseline.policy_label_key_counts,
    )
    coverage_candidates, coverage_ambiguous, coverage_unmatched = _candidate_counts(
        coverage_keys,
        baseline.coverage_label_key_counts,
    )

    mapping_values = [record.value for record in package.mappings]
    mappings_by_coverage_id = {mapping.canonical_rider_id: mapping for mapping in mapping_values}
    benefit_type_values = [
        coverage.value.benefit_type
        for coverage in package.coverages
        if mappings_by_coverage_id[coverage.value.canonical_rider_id].component_class
        == "BENEFIT_COVERAGE"
    ]
    benefit_types = BenefitTypeCounts(
        fixed=benefit_type_values.count("fixed"),
        indemnity=benefit_type_values.count("indemnity"),
        unknown=benefit_type_values.count("unknown"),
        not_applicable=sum(
            1
            for mapping in mapping_values
            if mapping.component_class == "NON_BENEFIT_CONTRACT_COMPONENT"
        ),
    )
    mapping_source_decisions = SourceMappingDecisionCounts(
        match=sum(1 for value in mapping_values if value.mapping_decision == "MATCH"),
        unknown=sum(1 for value in mapping_values if value.mapping_decision == "UNKNOWN"),
        not_applicable=sum(
            1 for value in mapping_values if value.mapping_decision == "NOT_APPLICABLE"
        ),
    )
    mapping_applicability = MappingApplicabilityCounts(
        applicable=sum(
            1 for value in mapping_values if value.component_class == "BENEFIT_COVERAGE"
        ),
        not_applicable=sum(
            1
            for value in mapping_values
            if value.component_class == "NON_BENEFIT_CONTRACT_COMPONENT"
        ),
        unknown=0,
    )
    statuses = [record.value.current_status.casefold() for record in package.coverages]
    provisional = KnowledgeDryRunReport(
        schema_version="private-knowledge-dry-run.v1",
        package_schema_version="private-analysis-package.sol-v2",
        package_digest_sha256=package.package_digest_sha256,
        baseline_digest_sha256=baseline.baseline_digest_sha256,
        operation=operation,
        target_already_current=target_already_current,
        input_counts=input_counts,
        expected_insert_counts=expected_insert_counts,
        expected_current_counts=expected_current_counts,
        enrollment_decisions=_tri_state_counts(
            [record.value.enrollment_decision for record in package.mappings]
        ),
        benefit_types=benefit_types,
        terms_document_identity=_tri_state_counts(
            [record.value.document_identity_decision for record in package.pairings]
        ),
        terms_edition_applicability=_tri_state_counts(
            [record.value.edition_applicability_decision for record in package.pairings]
        ),
        terms_overall_review=_tri_state_counts(
            [record.value.review_decision for record in package.pairings]
        ),
        mapping_source_decisions=mapping_source_decisions,
        mapping_applicability=mapping_applicability,
        current_statuses=CurrentStatusCounts(
            active=statuses.count("active"),
            inactive=statuses.count("inactive"),
            lapsed=statuses.count("lapsed"),
            terminated=statuses.count("terminated"),
            unknown=statuses.count("unknown"),
        ),
        operational_reconciliation=OperationalReconciliationCounts(
            subject_exact_bindings=0,
            subject_unknown_bindings=len(package.subject_aliases),
            policy_exact_bindings=0,
            policy_label_review_candidates=policy_candidates,
            policy_label_ambiguous_candidates=policy_ambiguous,
            policy_label_unmatched=policy_unmatched,
            coverage_exact_bindings=0,
            coverage_label_review_candidates=coverage_candidates,
            coverage_label_ambiguous_candidates=coverage_ambiguous,
            coverage_label_unmatched=coverage_unmatched,
            document_exact_bindings=0,
            document_unknown_bindings=len(package_source_aliases(package)),
            terms_edition_exact_bindings=0,
            operational_publish_blocked_coverages=len(package.coverages),
        ),
        snapshot_conflict_count=0,
        apply_block_count=1 if operation == "BLOCKED" else 0,
        report_digest_sha256="0" * 64,
    )
    return provisional.model_copy(
        update={"report_digest_sha256": canonical_report_digest(provisional)}
    )


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def write_dry_run_report(
    report: KnowledgeDryRunReport,
    path: Path,
    *,
    repository_root: Path,
) -> None:
    """Atomically write a private report with mode 0600 outside the repository."""

    if not path.is_absolute():
        raise PrivateKnowledgeReconciliationError(ReconciliationErrorCode.REPORT_PATH_INVALID)
    resolved_parent = path.parent.resolve(strict=False)
    resolved_repository = repository_root.resolve(strict=False)
    if _is_inside(path.resolve(strict=False), resolved_repository):
        raise PrivateKnowledgeReconciliationError(ReconciliationErrorCode.REPORT_PATH_INVALID)
    try:
        parent_stat = os.lstat(resolved_parent)
    except OSError:
        raise PrivateKnowledgeReconciliationError(
            ReconciliationErrorCode.REPORT_PATH_INVALID
        ) from None
    if not stat.S_ISDIR(parent_stat.st_mode) or stat.S_IMODE(parent_stat.st_mode) != 0o700:
        raise PrivateKnowledgeReconciliationError(
            ReconciliationErrorCode.REPORT_PARENT_MODE_INVALID
        )
    try:
        destination_stat = os.lstat(path)
    except FileNotFoundError:
        destination_stat = None
    if destination_stat is not None and (
        not stat.S_ISREG(destination_stat.st_mode)
        or stat.S_IMODE(destination_stat.st_mode) != 0o600
    ):
        raise PrivateKnowledgeReconciliationError(ReconciliationErrorCode.REPORT_FILE_MODE_INVALID)

    payload = _canonical_json(report.model_dump(mode="json")) + b"\n"
    temporary_name = f".familycare-dry-run-{uuid4().hex}.tmp"
    parent_fd = os.open(resolved_parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    file_fd: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        file_fd = os.open(temporary_name, flags, 0o600, dir_fd=parent_fd)
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
        raise PrivateKnowledgeReconciliationError(ReconciliationErrorCode.REPORT_IO_ERROR) from None
    finally:
        os.close(parent_fd)


def load_dry_run_report(path: Path) -> KnowledgeDryRunReport:
    """Read and authenticate one bounded dry-run report."""

    if not path.is_absolute():
        raise PrivateKnowledgeReconciliationError(ReconciliationErrorCode.REPORT_PATH_INVALID)
    try:
        observed = os.lstat(path)
    except OSError:
        raise PrivateKnowledgeReconciliationError(
            ReconciliationErrorCode.REPORT_PATH_INVALID
        ) from None
    if not stat.S_ISREG(observed.st_mode) or stat.S_IMODE(observed.st_mode) != 0o600:
        raise PrivateKnowledgeReconciliationError(ReconciliationErrorCode.REPORT_FILE_MODE_INVALID)
    if observed.st_size > 1024 * 1024:
        raise PrivateKnowledgeReconciliationError(ReconciliationErrorCode.REPORT_INVALID)
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
        try:
            payload = os.read(fd, 1024 * 1024 + 1)
        finally:
            os.close(fd)
        parsed = json.loads(payload)
        report = KnowledgeDryRunReport.model_validate(parsed)
    except OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError:
        raise PrivateKnowledgeReconciliationError(ReconciliationErrorCode.REPORT_INVALID) from None
    if report.report_digest_sha256 != canonical_report_digest(report):
        raise PrivateKnowledgeReconciliationError(ReconciliationErrorCode.REPORT_DIGEST_MISMATCH)
    return report
