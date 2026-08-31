"""Descriptor-safe loader for reviewed private-knowledge rule packages."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, JsonValue, ValidationError

from familycare_api.clauses.dsl import RuleValidationError, validate_rule_document
from familycare_api.private_knowledge.errors import (
    PublicationErrorCode,
    PublicationPackageError,
)
from familycare_api.private_knowledge.publication_models import (
    CalculationCitationRecord,
    CalculationPublicationRecord,
    ContractStatusIntervalRecord,
    CoverageDispositionRecord,
    CoverageDispositionRecordV2,
    FactNormalizerRecord,
    PublicationCounts,
    PublicationCountsV2,
    PublicationManifest,
    PublicationManifestV2,
    RuleCitationRecord,
    RulePublicationRecord,
)

SCHEMA_VERSION = "private-knowledge-rule-publication.sol-v1"
SCHEMA_VERSION_V2 = "private-knowledge-rule-publication.sol-v2"
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION, SCHEMA_VERSION_V2})
MANIFEST_NAME = "manifest.json"
PUBLICATION_DATA_FILES = frozenset(
    {
        "coverage-dispositions.jsonl",
        "contract-status-intervals.jsonl",
        "fact-normalizers.jsonl",
        "rule-publications.jsonl",
        "rule-citations.jsonl",
        "calculation-publications.jsonl",
        "calculation-citations.jsonl",
        "reconciliation.json",
    }
)
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_JSONL_LINE_BYTES = 1024 * 1024
MAX_NESTED_DEPTH = 16
MAX_NESTED_ITEMS = 256
MAX_NESTED_STRING_LENGTH = 16_384
MAX_NESTED_NODES = 20_000
MAX_ROWS_BY_ROLE: Mapping[str, int] = {
    "coverage-dispositions.jsonl": 100_000,
    "contract-status-intervals.jsonl": 100_000,
    "fact-normalizers.jsonl": 100_000,
    "rule-publications.jsonl": 500_000,
    "rule-citations.jsonl": 1_000_000,
    "calculation-publications.jsonl": 500_000,
    "calculation-citations.jsonl": 1_000_000,
}

JsonObject = dict[str, JsonValue]
PublicationSchemaVersion = Literal[
    "private-knowledge-rule-publication.sol-v1",
    "private-knowledge-rule-publication.sol-v2",
]
CoverageDisposition = CoverageDispositionRecord | CoverageDispositionRecordV2


@dataclass(frozen=True, slots=True)
class ValidatedPublicationRecord[ModelT: BaseModel]:
    value: ModelT
    record_digest_sha256: str


@dataclass(frozen=True, slots=True)
class RulePublicationPackage:
    schema_version: PublicationSchemaVersion
    manifest: PublicationManifest | PublicationManifestV2
    manifest_digest_sha256: str
    package_digest_sha256: str
    coverage_dispositions: tuple[ValidatedPublicationRecord[CoverageDisposition], ...]
    status_intervals: tuple[ValidatedPublicationRecord[ContractStatusIntervalRecord], ...]
    fact_normalizers: tuple[ValidatedPublicationRecord[FactNormalizerRecord], ...]
    rule_publications: tuple[ValidatedPublicationRecord[RulePublicationRecord], ...]
    rule_citations: tuple[ValidatedPublicationRecord[RuleCitationRecord], ...]
    calculation_publications: tuple[ValidatedPublicationRecord[CalculationPublicationRecord], ...]
    calculation_citations: tuple[ValidatedPublicationRecord[CalculationCitationRecord], ...]
    reconciliation: PublicationCounts | PublicationCountsV2

    @property
    def subject_aliases(self) -> tuple[str, ...]:
        return tuple(sorted({record.value.family_alias for record in self.coverage_dispositions}))


def _error(
    code: PublicationErrorCode,
    *,
    file_role: str | None = None,
    row_number: int | None = None,
) -> PublicationPackageError:
    return PublicationPackageError(code, file_role=file_role, row_number=row_number)


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


def _manifest_projection(
    manifest: PublicationManifest | PublicationManifestV2,
) -> dict[str, object]:
    projection = manifest.model_dump(mode="json")
    projection["files"] = sorted(
        cast(list[dict[str, object]], projection["files"]),
        key=lambda item: cast(str, item["name"]),
    )
    return projection


def canonical_rule_publication_digest(package: RulePublicationPackage) -> str:
    canonical_manifest = _canonical_json(_manifest_projection(package.manifest))
    domain = (
        b"familycare-private-rule-publication-sol-v1\x00"
        if package.schema_version == SCHEMA_VERSION
        else b"familycare-private-rule-publication-sol-v2\x00"
    )
    return _sha256(domain + canonical_manifest)


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
            PublicationErrorCode.MANIFEST_MISSING
            if name == MANIFEST_NAME
            else PublicationErrorCode.MISSING_REQUIRED_FILE
        )
        raise _error(code, file_role=name) from None
    if not stat.S_ISREG(before.st_mode):
        raise _error(PublicationErrorCode.FILE_NOT_REGULAR, file_role=name)
    if stat.S_IMODE(before.st_mode) != 0o600:
        raise _error(PublicationErrorCode.FILE_MODE_INVALID, file_role=name)
    if before.st_size > MAX_FILE_BYTES:
        raise _error(PublicationErrorCode.FILE_SIZE_LIMIT, file_role=name)

    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(name, flags, dir_fd=root_fd)
    except OSError:
        raise _error(PublicationErrorCode.FILE_NOT_REGULAR, file_role=name) from None
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
        if not stat.S_ISREG(opened.st_mode) or identity_opened != identity_before:
            raise _error(PublicationErrorCode.FILE_CHANGED, file_role=name)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise _error(PublicationErrorCode.FILE_SIZE_LIMIT, file_role=name)
            chunks.append(chunk)
        after_open = os.fstat(fd)
    finally:
        os.close(fd)

    try:
        after_path = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    except OSError:
        raise _error(PublicationErrorCode.FILE_CHANGED, file_role=name) from None
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
        raise _error(PublicationErrorCode.FILE_CHANGED, file_role=name)

    payload = b"".join(chunks)
    if expected_bytes is not None and len(payload) != expected_bytes:
        raise _error(PublicationErrorCode.FILE_SIZE_MISMATCH, file_role=name)
    if expected_sha256 is not None and _sha256(payload) != expected_sha256:
        raise _error(PublicationErrorCode.FILE_DIGEST_MISMATCH, file_role=name)
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
            PublicationErrorCode.INVALID_JSON,
            file_role=file_role,
            row_number=row_number,
        ) from None
    if not isinstance(parsed, dict):
        raise _error(
            PublicationErrorCode.INVALID_RECORD,
            file_role=file_role,
            row_number=row_number,
        )
    result = cast(JsonObject, parsed)
    _validate_nested_value(result, file_role=file_role, row_number=row_number)
    return result


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
                PublicationErrorCode.NESTED_VALUE_LIMIT,
                file_role=file_role,
                row_number=row_number,
            )
        if isinstance(value, str):
            if len(value) > MAX_NESTED_STRING_LENGTH:
                raise _error(
                    PublicationErrorCode.NESTED_VALUE_LIMIT,
                    file_role=file_role,
                    row_number=row_number,
                )
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise _error(
                    PublicationErrorCode.NESTED_VALUE_LIMIT,
                    file_role=file_role,
                    row_number=row_number,
                )
        elif isinstance(value, list):
            if len(value) > MAX_NESTED_ITEMS:
                raise _error(
                    PublicationErrorCode.NESTED_VALUE_LIMIT,
                    file_role=file_role,
                    row_number=row_number,
                )
            stack.extend((item, depth + 1) for item in value)
        elif isinstance(value, dict):
            if len(value) > MAX_NESTED_ITEMS:
                raise _error(
                    PublicationErrorCode.NESTED_VALUE_LIMIT,
                    file_role=file_role,
                    row_number=row_number,
                )
            for key, child in value.items():
                if not isinstance(key, str) or not key or len(key) > 160:
                    raise _error(
                        PublicationErrorCode.NESTED_VALUE_LIMIT,
                        file_role=file_role,
                        row_number=row_number,
                    )
                stack.append((child, depth + 1))


def _record_digest(value: BaseModel) -> str:
    return _sha256(_canonical_json(value.model_dump(mode="json")))


def _parse_model[ModelT: BaseModel](
    payload: bytes,
    *,
    file_role: str,
    model_type: type[ModelT],
    error_code: PublicationErrorCode = PublicationErrorCode.INVALID_RECORD,
) -> ModelT:
    _parse_json_object(payload, file_role=file_role)
    try:
        return model_type.model_validate_json(payload)
    except ValidationError:
        raise _error(error_code, file_role=file_role) from None


def _parse_jsonl[ModelT: BaseModel](
    payload: bytes,
    *,
    file_role: str,
    model_type: type[ModelT],
) -> tuple[ValidatedPublicationRecord[ModelT], ...]:
    if not payload:
        return ()
    lines = payload.splitlines()
    if len(lines) > MAX_ROWS_BY_ROLE[file_role]:
        raise _error(PublicationErrorCode.ROW_LIMIT, file_role=file_role)
    records: list[ValidatedPublicationRecord[ModelT]] = []
    for row_number, line in enumerate(lines, start=1):
        if not line or len(line) > MAX_JSONL_LINE_BYTES:
            code = (
                PublicationErrorCode.INVALID_JSON
                if not line
                else PublicationErrorCode.FILE_SIZE_LIMIT
            )
            raise _error(code, file_role=file_role, row_number=row_number)
        _parse_json_object(line, file_role=file_role, row_number=row_number)
        try:
            value = model_type.model_validate_json(line)
        except ValidationError:
            raise _error(
                PublicationErrorCode.INVALID_RECORD,
                file_role=file_role,
                row_number=row_number,
            ) from None
        records.append(
            ValidatedPublicationRecord(
                value=value,
                record_digest_sha256=_record_digest(value),
            )
        )
    return tuple(records)


def _unique_index[ModelT: BaseModel](
    records: Sequence[ValidatedPublicationRecord[ModelT]],
    key: Callable[[ModelT], str],
    *,
    file_role: str,
) -> dict[str, tuple[int, ValidatedPublicationRecord[ModelT]]]:
    result: dict[str, tuple[int, ValidatedPublicationRecord[ModelT]]] = {}
    for row_number, record in enumerate(records, start=1):
        identity = key(record.value)
        if identity in result:
            raise _error(
                PublicationErrorCode.DUPLICATE_CANONICAL_KEY,
                file_role=file_role,
                row_number=row_number,
            )
        result[identity] = (row_number, record)
    return result


def _dsl_error(error: RuleValidationError) -> PublicationErrorCode:
    if error.reason_code == "ARBITRARY_EXECUTABLE":
        return PublicationErrorCode.EXECUTABLE_INPUT
    return PublicationErrorCode.UNSUPPORTED_DSL


def _validate_rule(
    record: RulePublicationRecord,
    citation_keys: set[str],
    *,
    file_role: str,
    row_number: int,
) -> None:
    try:
        validated = validate_rule_document(
            cast(Mapping[str, object], record.rule_document),
            citation_keys,
        )
    except RuleValidationError as error:
        raise _error(
            _dsl_error(error),
            file_role=file_role,
            row_number=row_number,
        ) from None
    if (
        validated.rule_kind != record.rule_kind
        or validated.required is not record.required
        or validated.result_reason_code != record.result_reason_code
        or set(str(value) for value in validated.evidence_ids) != citation_keys
    ):
        raise _error(
            PublicationErrorCode.UNSUPPORTED_DSL,
            file_role=file_role,
            row_number=row_number,
        )


def _validate_calculation(
    record: CalculationPublicationRecord,
    citation_keys: set[str],
    *,
    row_number: int,
) -> None:
    file_role = "calculation-publications.jsonl"
    try:
        validated = validate_rule_document(
            cast(Mapping[str, object], record.calculation_document),
            citation_keys,
        )
    except RuleValidationError as error:
        raise _error(
            _dsl_error(error),
            file_role=file_role,
            row_number=row_number,
        ) from None
    allowed_kinds = (
        {"fixed_amount", "rate_amount"}
        if record.calculation_kind == "FIXED"
        else {"rate_amount", "deductible", "limit"}
    )
    if (
        validated.calculation is None
        or validated.rule_kind not in allowed_kinds
        or validated.result_reason_code != record.result_reason_code
        or set(str(value) for value in validated.evidence_ids) != citation_keys
    ):
        raise _error(
            PublicationErrorCode.UNSUPPORTED_DSL,
            file_role=file_role,
            row_number=row_number,
        )


def _derived_counts(
    package: RulePublicationPackage,
) -> PublicationCounts | PublicationCountsV2:
    dispositions = [record.value for record in package.coverage_dispositions]
    values: dict[str, int] = dict(
        subject_count=len({value.source_subject_key for value in dispositions}),
        contract_count=len({value.canonical_policy_id for value in dispositions}),
        coverage_count=len(dispositions),
        disposition_count=len(dispositions),
        published_disposition_count=sum(value.disposition == "PUBLISHED" for value in dispositions),
        blocked_disposition_count=sum(value.disposition == "BLOCKED" for value in dispositions),
        not_applicable_disposition_count=sum(
            value.disposition == "NOT_APPLICABLE" for value in dispositions
        ),
        status_interval_count=len(package.status_intervals),
        fact_normalizer_count=len(package.fact_normalizers),
        rule_publication_count=len(package.rule_publications),
        rule_citation_count=len(package.rule_citations),
        calculation_publication_count=len(package.calculation_publications),
        calculation_citation_count=len(package.calculation_citations),
    )
    if package.schema_version == SCHEMA_VERSION_V2:
        values["advisory_disposition_count"] = sum(
            value.disposition == "ADVISORY" for value in dispositions
        )
        values["user_confirmed_enrollment_count"] = sum(
            getattr(value, "enrollment_authority", None) == "USER_CONFIRMED_COVERAGE_ENROLLMENT"
            for value in dispositions
        )
        return PublicationCountsV2(**values)
    return PublicationCounts(**values)


def _validate_references(package: RulePublicationPackage) -> None:
    dispositions = _unique_index(
        package.coverage_dispositions,
        lambda value: value.canonical_coverage_id,
        file_role="coverage-dispositions.jsonl",
    )
    policies = {record.value.canonical_policy_id for record in package.coverage_dispositions}

    intervals_by_policy: dict[str, list[tuple[int, ContractStatusIntervalRecord]]] = defaultdict(
        list
    )
    for row_number, interval_record in enumerate(package.status_intervals, start=1):
        value = interval_record.value
        if value.canonical_policy_id not in policies:
            raise _error(
                PublicationErrorCode.BROKEN_REFERENCE,
                file_role="contract-status-intervals.jsonl",
                row_number=row_number,
            )
        intervals_by_policy[value.canonical_policy_id].append((row_number, value))
    for intervals in intervals_by_policy.values():
        intervals.sort(key=lambda item: (item[1].effective_from, item[1].effective_through))
        for previous, current in zip(intervals, intervals[1:], strict=False):
            if current[1].effective_from <= previous[1].effective_through:
                raise _error(
                    PublicationErrorCode.DUPLICATE_CANONICAL_KEY,
                    file_role="contract-status-intervals.jsonl",
                    row_number=current[0],
                )

    _unique_index(
        package.fact_normalizers,
        lambda value: value.normalizer_key,
        file_role="fact-normalizers.jsonl",
    )
    normalizer_matches: set[tuple[str, str]] = set()
    for row_number, normalizer_record in enumerate(package.fact_normalizers, start=1):
        identity = (
            normalizer_record.value.field_path,
            normalizer_record.value.phrase,
        )
        if identity in normalizer_matches:
            raise _error(
                PublicationErrorCode.DUPLICATE_CANONICAL_KEY,
                file_role="fact-normalizers.jsonl",
                row_number=row_number,
            )
        normalizer_matches.add(identity)

    rules = _unique_index(
        package.rule_publications,
        lambda value: value.rule_key,
        file_role="rule-publications.jsonl",
    )
    rule_citations = _unique_index(
        package.rule_citations,
        lambda value: value.citation_key,
        file_role="rule-citations.jsonl",
    )
    citations_by_rule: dict[str, set[str]] = defaultdict(set)
    for row_number, rule_citation_record in enumerate(package.rule_citations, start=1):
        rule_citation = rule_citation_record.value
        rule_entry = rules.get(rule_citation.rule_key)
        if rule_entry is None:
            raise _error(
                PublicationErrorCode.BROKEN_REFERENCE,
                file_role="rule-citations.jsonl",
                row_number=row_number,
            )
        rule = rule_entry[1].value
        if (
            rule_citation.canonical_policy_id != rule.canonical_policy_id
            or rule_citation.canonical_coverage_id != rule.canonical_coverage_id
        ):
            raise _error(
                PublicationErrorCode.BROKEN_REFERENCE,
                file_role="rule-citations.jsonl",
                row_number=row_number,
            )
        citations_by_rule[rule_citation.rule_key].add(rule_citation.citation_key)

    published_rule_coverages: set[str] = set()
    for row_number, rule_record in enumerate(package.rule_publications, start=1):
        rule = rule_record.value
        disposition_entry = dispositions.get(rule.canonical_coverage_id)
        if disposition_entry is None:
            raise _error(
                PublicationErrorCode.INCOMPLETE_DISPOSITION_CLOSURE,
                file_role="rule-publications.jsonl",
                row_number=row_number,
            )
        disposition = disposition_entry[1].value
        disposition_accepts_artifact = disposition.disposition == "PUBLISHED" or (
            package.schema_version == SCHEMA_VERSION_V2 and disposition.disposition == "ADVISORY"
        )
        if (
            disposition.canonical_policy_id != rule.canonical_policy_id
            or not disposition_accepts_artifact
        ):
            raise _error(
                PublicationErrorCode.BROKEN_REFERENCE,
                file_role="rule-publications.jsonl",
                row_number=row_number,
            )
        citation_keys = citations_by_rule.get(rule.rule_key, set())
        if not citation_keys:
            raise _error(
                PublicationErrorCode.MISSING_CITATION,
                file_role="rule-publications.jsonl",
                row_number=row_number,
            )
        _validate_rule(
            rule,
            citation_keys,
            file_role="rule-publications.jsonl",
            row_number=row_number,
        )
        if disposition.disposition == "PUBLISHED":
            published_rule_coverages.add(rule.canonical_coverage_id)

    calculations = _unique_index(
        package.calculation_publications,
        lambda value: value.calculation_key,
        file_role="calculation-publications.jsonl",
    )
    _unique_index(
        package.calculation_citations,
        lambda value: value.citation_key,
        file_role="calculation-citations.jsonl",
    )
    citations_by_calculation: dict[str, set[str]] = defaultdict(set)
    for row_number, calculation_citation_record in enumerate(
        package.calculation_citations,
        start=1,
    ):
        calculation_citation = calculation_citation_record.value
        calculation_entry = calculations.get(calculation_citation.calculation_key)
        if calculation_entry is None:
            raise _error(
                PublicationErrorCode.BROKEN_REFERENCE,
                file_role="calculation-citations.jsonl",
                row_number=row_number,
            )
        calculation = calculation_entry[1].value
        if (
            calculation_citation.canonical_policy_id != calculation.canonical_policy_id
            or calculation_citation.canonical_coverage_id != calculation.canonical_coverage_id
        ):
            raise _error(
                PublicationErrorCode.BROKEN_REFERENCE,
                file_role="calculation-citations.jsonl",
                row_number=row_number,
            )
        citations_by_calculation[calculation_citation.calculation_key].add(
            calculation_citation.citation_key
        )

    for row_number, calculation_record in enumerate(
        package.calculation_publications,
        start=1,
    ):
        calculation = calculation_record.value
        disposition_entry = dispositions.get(calculation.canonical_coverage_id)
        if disposition_entry is None:
            raise _error(
                PublicationErrorCode.INCOMPLETE_DISPOSITION_CLOSURE,
                file_role="calculation-publications.jsonl",
                row_number=row_number,
            )
        disposition = disposition_entry[1].value
        disposition_accepts_artifact = disposition.disposition == "PUBLISHED" or (
            package.schema_version == SCHEMA_VERSION_V2 and disposition.disposition == "ADVISORY"
        )
        if (
            disposition.canonical_policy_id != calculation.canonical_policy_id
            or not disposition_accepts_artifact
            or disposition.benefit_type != calculation.calculation_kind
        ):
            raise _error(
                PublicationErrorCode.BROKEN_REFERENCE,
                file_role="calculation-publications.jsonl",
                row_number=row_number,
            )
        citation_keys = citations_by_calculation.get(calculation.calculation_key, set())
        if not citation_keys:
            raise _error(
                PublicationErrorCode.MISSING_CITATION,
                file_role="calculation-publications.jsonl",
                row_number=row_number,
            )
        _validate_calculation(calculation, citation_keys, row_number=row_number)

    published_coverages = {
        identity
        for identity, (_, record) in dispositions.items()
        if record.value.disposition == "PUBLISHED"
    }
    if published_coverages != published_rule_coverages:
        raise _error(
            PublicationErrorCode.INCOMPLETE_DISPOSITION_CLOSURE,
            file_role="coverage-dispositions.jsonl",
        )
    if len(rule_citations) != len(package.rule_citations):
        raise _error(
            PublicationErrorCode.DUPLICATE_CANONICAL_KEY,
            file_role="rule-citations.jsonl",
        )


def _validate_reconciliation(package: RulePublicationPackage) -> None:
    derived = _derived_counts(package)
    if package.manifest.counts != package.reconciliation or package.reconciliation != derived:
        raise _error(
            PublicationErrorCode.RECONCILIATION_MISMATCH,
            file_role="reconciliation.json",
        )


def load_rule_publication_package(
    root: Path,
    *,
    repository_root: Path,
) -> RulePublicationPackage:
    """Validate an external publication package without a database write."""

    if not root.is_absolute():
        raise _error(PublicationErrorCode.ROOT_NOT_ABSOLUTE)
    try:
        root_stat = os.lstat(root)
    except OSError:
        raise _error(PublicationErrorCode.ROOT_NOT_DIRECTORY) from None
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        raise _error(PublicationErrorCode.ROOT_NOT_DIRECTORY)
    if stat.S_IMODE(root_stat.st_mode) != 0o700:
        raise _error(PublicationErrorCode.ROOT_MODE_INVALID)
    resolved_root = root.resolve(strict=True)
    resolved_repository = repository_root.resolve(strict=False)
    if _is_inside(resolved_root, resolved_repository):
        raise _error(PublicationErrorCode.ROOT_INSIDE_REPOSITORY)

    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        root_fd = os.open(root, flags)
    except OSError:
        raise _error(PublicationErrorCode.ROOT_NOT_DIRECTORY) from None
    try:
        opened_root = os.fstat(root_fd)
        root_identity = (
            root_stat.st_dev,
            root_stat.st_ino,
            root_stat.st_mode,
            root_stat.st_ctime_ns,
            root_stat.st_mtime_ns,
        )
        opened_identity = (
            opened_root.st_dev,
            opened_root.st_ino,
            opened_root.st_mode,
            opened_root.st_ctime_ns,
            opened_root.st_mtime_ns,
        )
        if not stat.S_ISDIR(opened_root.st_mode) or opened_identity != root_identity:
            raise _error(PublicationErrorCode.ROOT_NOT_DIRECTORY)

        manifest_payload = _read_file(root_fd, MANIFEST_NAME)
        manifest_source = _parse_json_object(
            manifest_payload,
            file_role=MANIFEST_NAME,
        )
        schema_version = manifest_source.get("schema_version")
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise _error(PublicationErrorCode.UNSUPPORTED_SCHEMA, file_role=MANIFEST_NAME)
        manifest: PublicationManifest | PublicationManifestV2
        if schema_version == SCHEMA_VERSION:
            manifest = _parse_model(
                manifest_payload,
                file_role=MANIFEST_NAME,
                model_type=PublicationManifest,
                error_code=PublicationErrorCode.MANIFEST_INVALID,
            )
        else:
            manifest = _parse_model(
                manifest_payload,
                file_role=MANIFEST_NAME,
                model_type=PublicationManifestV2,
                error_code=PublicationErrorCode.MANIFEST_INVALID,
            )

        entries: dict[str, tuple[int, str]] = {}
        for entry in manifest.files:
            if entry.name in entries:
                raise _error(
                    PublicationErrorCode.DUPLICATE_MANIFEST_ENTRY,
                    file_role=MANIFEST_NAME,
                )
            if Path(entry.name).name != entry.name or entry.name not in PUBLICATION_DATA_FILES:
                raise _error(PublicationErrorCode.UNSUPPORTED_FILE, file_role=MANIFEST_NAME)
            entries[entry.name] = (entry.bytes, entry.sha256)
        missing = PUBLICATION_DATA_FILES - set(entries)
        if missing:
            raise _error(
                PublicationErrorCode.MISSING_REQUIRED_FILE,
                file_role=sorted(missing)[0],
            )
        if sum(size for size, _ in entries.values()) > MAX_TOTAL_BYTES:
            raise _error(PublicationErrorCode.TOTAL_SIZE_LIMIT, file_role=MANIFEST_NAME)

        payloads: dict[str, bytes] = {}
        for name in sorted(entries):
            expected_bytes, expected_digest = entries[name]
            payloads[name] = _read_file(
                root_fd,
                name,
                expected_bytes=expected_bytes,
                expected_sha256=expected_digest,
            )
        if set(os.listdir(root_fd)) != set(entries) | {MANIFEST_NAME}:
            raise _error(PublicationErrorCode.UNEXPECTED_FILE)
        closed_root = os.fstat(root_fd)
        closed_identity = (
            closed_root.st_dev,
            closed_root.st_ino,
            closed_root.st_mode,
            closed_root.st_ctime_ns,
            closed_root.st_mtime_ns,
        )
        if closed_identity != opened_identity:
            raise _error(PublicationErrorCode.FILE_CHANGED)
    finally:
        os.close(root_fd)

    if schema_version == SCHEMA_VERSION:
        coverage_dispositions = cast(
            tuple[ValidatedPublicationRecord[CoverageDisposition], ...],
            _parse_jsonl(
                payloads["coverage-dispositions.jsonl"],
                file_role="coverage-dispositions.jsonl",
                model_type=CoverageDispositionRecord,
            ),
        )
    else:
        coverage_dispositions = cast(
            tuple[ValidatedPublicationRecord[CoverageDisposition], ...],
            _parse_jsonl(
                payloads["coverage-dispositions.jsonl"],
                file_role="coverage-dispositions.jsonl",
                model_type=CoverageDispositionRecordV2,
            ),
        )
    status_intervals = _parse_jsonl(
        payloads["contract-status-intervals.jsonl"],
        file_role="contract-status-intervals.jsonl",
        model_type=ContractStatusIntervalRecord,
    )
    fact_normalizers = _parse_jsonl(
        payloads["fact-normalizers.jsonl"],
        file_role="fact-normalizers.jsonl",
        model_type=FactNormalizerRecord,
    )
    rule_publications = _parse_jsonl(
        payloads["rule-publications.jsonl"],
        file_role="rule-publications.jsonl",
        model_type=RulePublicationRecord,
    )
    rule_citations = _parse_jsonl(
        payloads["rule-citations.jsonl"],
        file_role="rule-citations.jsonl",
        model_type=RuleCitationRecord,
    )
    calculation_publications = _parse_jsonl(
        payloads["calculation-publications.jsonl"],
        file_role="calculation-publications.jsonl",
        model_type=CalculationPublicationRecord,
    )
    calculation_citations = _parse_jsonl(
        payloads["calculation-citations.jsonl"],
        file_role="calculation-citations.jsonl",
        model_type=CalculationCitationRecord,
    )
    counts_model = PublicationCounts if schema_version == SCHEMA_VERSION else PublicationCountsV2
    reconciliation = _parse_model(
        payloads["reconciliation.json"],
        file_role="reconciliation.json",
        model_type=counts_model,
    )
    manifest_projection = _manifest_projection(manifest)
    manifest_digest = _sha256(_canonical_json(manifest_projection))
    package = RulePublicationPackage(
        schema_version=cast(PublicationSchemaVersion, schema_version),
        manifest=manifest,
        manifest_digest_sha256=manifest_digest,
        package_digest_sha256="",
        coverage_dispositions=coverage_dispositions,
        status_intervals=status_intervals,
        fact_normalizers=fact_normalizers,
        rule_publications=rule_publications,
        rule_citations=rule_citations,
        calculation_publications=calculation_publications,
        calculation_citations=calculation_citations,
        reconciliation=reconciliation,
    )
    object.__setattr__(package, "package_digest_sha256", canonical_rule_publication_digest(package))
    _validate_references(package)
    _validate_reconciliation(package)
    return package


def _validate_loaded_records[ModelT: BaseModel](
    records: Sequence[ValidatedPublicationRecord[ModelT]],
    *,
    file_role: str,
) -> None:
    for row_number, record in enumerate(records, start=1):
        if _record_digest(record.value) != record.record_digest_sha256:
            raise _error(
                PublicationErrorCode.FILE_CHANGED,
                file_role=file_role,
                row_number=row_number,
            )


def validate_loaded_rule_publication_package(package: RulePublicationPackage) -> None:
    """Recheck an immutable package projection immediately before apply."""

    if package.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise _error(PublicationErrorCode.FILE_CHANGED, file_role=MANIFEST_NAME)
    manifest_projection = _manifest_projection(package.manifest)
    if (
        _sha256(_canonical_json(manifest_projection)) != package.manifest_digest_sha256
        or canonical_rule_publication_digest(package) != package.package_digest_sha256
    ):
        raise _error(PublicationErrorCode.FILE_CHANGED, file_role=MANIFEST_NAME)
    record_groups: tuple[tuple[Sequence[Any], str], ...] = (
        (package.coverage_dispositions, "coverage-dispositions.jsonl"),
        (package.status_intervals, "contract-status-intervals.jsonl"),
        (package.fact_normalizers, "fact-normalizers.jsonl"),
        (package.rule_publications, "rule-publications.jsonl"),
        (package.rule_citations, "rule-citations.jsonl"),
        (package.calculation_publications, "calculation-publications.jsonl"),
        (package.calculation_citations, "calculation-citations.jsonl"),
    )
    for records, file_role in record_groups:
        _validate_loaded_records(records, file_role=file_role)
    _validate_references(package)
    _validate_reconciliation(package)
