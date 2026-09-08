"""Lossless, non-executable adaptation of one scoped sol-v2 legacy section.

Legacy page/hash/index citations are retained as historical locations. They do not
supply original extraction nodes, offsets or independently verified meanings, so
this adapter always requires original-source reprocessing. Existing publications
are neither read nor written. A caller must obtain scoped row identities itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from familycare_api.private_knowledge.models import (
    CitationRecord,
    ClauseRecord,
    SemanticFactRecord,
    SemanticReviewRecord,
    TermsSectionRecord,
)
from familycare_api.private_knowledge.package import MAX_JSONL_LINE_BYTES, SCHEMA_VERSION
from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticSerializationError

MAX_SECTION_CLAUSES = 4096
MAX_SECTION_FACTS = 128


class LegacyAdapterError(ValueError):
    """Fixed errors never include historical text, paths or identifiers."""

    def __init__(self, code: str = "LEGACY_RECORD_INVALID") -> None:
        if code not in {
            "LEGACY_RECORD_INVALID",
            "LEGACY_SCOPE_MISMATCH",
            "LEGACY_IDENTITY_INVALID",
            "LEGACY_SCHEMA_UNSUPPORTED",
            "LEGACY_SECTION_MISMATCH",
            "LEGACY_FACT_IDENTITY_MISMATCH",
            "LEGACY_CLAUSE_IDENTITY_MISMATCH",
            "LEGACY_ADAPTER_LIMIT_EXCEEDED",
        }:
            code = "LEGACY_RECORD_INVALID"
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class LegacyReviewContext:
    household_space_id: UUID
    import_run_id: UUID
    section_id: UUID
    review_id: UUID
    package_schema_version: Literal["private-analysis-package.sol-v2"] = (
        "private-analysis-package.sol-v2"
    )


@dataclass(frozen=True, slots=True, repr=False)
class LegacyClauseRow:
    household_space_id: UUID
    import_run_id: UUID
    section_id: UUID
    clause_id: UUID
    record: ClauseRecord


@dataclass(frozen=True, slots=True, repr=False)
class LegacyFactIdentity:
    household_space_id: UUID
    import_run_id: UUID
    section_id: UUID
    fact_id: UUID
    source_fact_id: str


def _record_json[Record: BaseModel](value: Record, model: type[Record]) -> str:
    if not isinstance(value, model):
        raise LegacyAdapterError()
    try:
        encoded = json.dumps(
            value.model_dump(mode="json", warnings="error"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        if len(encoded.encode("utf-8")) > MAX_JSONL_LINE_BYTES:
            raise LegacyAdapterError("LEGACY_ADAPTER_LIMIT_EXCEEDED")
        model.model_validate_json(encoded, strict=True)
    except LegacyAdapterError:
        raise
    except ValidationError, PydanticSerializationError, TypeError, ValueError, RecursionError:
        raise LegacyAdapterError() from None
    return encoded


def _restore[Record: BaseModel](encoded: str, model: type[Record]) -> Record:
    try:
        return model.model_validate_json(encoded, strict=True)
    except ValidationError, TypeError, ValueError:
        raise LegacyAdapterError() from None


@dataclass(frozen=True, slots=True, repr=False)
class LegacyCitation:
    clause_index: int
    physical_page_start: int
    physical_page_end: int
    source_text_sha256: str
    indexed_clause_id: UUID | None
    index_status: Literal["INDEX_MATCHED", "CLAUSE_MISSING", "INDEX_MISMATCH"]


@dataclass(frozen=True, slots=True, repr=False)
class LegacyClause:
    clause_id: UUID
    record_json: str

    def restore_record(self) -> ClauseRecord:
        return _restore(self.record_json, ClauseRecord)


@dataclass(frozen=True, slots=True, repr=False)
class LegacyFact:
    fact_id: UUID | None
    source_fact_id: str
    record_json: str
    citations: tuple[LegacyCitation, ...]
    reason_codes: tuple[str, ...]

    def restore_record(self) -> SemanticFactRecord:
        return _restore(self.record_json, SemanticFactRecord)


@dataclass(frozen=True, slots=True)
class LegacyCounts:
    fact_count: int
    clause_count: int
    citation_count: int
    unresolved_citation_count: int
    missing_fact_identity_count: int


@dataclass(frozen=True, slots=True, repr=False)
class LegacyAdaptation:
    context: LegacyReviewContext
    section_record_json: str
    review_record_json: str
    clauses: tuple[LegacyClause, ...]
    facts: tuple[LegacyFact, ...]
    summary_citations: tuple[LegacyCitation, ...]
    counts: LegacyCounts
    reason_codes: tuple[str, ...]
    status: Literal["REPROCESSING_REQUIRED"] = field(default="REPROCESSING_REQUIRED", init=False)
    executable_rule: Literal[False] = field(default=False, init=False)

    def restore_section(self) -> TermsSectionRecord:
        return _restore(self.section_record_json, TermsSectionRecord)

    def restore_review(self) -> SemanticReviewRecord:
        return _restore(self.review_record_json, SemanticReviewRecord)


def _identity(value: UUID) -> None:
    if not isinstance(value, UUID) or value.int == 0:
        raise LegacyAdapterError("LEGACY_IDENTITY_INVALID")


def _scope(context: LegacyReviewContext, row: LegacyClauseRow | LegacyFactIdentity) -> None:
    for key in ("household_space_id", "import_run_id", "section_id"):
        value = getattr(row, key)
        _identity(value)
        if value != getattr(context, key):
            raise LegacyAdapterError("LEGACY_SCOPE_MISMATCH")


def _citation(
    value: CitationRecord, clauses: dict[int, tuple[UUID, ClauseRecord]]
) -> LegacyCitation:
    found = clauses.get(value.clause_index)
    status: Literal["INDEX_MATCHED", "CLAUSE_MISSING", "INDEX_MISMATCH"] = "CLAUSE_MISSING"
    if found is not None:
        clause = found[1]
        matched = (
            value.source_text_sha256 == clause.source_text_sha256
            and clause.physical_page_start
            <= value.physical_page_start
            <= value.physical_page_end
            <= clause.physical_page_end
        )
        status = "INDEX_MATCHED" if matched else "INDEX_MISMATCH"
    return LegacyCitation(
        value.clause_index,
        value.physical_page_start,
        value.physical_page_end,
        value.source_text_sha256,
        found[0] if found is not None else None,
        status,
    )


def adapt_legacy_review(
    context: LegacyReviewContext,
    *,
    section: TermsSectionRecord,
    review: SemanticReviewRecord,
    clauses: tuple[LegacyClauseRow, ...] = (),
    fact_ids: tuple[LegacyFactIdentity, ...] = (),
) -> LegacyAdaptation:
    """Retain one legacy review and explicit row identities without minting authority.

    Missing historical clause/fact mappings remain visible. Extra or duplicate
    identities and rows from another scope are rejected rather than silently lost.
    All mutable model inputs are revalidated and copied to immutable JSON strings.
    """
    if not isinstance(context, LegacyReviewContext):
        raise LegacyAdapterError("LEGACY_IDENTITY_INVALID")
    for identity in (
        context.household_space_id,
        context.import_run_id,
        context.section_id,
        context.review_id,
    ):
        _identity(identity)
    if context.package_schema_version != SCHEMA_VERSION:
        raise LegacyAdapterError("LEGACY_SCHEMA_UNSUPPORTED")
    if not isinstance(clauses, tuple) or not isinstance(fact_ids, tuple):
        raise LegacyAdapterError()
    if len(clauses) > MAX_SECTION_CLAUSES or len(fact_ids) > MAX_SECTION_FACTS:
        raise LegacyAdapterError("LEGACY_ADAPTER_LIMIT_EXCEEDED")
    section_json = _record_json(section, TermsSectionRecord)
    review_json = _record_json(review, SemanticReviewRecord)
    section = _restore(section_json, TermsSectionRecord)
    review = _restore(review_json, SemanticReviewRecord)
    source_key = (section.terms_alias, section.section_id)
    if (review.terms_alias, review.section_id) != source_key or (
        review.section_physical_page != section.physical_page
    ):
        raise LegacyAdapterError("LEGACY_SECTION_MISMATCH")
    source_fact_ids = [fact.fact_id for fact in review.facts]
    if len(source_fact_ids) != len(set(source_fact_ids)):
        raise LegacyAdapterError("LEGACY_FACT_IDENTITY_MISMATCH")
    clause_index: dict[int, tuple[UUID, ClauseRecord]] = {}
    retained_clauses = []
    clause_ids = set()
    for row in clauses:
        if not isinstance(row, LegacyClauseRow):
            raise LegacyAdapterError()
        _scope(context, row)
        _identity(row.clause_id)
        encoded = _record_json(row.record, ClauseRecord)
        clause = _restore(encoded, ClauseRecord)
        if (clause.terms_alias, clause.section_id) != source_key:
            raise LegacyAdapterError("LEGACY_SECTION_MISMATCH")
        if clause.clause_index in clause_index or row.clause_id in clause_ids:
            raise LegacyAdapterError("LEGACY_CLAUSE_IDENTITY_MISMATCH")
        clause_ids.add(row.clause_id)
        clause_index[clause.clause_index] = row.clause_id, clause
        retained_clauses.append(LegacyClause(row.clause_id, encoded))
    mapped_facts: dict[str, UUID] = {}
    database_fact_ids = set()
    for identity_row in fact_ids:
        if not isinstance(identity_row, LegacyFactIdentity):
            raise LegacyAdapterError()
        _scope(context, identity_row)
        _identity(identity_row.fact_id)
        if (
            identity_row.source_fact_id not in source_fact_ids
            or identity_row.source_fact_id in mapped_facts
            or identity_row.fact_id in database_fact_ids
        ):
            raise LegacyAdapterError("LEGACY_FACT_IDENTITY_MISMATCH")
        mapped_facts[identity_row.source_fact_id] = identity_row.fact_id
        database_fact_ids.add(identity_row.fact_id)
    facts = []
    for fact in review.facts:
        citations = tuple(_citation(citation, clause_index) for citation in fact.citations)
        reasons = ["LEGACY_ORIGINAL_REPROCESSING_REQUIRED"]
        if fact.fact_id not in mapped_facts:
            reasons.append("LEGACY_FACT_ID_UNAVAILABLE")
        if fact.unresolved_reference:
            reasons.append("LEGACY_REFERENCE_UNRESOLVED")
        if fact.review_state == "NEEDS_REFERENCE_REVIEW":
            reasons.append("LEGACY_REFERENCE_REVIEW_PENDING")
        if any(c.index_status != "INDEX_MATCHED" for c in citations):
            reasons.append("LEGACY_CITATION_UNRESOLVED")
        facts.append(
            LegacyFact(
                mapped_facts.get(fact.fact_id),
                fact.fact_id,
                _record_json(fact, SemanticFactRecord),
                citations,
                tuple(reasons),
            )
        )
    summary = tuple(_citation(citation, clause_index) for citation in review.summary_citations)
    all_citations = (*summary, *(c for fact in facts for c in fact.citations))
    counts = LegacyCounts(
        len(facts),
        len(retained_clauses),
        len(all_citations),
        sum(c.index_status != "INDEX_MATCHED" for c in all_citations),
        sum(fact.fact_id is None for fact in facts),
    )
    reasons = ["LEGACY_ORIGINAL_REPROCESSING_REQUIRED"]
    if section.legacy_review_only or review.legacy_review_only:
        reasons.append("LEGACY_REVIEW_ONLY")
    if review.missing_categories:
        reasons.append("LEGACY_CATEGORIES_MISSING")
    if counts.unresolved_citation_count:
        reasons.append("LEGACY_CITATION_UNRESOLVED")
    if section.semantic_fact_count != len(facts) or (
        section.source_clause_count != review.source_clause_count
        or review.classified_clause_count + review.unclassified_clause_count
        != review.source_clause_count
    ):
        reasons.append("LEGACY_COUNT_MISMATCH")
    if len(clauses) < review.source_clause_count:
        reasons.append("LEGACY_CLAUSE_INDEX_PARTIAL")
    return LegacyAdaptation(
        context,
        section_json,
        review_json,
        tuple(retained_clauses),
        tuple(facts),
        summary,
        counts,
        tuple(reasons),
    )
