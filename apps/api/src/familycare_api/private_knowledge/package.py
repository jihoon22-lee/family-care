"""Descriptor-safe loader for private-analysis-package.sol-v2."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast

from pydantic import BaseModel, JsonValue, ValidationError

from familycare_api.private_knowledge.errors import (
    PackageErrorCode,
    PrivateKnowledgePackageError,
)
from familycare_api.private_knowledge.models import (
    ClauseRecord,
    ContractRecord,
    CoverageRecord,
    MappingRecord,
    PackageManifest,
    PairingRecord,
    ReconciliationCounts,
    SemanticReviewRecord,
    TermsSectionRecord,
)

SCHEMA_VERSION = "private-analysis-package.sol-v2"
MANIFEST_NAME = "manifest.json"
REQUIRED_DATA_FILES = frozenset(
    {
        "contracts.jsonl",
        "coverage-components.jsonl",
        "policy-terms-pairings.jsonl",
        "coverage-terms-mappings.jsonl",
        "terms-sections.jsonl",
        "clause-evidence-index.jsonl",
        "terms-semantic-review.jsonl",
        "reconciliation.json",
    }
)
ALLOWED_SUPPLEMENTARY_FILES = frozenset(
    {
        "ANALYSIS-REPORT.md",
        "DRIVE-SOURCE-READABILITY-AUDIT.json",
        "import-eligibility-dry-run.json",
        "methodology-and-reference-review.json",
        "runtime-database-dry-run.json",
    }
)
ALLOWED_MANIFEST_FILES = REQUIRED_DATA_FILES | ALLOWED_SUPPLEMENTARY_FILES
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_JSONL_LINE_BYTES = 1024 * 1024
MAX_NESTED_DEPTH = 12
MAX_NESTED_ITEMS = 128
MAX_NESTED_STRING_LENGTH = 16_384
MAX_NESTED_NODES = 20_000
MAX_ROWS_BY_ROLE: Mapping[str, int] = {
    "contracts.jsonl": 10_000,
    "coverage-components.jsonl": 100_000,
    "policy-terms-pairings.jsonl": 10_000,
    "coverage-terms-mappings.jsonl": 100_000,
    "terms-sections.jsonl": 50_000,
    "clause-evidence-index.jsonl": 500_000,
    "terms-semantic-review.jsonl": 50_000,
}

JsonObject = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ValidatedRecord[ModelT: BaseModel]:
    """A strict projection plus the complete canonical source record."""

    value: ModelT
    source_record: JsonObject
    source_record_digest_sha256: str


@dataclass(frozen=True, slots=True)
class PrivateKnowledgePackage:
    """Validated and referentially closed package snapshot."""

    schema_version: str
    manifest: PackageManifest
    manifest_digest_sha256: str
    package_digest_sha256: str
    contracts: tuple[ValidatedRecord[ContractRecord], ...]
    coverages: tuple[ValidatedRecord[CoverageRecord], ...]
    pairings: tuple[ValidatedRecord[PairingRecord], ...]
    mappings: tuple[ValidatedRecord[MappingRecord], ...]
    sections: tuple[ValidatedRecord[TermsSectionRecord], ...]
    clauses: tuple[ValidatedRecord[ClauseRecord], ...]
    semantic_reviews: tuple[ValidatedRecord[SemanticReviewRecord], ...]
    reconciliation: ReconciliationCounts

    @property
    def fact_count(self) -> int:
        return sum(len(record.value.facts) for record in self.semantic_reviews)

    @property
    def subject_aliases(self) -> tuple[str, ...]:
        return tuple(sorted({record.value.family_alias for record in self.contracts}))


def _error(
    code: PackageErrorCode,
    *,
    file_role: str | None = None,
    row_number: int | None = None,
) -> PrivateKnowledgePackageError:
    return PrivateKnowledgePackageError(
        code,
        file_role=file_role,
        row_number=row_number,
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


def _manifest_projection(manifest: PackageManifest) -> dict[str, object]:
    projection = manifest.model_dump(mode="json")
    projection["files"] = sorted(
        cast(list[dict[str, object]], projection["files"]),
        key=lambda item: cast(str, item["name"]),
    )
    return projection


def canonical_package_digest(package: PrivateKnowledgePackage) -> str:
    """Recalculate the semantic package digest independent of file ordering."""

    canonical_manifest = _canonical_json(_manifest_projection(package.manifest))
    return _sha256(b"familycare-private-knowledge-sol-v2\x00" + canonical_manifest)


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _read_file(
    root_fd: int,
    name: str,
    *,
    expected_bytes: int | None = None,
    expected_sha256: str | None = None,
) -> bytes:
    try:
        before = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError, NotADirectoryError:
        code = (
            PackageErrorCode.MANIFEST_MISSING
            if name == MANIFEST_NAME
            else PackageErrorCode.MISSING_REQUIRED_FILE
        )
        raise _error(code, file_role=name) from None
    if not stat.S_ISREG(before.st_mode):
        raise _error(PackageErrorCode.FILE_NOT_REGULAR, file_role=name)
    if stat.S_IMODE(before.st_mode) != 0o600:
        raise _error(PackageErrorCode.FILE_MODE_INVALID, file_role=name)
    if before.st_size > MAX_FILE_BYTES:
        raise _error(PackageErrorCode.FILE_SIZE_LIMIT, file_role=name)

    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(name, flags, dir_fd=root_fd)
    except OSError:
        raise _error(PackageErrorCode.FILE_NOT_REGULAR, file_role=name) from None
    try:
        opened = os.fstat(fd)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        identity_opened = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        )
        if not stat.S_ISREG(opened.st_mode) or identity_before != identity_opened:
            raise _error(PackageErrorCode.FILE_CHANGED, file_role=name)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise _error(PackageErrorCode.FILE_SIZE_LIMIT, file_role=name)
            chunks.append(chunk)
        after_open = os.fstat(fd)
    finally:
        os.close(fd)

    try:
        after_path = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    except OSError:
        raise _error(PackageErrorCode.FILE_CHANGED, file_role=name) from None
    identity_after_open = (
        after_open.st_dev,
        after_open.st_ino,
        after_open.st_size,
        after_open.st_mtime_ns,
    )
    identity_after_path = (
        after_path.st_dev,
        after_path.st_ino,
        after_path.st_size,
        after_path.st_mtime_ns,
    )
    if identity_before != identity_after_open or identity_before != identity_after_path:
        raise _error(PackageErrorCode.FILE_CHANGED, file_role=name)

    payload = b"".join(chunks)
    if expected_bytes is not None and len(payload) != expected_bytes:
        raise _error(PackageErrorCode.FILE_SIZE_MISMATCH, file_role=name)
    if expected_sha256 is not None and _sha256(payload) != expected_sha256:
        raise _error(PackageErrorCode.FILE_DIGEST_MISMATCH, file_role=name)
    return payload


def _reject_json_constant(_: str) -> None:
    raise ValueError


def _parse_json_object(
    payload: bytes,
    *,
    file_role: str,
    row_number: int | None = None,
) -> JsonObject:
    try:
        parsed = json.loads(payload, parse_constant=_reject_json_constant)
    except UnicodeDecodeError, json.JSONDecodeError, ValueError:
        raise _error(
            PackageErrorCode.INVALID_JSON,
            file_role=file_role,
            row_number=row_number,
        ) from None
    if not isinstance(parsed, dict):
        raise _error(
            PackageErrorCode.INVALID_RECORD,
            file_role=file_role,
            row_number=row_number,
        )
    typed = cast(JsonObject, parsed)
    _validate_nested_value(typed, file_role=file_role, row_number=row_number)
    return typed


def _validate_nested_value(
    root: JsonValue,
    *,
    file_role: str,
    row_number: int | None,
) -> None:
    nodes = 0
    stack: list[tuple[JsonValue, int]] = [(root, 0)]
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NESTED_NODES or depth > MAX_NESTED_DEPTH:
            raise _error(
                PackageErrorCode.NESTED_VALUE_LIMIT,
                file_role=file_role,
                row_number=row_number,
            )
        if isinstance(value, str):
            if len(value) > MAX_NESTED_STRING_LENGTH:
                raise _error(
                    PackageErrorCode.NESTED_VALUE_LIMIT,
                    file_role=file_role,
                    row_number=row_number,
                )
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise _error(
                    PackageErrorCode.NESTED_VALUE_LIMIT,
                    file_role=file_role,
                    row_number=row_number,
                )
        elif isinstance(value, list):
            if len(value) > MAX_NESTED_ITEMS:
                raise _error(
                    PackageErrorCode.NESTED_VALUE_LIMIT,
                    file_role=file_role,
                    row_number=row_number,
                )
            stack.extend((item, depth + 1) for item in value)
        elif isinstance(value, dict):
            if len(value) > MAX_NESTED_ITEMS:
                raise _error(
                    PackageErrorCode.NESTED_VALUE_LIMIT,
                    file_role=file_role,
                    row_number=row_number,
                )
            for key, item in value.items():
                if not isinstance(key, str) or not key or len(key) > 160:
                    raise _error(
                        PackageErrorCode.NESTED_VALUE_LIMIT,
                        file_role=file_role,
                        row_number=row_number,
                    )
                stack.append((item, depth + 1))


def _contains_executable_input(value: JsonValue) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "executable_rule" and child is not False:
                return True
            if _contains_executable_input(child):
                return True
    elif isinstance(value, list):
        return any(_contains_executable_input(child) for child in value)
    return False


def _parse_jsonl[ModelT: BaseModel](
    payload: bytes,
    *,
    file_role: str,
    model_type: type[ModelT],
) -> tuple[ValidatedRecord[ModelT], ...]:
    lines = payload.splitlines()
    if not lines or len(lines) > MAX_ROWS_BY_ROLE[file_role]:
        raise _error(PackageErrorCode.ROW_LIMIT, file_role=file_role)
    records: list[ValidatedRecord[ModelT]] = []
    for row_number, line in enumerate(lines, start=1):
        if not line or len(line) > MAX_JSONL_LINE_BYTES:
            code = PackageErrorCode.INVALID_JSON if not line else PackageErrorCode.FILE_SIZE_LIMIT
            raise _error(code, file_role=file_role, row_number=row_number)
        source_record = _parse_json_object(
            line,
            file_role=file_role,
            row_number=row_number,
        )
        if _contains_executable_input(source_record):
            raise _error(
                PackageErrorCode.EXECUTABLE_INPUT,
                file_role=file_role,
                row_number=row_number,
            )
        try:
            value = model_type.model_validate(source_record)
        except ValidationError:
            raise _error(
                PackageErrorCode.INVALID_RECORD,
                file_role=file_role,
                row_number=row_number,
            ) from None
        records.append(
            ValidatedRecord(
                value=value,
                source_record=source_record,
                source_record_digest_sha256=_sha256(_canonical_json(source_record)),
            )
        )
    return tuple(records)


def _validate_date_range(
    start: str | None,
    end: str | None,
    *,
    file_role: str,
    row_number: int,
) -> None:
    try:
        parsed_start = date.fromisoformat(start) if start is not None else None
        parsed_end = date.fromisoformat(end) if end is not None else None
    except ValueError:
        raise _error(
            PackageErrorCode.INVALID_RECORD,
            file_role=file_role,
            row_number=row_number,
        ) from None
    if parsed_start is not None and parsed_end is not None and parsed_end < parsed_start:
        raise _error(
            PackageErrorCode.INVALID_RECORD,
            file_role=file_role,
            row_number=row_number,
        )


def _nested_string(
    value: JsonObject,
    key: str,
    *,
    allowed: frozenset[str],
    file_role: str,
    row_number: int,
) -> str:
    candidate = value.get(key)
    if not isinstance(candidate, str) or candidate not in allowed:
        raise _error(
            PackageErrorCode.INVALID_RECORD,
            file_role=file_role,
            row_number=row_number,
        )
    return candidate


def _unique_index[ModelT: BaseModel](
    records: Sequence[ValidatedRecord[ModelT]],
    key: Callable[[ModelT], str],
    *,
    file_role: str,
) -> dict[str, tuple[int, ValidatedRecord[ModelT]]]:
    result: dict[str, tuple[int, ValidatedRecord[ModelT]]] = {}
    for row_number, record in enumerate(records, start=1):
        identity = key(record.value)
        if identity in result:
            raise _error(
                PackageErrorCode.DUPLICATE_CANONICAL_KEY,
                file_role=file_role,
                row_number=row_number,
            )
        result[identity] = (row_number, record)
    return result


def _validate_citation(
    citation: object,
    *,
    clause_by_key: Mapping[tuple[str, str, int], ClauseRecord],
    terms_alias: str,
    section_id: str,
    file_role: str,
    row_number: int,
) -> None:
    from familycare_api.private_knowledge.models import CitationRecord

    if not isinstance(citation, CitationRecord):
        raise _error(
            PackageErrorCode.SOURCE_LINEAGE_MISMATCH,
            file_role=file_role,
            row_number=row_number,
        )
    clause = clause_by_key.get((terms_alias, section_id, citation.clause_index))
    if clause is None or (
        clause.physical_page_start != citation.physical_page_start
        or clause.physical_page_end != citation.physical_page_end
        or clause.source_text_sha256 != citation.source_text_sha256
    ):
        raise _error(
            PackageErrorCode.SOURCE_LINEAGE_MISMATCH,
            file_role=file_role,
            row_number=row_number,
        )


def _validate_references(
    *,
    contracts: tuple[ValidatedRecord[ContractRecord], ...],
    coverages: tuple[ValidatedRecord[CoverageRecord], ...],
    pairings: tuple[ValidatedRecord[PairingRecord], ...],
    mappings: tuple[ValidatedRecord[MappingRecord], ...],
    sections: tuple[ValidatedRecord[TermsSectionRecord], ...],
    clauses: tuple[ValidatedRecord[ClauseRecord], ...],
    semantic_reviews: tuple[ValidatedRecord[SemanticReviewRecord], ...],
) -> None:
    contract_by_id = _unique_index(
        contracts,
        lambda value: value.canonical_policy_id,
        file_role="contracts.jsonl",
    )
    coverage_by_id = _unique_index(
        coverages,
        lambda value: value.canonical_rider_id,
        file_role="coverage-components.jsonl",
    )
    pairing_by_id = _unique_index(
        pairings,
        lambda value: value.canonical_policy_id,
        file_role="policy-terms-pairings.jsonl",
    )
    mapping_by_id = _unique_index(
        mappings,
        lambda value: value.canonical_rider_id,
        file_role="coverage-terms-mappings.jsonl",
    )
    section_by_key: dict[tuple[str, str], TermsSectionRecord] = {}
    for row_number, section_record in enumerate(sections, start=1):
        section_identity = (
            section_record.value.terms_alias,
            section_record.value.section_id,
        )
        if section_identity in section_by_key:
            raise _error(
                PackageErrorCode.DUPLICATE_CANONICAL_KEY,
                file_role="terms-sections.jsonl",
                row_number=row_number,
            )
        section_by_key[section_identity] = section_record.value
    clause_by_key: dict[tuple[str, str, int], ClauseRecord] = {}
    clause_counts: Counter[tuple[str, str]] = Counter()
    for row_number, clause_record in enumerate(clauses, start=1):
        clause = clause_record.value
        section_key = (clause.terms_alias, clause.section_id)
        if section_key not in section_by_key:
            raise _error(
                PackageErrorCode.BROKEN_REFERENCE,
                file_role="clause-evidence-index.jsonl",
                row_number=row_number,
            )
        clause_identity = (*section_key, clause.clause_index)
        if (
            clause_identity in clause_by_key
            or clause.physical_page_end < clause.physical_page_start
        ):
            raise _error(
                PackageErrorCode.SOURCE_LINEAGE_MISMATCH,
                file_role="clause-evidence-index.jsonl",
                row_number=row_number,
            )
        clause_by_key[clause_identity] = clause
        clause_counts[section_key] += 1

    if set(contract_by_id) != set(pairing_by_id):
        raise _error(PackageErrorCode.BROKEN_REFERENCE, file_role="policy-terms-pairings.jsonl")
    if set(coverage_by_id) != set(mapping_by_id):
        raise _error(PackageErrorCode.BROKEN_REFERENCE, file_role="coverage-terms-mappings.jsonl")

    for row_number, contract_record in enumerate(contracts, start=1):
        contract = contract_record.value
        _validate_date_range(
            contract.contract_start,
            contract.contract_end,
            file_role="contracts.jsonl",
            row_number=row_number,
        )
        nested_policy_id = contract_record.source_record["terms_pairing"]
        if (
            not isinstance(nested_policy_id, dict)
            or nested_policy_id.get("canonical_policy_id") != contract.canonical_policy_id
        ):
            raise _error(
                PackageErrorCode.SOURCE_LINEAGE_MISMATCH,
                file_role="contracts.jsonl",
                row_number=row_number,
            )

    for row_number, coverage_record in enumerate(coverages, start=1):
        coverage = coverage_record.value
        contract_entry = contract_by_id.get(coverage.canonical_policy_id)
        if contract_entry is None or contract_entry[1].value.family_alias != coverage.family_alias:
            raise _error(
                PackageErrorCode.BROKEN_REFERENCE,
                file_role="coverage-components.jsonl",
                row_number=row_number,
            )
        _validate_date_range(
            coverage.coverage_start,
            coverage.coverage_end,
            file_role="coverage-components.jsonl",
            row_number=row_number,
        )
        certificate_review = coverage.certificate_review
        _nested_string(
            certificate_review,
            "component_class",
            allowed=frozenset({"BENEFIT_COVERAGE", "NON_BENEFIT_CONTRACT_COMPONENT"}),
            file_role="coverage-components.jsonl",
            row_number=row_number,
        )
        _nested_string(
            certificate_review,
            "enrollment_decision",
            allowed=frozenset({"MATCH", "NO_MATCH", "UNKNOWN"}),
            file_role="coverage-components.jsonl",
            row_number=row_number,
        )

    for row_number, mapping_record in enumerate(mappings, start=1):
        mapping = mapping_record.value
        coverage = coverage_by_id[mapping.canonical_rider_id][1].value
        if coverage.canonical_policy_id != mapping.canonical_policy_id:
            raise _error(
                PackageErrorCode.BROKEN_REFERENCE,
                file_role="coverage-terms-mappings.jsonl",
                row_number=row_number,
            )
        certificate_review = coverage.certificate_review
        if (
            certificate_review.get("component_class") != mapping.component_class
            or certificate_review.get("enrollment_decision") != mapping.enrollment_decision
        ):
            raise _error(
                PackageErrorCode.SOURCE_LINEAGE_MISMATCH,
                file_role="coverage-terms-mappings.jsonl",
                row_number=row_number,
            )
        if mapping.mapping_decision == "MATCH":
            if mapping.selected_terms_alias is None or mapping.selected_section_id is None:
                raise _error(
                    PackageErrorCode.BROKEN_REFERENCE,
                    file_role="coverage-terms-mappings.jsonl",
                    row_number=row_number,
                )
            section = section_by_key.get(
                (mapping.selected_terms_alias, mapping.selected_section_id)
            )
            if (
                section is None
                or mapping.physical_page != section.physical_page
                or mapping.clause_count
                != clause_counts[(mapping.selected_terms_alias, mapping.selected_section_id)]
            ):
                raise _error(
                    PackageErrorCode.SOURCE_LINEAGE_MISMATCH,
                    file_role="coverage-terms-mappings.jsonl",
                    row_number=row_number,
                )
        elif mapping.mapping_decision == "NOT_APPLICABLE" and (
            mapping.component_class != "NON_BENEFIT_CONTRACT_COMPONENT"
            or mapping.selected_terms_alias is not None
            or mapping.selected_section_id is not None
        ):
            raise _error(
                PackageErrorCode.INVALID_RECORD,
                file_role="coverage-terms-mappings.jsonl",
                row_number=row_number,
            )

    review_keys: set[tuple[str, str]] = set()
    fact_ids: set[tuple[str, str, str]] = set()
    fact_counts: Counter[tuple[str, str]] = Counter()
    for row_number, semantic_record in enumerate(semantic_reviews, start=1):
        semantic_review = semantic_record.value
        section_key = (semantic_review.terms_alias, semantic_review.section_id)
        section = section_by_key.get(section_key)
        if section is None or section_key in review_keys:
            raise _error(
                PackageErrorCode.BROKEN_REFERENCE,
                file_role="terms-semantic-review.jsonl",
                row_number=row_number,
            )
        review_keys.add(section_key)
        if (
            semantic_review.section_physical_page != section.physical_page
            or semantic_review.source_clause_count != clause_counts[section_key]
            or semantic_review.classified_clause_count + semantic_review.unclassified_clause_count
            != semantic_review.source_clause_count
        ):
            raise _error(
                PackageErrorCode.SOURCE_LINEAGE_MISMATCH,
                file_role="terms-semantic-review.jsonl",
                row_number=row_number,
            )
        for citation in semantic_review.summary_citations:
            _validate_citation(
                citation,
                clause_by_key=clause_by_key,
                terms_alias=semantic_review.terms_alias,
                section_id=semantic_review.section_id,
                file_role="terms-semantic-review.jsonl",
                row_number=row_number,
            )
        for fact in semantic_review.facts:
            fact_key = (
                semantic_review.terms_alias,
                semantic_review.section_id,
                fact.fact_id,
            )
            if fact_key in fact_ids:
                raise _error(
                    PackageErrorCode.DUPLICATE_CANONICAL_KEY,
                    file_role="terms-semantic-review.jsonl",
                    row_number=row_number,
                )
            fact_ids.add(fact_key)
            fact_counts[section_key] += 1
            for citation in fact.citations:
                _validate_citation(
                    citation,
                    clause_by_key=clause_by_key,
                    terms_alias=semantic_review.terms_alias,
                    section_id=semantic_review.section_id,
                    file_role="terms-semantic-review.jsonl",
                    row_number=row_number,
                )

    if review_keys != set(section_by_key):
        raise _error(PackageErrorCode.BROKEN_REFERENCE, file_role="terms-semantic-review.jsonl")
    for section_key, section in section_by_key.items():
        if (
            section.source_clause_count != clause_counts[section_key]
            or section.semantic_fact_count != fact_counts[section_key]
        ):
            raise _error(
                PackageErrorCode.SOURCE_LINEAGE_MISMATCH,
                file_role="terms-sections.jsonl",
            )


def _derived_counts(
    *,
    contracts: Sequence[ValidatedRecord[ContractRecord]],
    coverages: Sequence[ValidatedRecord[CoverageRecord]],
    pairings: Sequence[ValidatedRecord[PairingRecord]],
    mappings: Sequence[ValidatedRecord[MappingRecord]],
    sections: Sequence[ValidatedRecord[TermsSectionRecord]],
    clauses: Sequence[ValidatedRecord[ClauseRecord]],
    semantic_reviews: Sequence[ValidatedRecord[SemanticReviewRecord]],
) -> dict[str, int]:
    previous_audits = [
        item for review in semantic_reviews for item in review.value.previous_fact_audit
    ]
    inherited_object_rows = sum(
        1
        for record in coverages
        if record.value.certificate_review.get("evidence_inherited_from_rider_id") is not None
    )
    return {
        "policy_count": len(contracts),
        "coverage_component_count": len(coverages),
        "benefit_coverage_count": sum(
            1 for record in mappings if record.value.component_class == "BENEFIT_COVERAGE"
        ),
        "non_benefit_contract_component_count": sum(
            1
            for record in mappings
            if record.value.component_class == "NON_BENEFIT_CONTRACT_COMPONENT"
        ),
        "certificate_enrollment_match_count": sum(
            1 for record in mappings if record.value.enrollment_decision == "MATCH"
        ),
        "certificate_enrollment_unknown_count": sum(
            1 for record in mappings if record.value.enrollment_decision == "UNKNOWN"
        ),
        "policy_terms_identity_match_count": sum(
            1 for record in pairings if record.value.document_identity_decision == "MATCH"
        ),
        "policy_terms_edition_match_count": sum(
            1 for record in pairings if record.value.edition_applicability_decision == "MATCH"
        ),
        "coverage_section_match_count": sum(
            1 for record in mappings if record.value.mapping_decision == "MATCH"
        ),
        "coverage_section_unknown_count": sum(
            1 for record in mappings if record.value.mapping_decision == "UNKNOWN"
        ),
        "coverage_section_not_applicable_count": sum(
            1 for record in mappings if record.value.mapping_decision == "NOT_APPLICABLE"
        ),
        "terms_section_review_count": len(sections),
        "source_clause_review_count": len(clauses),
        "semantic_fact_count": sum(len(record.value.facts) for record in semantic_reviews),
        "previous_fact_recheck_count": len(previous_audits),
        "previous_fact_needs_review_count": sum(
            1
            for item in previous_audits
            if isinstance(item, dict) and item.get("review_decision") == "NEEDS_REVIEW"
        ),
        "restored_distinct_object_row_count": inherited_object_rows,
        "true_duplicate_row_count": sum(
            record.value.duplicate_rows_removed for record in contracts
        ),
        "current_status_unknown_count": sum(
            1 for record in coverages if record.value.current_status == "UNKNOWN"
        ),
        "database_write_count": 0,
        "executable_rule_count": 0,
    }


def _validate_reconciliation(
    manifest: PackageManifest,
    reconciliation: ReconciliationCounts,
    derived: Mapping[str, int],
) -> None:
    manifest_counts = manifest.counts.model_dump(mode="python")
    reconciliation_counts = reconciliation.model_dump(mode="python")
    if (
        manifest_counts != reconciliation_counts
        or reconciliation_counts != dict(derived)
        or reconciliation.database_write_count != 0
        or reconciliation.executable_rule_count != 0
    ):
        raise _error(
            PackageErrorCode.RECONCILIATION_MISMATCH,
            file_role="reconciliation.json",
        )


def load_private_knowledge_package(
    root: Path,
    *,
    repository_root: Path,
) -> PrivateKnowledgePackage:
    """Validate, close references, and normalize one external package."""

    if not root.is_absolute():
        raise _error(PackageErrorCode.ROOT_NOT_ABSOLUTE)
    try:
        root_stat = os.lstat(root)
    except OSError:
        raise _error(PackageErrorCode.ROOT_NOT_DIRECTORY) from None
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        raise _error(PackageErrorCode.ROOT_NOT_DIRECTORY)
    if stat.S_IMODE(root_stat.st_mode) != 0o700:
        raise _error(PackageErrorCode.ROOT_MODE_INVALID)
    resolved_root = root.resolve(strict=True)
    resolved_repository = repository_root.resolve(strict=False)
    if _is_inside(resolved_root, resolved_repository):
        raise _error(PackageErrorCode.ROOT_INSIDE_REPOSITORY)

    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        root_fd = os.open(root, flags)
    except OSError:
        raise _error(PackageErrorCode.ROOT_NOT_DIRECTORY) from None
    try:
        manifest_payload = _read_file(root_fd, MANIFEST_NAME)
        manifest_source = _parse_json_object(
            manifest_payload,
            file_role=MANIFEST_NAME,
        )
        if manifest_source.get("schema_version") != SCHEMA_VERSION:
            raise _error(PackageErrorCode.UNSUPPORTED_SCHEMA, file_role=MANIFEST_NAME)
        try:
            manifest = PackageManifest.model_validate(manifest_source)
        except ValidationError:
            raise _error(PackageErrorCode.MANIFEST_INVALID, file_role=MANIFEST_NAME) from None

        entries: dict[str, tuple[int, str]] = {}
        for entry in manifest.files:
            if entry.name in entries:
                raise _error(
                    PackageErrorCode.DUPLICATE_MANIFEST_ENTRY,
                    file_role=MANIFEST_NAME,
                )
            if Path(entry.name).name != entry.name or entry.name not in ALLOWED_MANIFEST_FILES:
                raise _error(PackageErrorCode.UNSUPPORTED_FILE, file_role=MANIFEST_NAME)
            entries[entry.name] = (entry.bytes, entry.sha256)
        missing = REQUIRED_DATA_FILES - set(entries)
        if missing:
            raise _error(
                PackageErrorCode.MISSING_REQUIRED_FILE,
                file_role=sorted(missing)[0],
            )
        if sum(size for size, _ in entries.values()) > MAX_TOTAL_BYTES:
            raise _error(PackageErrorCode.TOTAL_SIZE_LIMIT, file_role=MANIFEST_NAME)

        payloads: dict[str, bytes] = {}
        for name in sorted(entries):
            expected_bytes, expected_sha256 = entries[name]
            payload = _read_file(
                root_fd,
                name,
                expected_bytes=expected_bytes,
                expected_sha256=expected_sha256,
            )
            if name in REQUIRED_DATA_FILES:
                payloads[name] = payload
        actual_names = set(os.listdir(root_fd))
        expected_names = set(entries) | {MANIFEST_NAME}
        if actual_names != expected_names:
            raise _error(PackageErrorCode.UNEXPECTED_FILE)
    finally:
        os.close(root_fd)

    contracts = _parse_jsonl(
        payloads["contracts.jsonl"],
        file_role="contracts.jsonl",
        model_type=ContractRecord,
    )
    coverages = _parse_jsonl(
        payloads["coverage-components.jsonl"],
        file_role="coverage-components.jsonl",
        model_type=CoverageRecord,
    )
    pairings = _parse_jsonl(
        payloads["policy-terms-pairings.jsonl"],
        file_role="policy-terms-pairings.jsonl",
        model_type=PairingRecord,
    )
    mappings = _parse_jsonl(
        payloads["coverage-terms-mappings.jsonl"],
        file_role="coverage-terms-mappings.jsonl",
        model_type=MappingRecord,
    )
    sections = _parse_jsonl(
        payloads["terms-sections.jsonl"],
        file_role="terms-sections.jsonl",
        model_type=TermsSectionRecord,
    )
    clauses = _parse_jsonl(
        payloads["clause-evidence-index.jsonl"],
        file_role="clause-evidence-index.jsonl",
        model_type=ClauseRecord,
    )
    semantic_reviews = _parse_jsonl(
        payloads["terms-semantic-review.jsonl"],
        file_role="terms-semantic-review.jsonl",
        model_type=SemanticReviewRecord,
    )
    reconciliation_source = _parse_json_object(
        payloads["reconciliation.json"],
        file_role="reconciliation.json",
    )
    try:
        reconciliation = ReconciliationCounts.model_validate(reconciliation_source)
    except ValidationError:
        raise _error(
            PackageErrorCode.INVALID_RECORD,
            file_role="reconciliation.json",
        ) from None

    _validate_references(
        contracts=contracts,
        coverages=coverages,
        pairings=pairings,
        mappings=mappings,
        sections=sections,
        clauses=clauses,
        semantic_reviews=semantic_reviews,
    )
    derived = _derived_counts(
        contracts=contracts,
        coverages=coverages,
        pairings=pairings,
        mappings=mappings,
        sections=sections,
        clauses=clauses,
        semantic_reviews=semantic_reviews,
    )
    _validate_reconciliation(manifest, reconciliation, derived)

    manifest_projection = _manifest_projection(manifest)
    manifest_digest = _sha256(_canonical_json(manifest_projection))
    package = PrivateKnowledgePackage(
        schema_version=SCHEMA_VERSION,
        manifest=manifest,
        manifest_digest_sha256=manifest_digest,
        package_digest_sha256=_sha256(
            b"familycare-private-knowledge-sol-v2\x00" + _canonical_json(manifest_projection)
        ),
        contracts=contracts,
        coverages=coverages,
        pairings=pairings,
        mappings=mappings,
        sections=sections,
        clauses=clauses,
        semantic_reviews=semantic_reviews,
        reconciliation=reconciliation,
    )
    return package
