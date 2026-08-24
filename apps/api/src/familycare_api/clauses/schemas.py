"""Strict HTTP adapters for the household-scoped Clause search boundary."""

from __future__ import annotations

from datetime import date
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from familycare_api.clauses.domain import Clause, ClauseSearchHit, TermsEdition
from familycare_api.clauses.normalization import (
    MAX_EXCERPT_LENGTH,
    bounded_excerpt,
)
from familycare_api.common.evidence import EvidenceRef

_STRICT = ConfigDict(extra="forbid", frozen=True)
_SHA256_PATTERN = r"^[0-9a-f]{64}$"

ClauseApiErrorCode = Literal[
    "AUTHENTICATION_REQUIRED",
    "CLAUSE_NOT_FOUND",
    "EVIDENCE_INVALID",
    "INVALID_REQUEST",
    "POLICY_STATE_CONFLICT",
    "RESOURCE_LIMIT_EXCEEDED",
    "SEARCH_INDEX_VERSION_MISMATCH",
    "TERMS_EDITION_NOT_FOUND",
    "VERSION_CONFLICT",
]


class ClauseErrorResponse(BaseModel):
    """Fixed error envelope; request values and Clause text are never fields."""

    model_config = _STRICT

    error_code: ClauseApiErrorCode
    message: str = Field(min_length=1, max_length=160)
    fields: list[str] | None = None


class ClauseSearchQuery(BaseModel):
    """Bounded JSON body for POST search; the phrase is never a URL field."""

    model_config = _STRICT

    q: str = Field(min_length=1, max_length=160)
    terms_edition_id: UUID | None = None
    effective_on: date | None = None
    insurer_key: str | None = Field(default=None, min_length=1, max_length=160)
    product_key: str | None = Field(default=None, min_length=1, max_length=200)
    limit: int = Field(default=20, ge=1, le=50)


class ClauseEvidenceResponse(BaseModel):
    """Public Evidence projection with a 1-based physical PDF page."""

    model_config = _STRICT

    evidence_id: UUID
    document_version_id: UUID
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    page_number: int = Field(ge=1)
    bbox: tuple[float, float, float, float] | None

    @classmethod
    def from_domain(cls, evidence: EvidenceRef) -> ClauseEvidenceResponse:
        bbox = (
            cast(tuple[float, float, float, float], tuple(float(value) for value in evidence.bbox))
            if evidence.bbox is not None
            else None
        )
        return cls(
            evidence_id=evidence.evidence_id,
            document_version_id=evidence.document_version_id,
            content_sha256=evidence.content_sha256,
            page_number=evidence.physical_page,
            bbox=bbox,
        )


class TermsEditionResponse(BaseModel):
    """Terms metadata that can be shown without exposing household scope."""

    model_config = _STRICT

    id: UUID
    document_version_id: UUID
    insurer_display: str = Field(min_length=1, max_length=160)
    insurer_key: str = Field(min_length=1, max_length=160)
    product_display: str = Field(min_length=1, max_length=200)
    product_key: str = Field(min_length=1, max_length=200)
    applicability_start: date | None
    applicability_end: date | None
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    normalization_version: str = Field(min_length=1, max_length=32)
    version: int = Field(ge=1)

    @classmethod
    def from_domain(cls, edition: TermsEdition) -> TermsEditionResponse:
        return cls(
            id=edition.id,
            document_version_id=edition.document_version_id,
            insurer_display=edition.insurer_display,
            insurer_key=edition.insurer_key,
            product_display=edition.product_display,
            product_key=edition.product_key,
            applicability_start=edition.applicability_start,
            applicability_end=edition.applicability_end,
            content_sha256=edition.content_sha256,
            normalization_version=edition.normalization_version,
            version=edition.version,
        )


class ClauseHierarchyNodeResponse(BaseModel):
    """One Clause hierarchy node with a bounded excerpt only."""

    model_config = _STRICT

    clause_id: UUID
    parent_clause_id: UUID | None
    clause_type: str = Field(min_length=1, max_length=32)
    label: str = Field(min_length=1, max_length=160)
    excerpt: str = Field(min_length=1, max_length=MAX_EXCERPT_LENGTH)
    physical_page_start: int = Field(ge=1)
    physical_page_end: int = Field(ge=1)
    evidence: tuple[ClauseEvidenceResponse, ...] = Field(min_length=1, max_length=16)
    normalization_version: str = Field(min_length=1, max_length=32)

    @classmethod
    def from_domain(cls, clause: Clause) -> ClauseHierarchyNodeResponse:
        return cls(
            clause_id=clause.id,
            parent_clause_id=clause.parent_clause_id,
            clause_type=clause.clause_type,
            label=clause.label,
            excerpt=bounded_excerpt(clause.normalized_text),
            physical_page_start=clause.physical_page_start,
            physical_page_end=clause.physical_page_end,
            evidence=tuple(ClauseEvidenceResponse.from_domain(item) for item in clause.evidence),
            normalization_version=clause.normalization_version,
        )


class ClauseHierarchyResponse(BaseModel):
    model_config = _STRICT

    terms_edition_id: UUID
    clauses: tuple[ClauseHierarchyNodeResponse, ...] = Field(max_length=5000)


class ClauseSearchHitResponse(BaseModel):
    """Search result projection; normalized Clause body is intentionally absent."""

    model_config = _STRICT

    clause_id: UUID
    label: str = Field(min_length=1, max_length=160)
    excerpt: str = Field(min_length=1, max_length=MAX_EXCERPT_LENGTH)
    terms_edition_id: UUID
    physical_page_start: int = Field(ge=1)
    physical_page_end: int = Field(ge=1)
    evidence: tuple[ClauseEvidenceResponse, ...] = Field(min_length=1, max_length=16)
    normalization_version: str = Field(min_length=1, max_length=32)
    relevance: float = Field(ge=0)

    @classmethod
    def from_domain(cls, hit: ClauseSearchHit) -> ClauseSearchHitResponse:
        return cls(
            clause_id=hit.clause_id,
            label=hit.label,
            excerpt=hit.excerpt,
            terms_edition_id=hit.terms_edition_id,
            physical_page_start=hit.physical_page_start,
            physical_page_end=hit.physical_page_end,
            evidence=tuple(ClauseEvidenceResponse.from_domain(item) for item in hit.evidence),
            normalization_version=hit.normalization_version,
            relevance=float(hit.relevance),
        )


class ClauseSearchResponse(BaseModel):
    model_config = _STRICT

    schema_version: Literal["1"]
    normalization_version: Literal["unicode-nfc-v1"]
    query_matched_count: int = Field(ge=0, le=50)
    hits: tuple[ClauseSearchHitResponse, ...] = Field(max_length=50)


__all__ = [
    "ClauseApiErrorCode",
    "ClauseErrorResponse",
    "ClauseHierarchyNodeResponse",
    "ClauseHierarchyResponse",
    "ClauseSearchHitResponse",
    "ClauseSearchQuery",
    "ClauseSearchResponse",
    "ClauseEvidenceResponse",
    "TermsEditionResponse",
]
