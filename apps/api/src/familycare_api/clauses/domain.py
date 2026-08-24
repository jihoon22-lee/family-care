"""Immutable TermsEdition, Clause, and search result projections."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from familycare_api.clauses.normalization import (
    MAX_EXCERPT_LENGTH,
)
from familycare_api.common.evidence import EvidenceRef
from familycare_api.common.scope import HouseholdScope

ClauseType = Literal[
    "chapter",
    "section",
    "article",
    "paragraph",
    "item",
    "special_terms",
    "definition",
    "appendix",
    "table",
]

_CLAUSE_TYPES = {
    "chapter",
    "section",
    "article",
    "paragraph",
    "item",
    "special_terms",
    "definition",
    "appendix",
    "table",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _require_uuid(value: UUID, *, field: str) -> None:
    if not isinstance(value, UUID) or value.int == 0:
        raise ValueError(f"{field} must be a non-zero UUID")


def _require_text(value: str, *, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")


def _require_hash(value: str, *, field: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 value")


def _require_version(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 32:
        raise ValueError("normalization version must be non-empty bounded text")


def _require_positive_version(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("version must be a positive integer")


def _require_page_range(start: int, end: int) -> None:
    if (
        isinstance(start, bool)
        or not isinstance(start, int)
        or isinstance(end, bool)
        or not isinstance(end, int)
        or start < 1
        or end < start
    ):
        raise ValueError("physical page range must be 1-based and ordered")


@dataclass(frozen=True)
class TermsEdition:
    """One household-scoped version of a synthetic or private terms document."""

    id: UUID
    household_space_id: UUID
    document_version_id: UUID
    insurer_display: str
    insurer_key: str
    product_display: str
    product_key: str
    applicability_start: date | None
    applicability_end: date | None
    content_sha256: str
    normalization_version: str
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None

    def __post_init__(self) -> None:
        _require_uuid(self.id, field="id")
        _require_uuid(self.household_space_id, field="household_space_id")
        _require_uuid(self.document_version_id, field="document_version_id")
        for field, value in (
            ("insurer_display", self.insurer_display),
            ("insurer_key", self.insurer_key),
            ("product_display", self.product_display),
            ("product_key", self.product_key),
        ):
            _require_text(value, field=field)
        _require_hash(self.content_sha256, field="content_sha256")
        _require_version(self.normalization_version)
        _require_positive_version(self.version)
        if (
            self.applicability_start is not None
            and self.applicability_end is not None
            and self.applicability_end < self.applicability_start
        ):
            raise ValueError("applicability dates must be ordered")

    def in_scope(self, scope: HouseholdScope) -> bool:
        """Return whether this projection belongs to the server-owned scope."""

        return self.household_space_id == scope.household_space_id


@dataclass(frozen=True)
class Clause:
    """One normalized Clause node with optional validated Evidence references."""

    id: UUID
    household_space_id: UUID
    terms_edition_id: UUID
    parent_clause_id: UUID | None
    clause_type: ClauseType
    label: str
    normalized_title: str
    normalized_text: str
    physical_page_start: int
    physical_page_end: int
    normalization_version: str
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        _require_uuid(self.id, field="id")
        _require_uuid(self.household_space_id, field="household_space_id")
        _require_uuid(self.terms_edition_id, field="terms_edition_id")
        if self.parent_clause_id is not None:
            _require_uuid(self.parent_clause_id, field="parent_clause_id")
        if self.clause_type not in _CLAUSE_TYPES:
            raise ValueError("unsupported Clause type")
        _require_text(self.label, field="label")
        _require_text(self.normalized_title, field="normalized_title")
        _require_text(self.normalized_text, field="normalized_text")
        _require_page_range(self.physical_page_start, self.physical_page_end)
        _require_version(self.normalization_version)
        _require_positive_version(self.version)
        if not all(isinstance(item, EvidenceRef) for item in self.evidence):
            raise ValueError("evidence must contain EvidenceRef values")

    def in_scope(self, scope: HouseholdScope) -> bool:
        """Return whether this projection belongs to the server-owned scope."""

        return self.household_space_id == scope.household_space_id


@dataclass(frozen=True)
class ClauseSearchFilters:
    """Optional filters applied after the server derives household scope."""

    terms_edition_id: UUID | None = None
    effective_on: date | None = None
    insurer_key: str | None = None
    product_key: str | None = None


@dataclass(frozen=True)
class ClauseSearchHit:
    """Bounded search projection; it never contains the full Clause body."""

    clause_id: UUID
    label: str
    excerpt: str
    terms_edition_id: UUID
    physical_page_start: int
    physical_page_end: int
    evidence: tuple[EvidenceRef, ...]
    relevance: Decimal
    normalization_version: str

    def __post_init__(self) -> None:
        _require_uuid(self.clause_id, field="clause_id")
        _require_uuid(self.terms_edition_id, field="terms_edition_id")
        _require_text(self.label, field="label")
        if not isinstance(self.excerpt, str) or not self.excerpt:
            raise ValueError("excerpt must be non-empty text")
        if len(self.excerpt) > MAX_EXCERPT_LENGTH:
            raise ValueError("excerpt exceeds the maximum length")
        _require_page_range(self.physical_page_start, self.physical_page_end)
        if not isinstance(self.relevance, Decimal):
            raise TypeError("relevance must be Decimal")
        _require_version(self.normalization_version)
        if not all(isinstance(item, EvidenceRef) for item in self.evidence):
            raise ValueError("evidence must contain EvidenceRef values")


__all__ = [
    "Clause",
    "ClauseSearchFilters",
    "ClauseSearchHit",
    "ClauseType",
    "TermsEdition",
]
