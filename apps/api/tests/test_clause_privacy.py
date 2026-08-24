"""Privacy and stale-index boundaries for the Clause search service.

These tests use synthetic markers only.  They deliberately exercise the
transport-neutral service boundary so that query text, Clause text, and source
paths cannot become public error values or log fields.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from decimal import Decimal
from uuid import UUID

import pytest
from familycare_api.clauses.domain import ClauseSearchFilters, ClauseSearchHit
from familycare_api.clauses.errors import (
    ClauseEvidenceInvalid,
    InvalidSearchQuery,
    SearchIndexVersionMismatch,
)
from familycare_api.clauses.normalization import NORMALIZATION_VERSION, normalize_search_query
from familycare_api.clauses.repository import SEARCH_SQL
from familycare_api.clauses.search import ClauseSearchService
from familycare_api.common.evidence import EvidenceRef
from familycare_api.common.scope import HouseholdScope

SCOPE = HouseholdScope(UUID("00000000-0000-4000-8000-000000000801"))
EDITION_ID = UUID("00000000-0000-4000-8000-000000000802")
CLAUSE_ID = UUID("00000000-0000-4000-8000-000000000803")
EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000804")
DOCUMENT_VERSION_ID = UUID("00000000-0000-4000-8000-000000000805")
EXTRACTION_ID = UUID("00000000-0000-4000-8000-000000000806")

PRIVATE_QUERY_MARKER = "synthetic-private-search-marker"
PRIVATE_TEXT_MARKER = "synthetic-private-clause-body-marker"
PRIVATE_PATH_MARKER = "/synthetic/private/terms-document.pdf"


def _evidence() -> EvidenceRef:
    return EvidenceRef(
        evidence_id=EVIDENCE_ID,
        document_version_id=DOCUMENT_VERSION_ID,
        extraction_id=EXTRACTION_ID,
        content_sha256="a" * 64,
        physical_page=3,
        bbox=None,
        review_state="USER_CONFIRMED",
    )


def _hit(*, evidence: tuple[EvidenceRef, ...] = (_evidence(),)) -> ClauseSearchHit:
    return ClauseSearchHit(
        clause_id=CLAUSE_ID,
        label="Sample Terms Article",
        excerpt=PRIVATE_TEXT_MARKER,
        terms_edition_id=EDITION_ID,
        physical_page_start=3,
        physical_page_end=3,
        evidence=evidence,
        relevance=Decimal("0.625"),
        normalization_version=NORMALIZATION_VERSION,
    )


class _StaticSearchRepository:
    def __init__(self, hits: tuple[ClauseSearchHit, ...]) -> None:
        self.hits = hits
        self.calls: list[tuple[HouseholdScope, str, ClauseSearchFilters, int]] = []

    def search(
        self,
        scope: HouseholdScope,
        normalized_query: str,
        filters: ClauseSearchFilters,
        *,
        limit: int,
    ) -> tuple[ClauseSearchHit, ...]:
        self.calls.append((scope, normalized_query, filters, limit))
        return self.hits[:limit]


def test_missing_evidence_is_surfaced_instead_of_returning_a_search_hit() -> None:
    repository = _StaticSearchRepository((_hit(evidence=()),))
    service = ClauseSearchService(repository)

    with pytest.raises(ClauseEvidenceInvalid) as raised:
        service.search(SCOPE, PRIVATE_QUERY_MARKER, ClauseSearchFilters())

    assert raised.value.error_code == "EVIDENCE_INVALID"
    assert raised.value.public_message == "clause evidence is invalid"
    assert repository.calls == [
        (SCOPE, normalize_search_query(PRIVATE_QUERY_MARKER), ClauseSearchFilters(), 20),
    ]


def test_stale_normalization_uses_a_stable_value_free_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    stale_hit = replace(_hit(), normalization_version="unicode-nfc-v0")
    service = ClauseSearchService(_StaticSearchRepository((stale_hit,)))

    with pytest.raises(SearchIndexVersionMismatch) as raised:
        service.search(
            SCOPE,
            f"{PRIVATE_QUERY_MARKER} {PRIVATE_PATH_MARKER}",
            ClauseSearchFilters(),
        )

    assert raised.value.error_code == "SEARCH_INDEX_VERSION_MISMATCH"
    assert raised.value.public_message == "search index version mismatch"
    serialized = " ".join(
        (str(raised.value), repr(raised.value), raised.value.public_message, caplog.text)
    ).lower()
    assert PRIVATE_QUERY_MARKER not in serialized
    assert PRIVATE_PATH_MARKER.lower() not in serialized
    assert PRIVATE_TEXT_MARKER not in serialized


def test_invalid_query_does_not_echo_query_text_or_path_in_errors_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    repository = _StaticSearchRepository(())
    service = ClauseSearchService(repository)
    overlong_query = f"{PRIVATE_QUERY_MARKER} {PRIVATE_PATH_MARKER} " + ("x" * 180)

    with pytest.raises(InvalidSearchQuery) as raised:
        service.search(SCOPE, overlong_query, ClauseSearchFilters())

    assert raised.value.error_code == "INVALID_REQUEST"
    assert raised.value.public_message == "search request is invalid"
    assert repository.calls == []
    serialized = " ".join(
        (str(raised.value), repr(raised.value), raised.value.public_message, caplog.text)
    ).lower()
    assert PRIVATE_QUERY_MARKER not in serialized
    assert PRIVATE_PATH_MARKER.lower() not in serialized
    assert PRIVATE_TEXT_MARKER not in serialized


def test_search_sql_returns_only_a_bounded_excerpt_and_never_a_private_field() -> None:
    normalized_sql = SEARCH_SQL.lower()

    assert "left(c.normalized_text, 320) as excerpt" in normalized_sql
    assert "%(normalized_query)s" in SEARCH_SQL
    assert "raw_query" not in normalized_sql
    assert "document_text" not in normalized_sql
    assert "source_path" not in normalized_sql
    assert PRIVATE_QUERY_MARKER not in SEARCH_SQL
    assert PRIVATE_TEXT_MARKER not in SEARCH_SQL
    assert PRIVATE_PATH_MARKER not in SEARCH_SQL
