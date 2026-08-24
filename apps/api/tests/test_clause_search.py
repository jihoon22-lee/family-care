"""Focused unit tests for deterministic, household-scoped Clause search."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from familycare_api.clauses.domain import ClauseSearchFilters, ClauseSearchHit
from familycare_api.clauses.errors import (
    InvalidSearchQuery,
    SearchIndexVersionMismatch,
)
from familycare_api.clauses.normalization import NORMALIZATION_VERSION
from familycare_api.clauses.repository import SEARCH_SQL
from familycare_api.clauses.search import ClauseSearchService
from familycare_api.common.scope import HouseholdScope

SCOPE = HouseholdScope(UUID("00000000-0000-4000-8000-000000000301"))
EDITION_ID = UUID("00000000-0000-4000-8000-000000000302")
CLAUSE_ID = UUID("00000000-0000-4000-8000-000000000303")


def _hit() -> ClauseSearchHit:
    return ClauseSearchHit(
        clause_id=CLAUSE_ID,
        label="제3조 (합성 입원 정의)",
        excerpt="합성 입원은 표본 의료기관에서 이어지는 관찰을 뜻합니다.",
        terms_edition_id=EDITION_ID,
        physical_page_start=3,
        physical_page_end=3,
        evidence=(),
        relevance=Decimal("0.625"),
        normalization_version=NORMALIZATION_VERSION,
    )


class RecordingSearchRepository:
    def __init__(self, hits: tuple[ClauseSearchHit, ...] = ()) -> None:
        self.hits = hits
        self.calls: list[dict[str, object]] = []

    def search(
        self,
        scope: HouseholdScope,
        normalized_query: str,
        filters: ClauseSearchFilters,
        *,
        limit: int,
    ) -> tuple[ClauseSearchHit, ...]:
        self.calls.append(
            {
                "scope": scope,
                "normalized_query": normalized_query,
                "filters": filters,
                "limit": limit,
            }
        )
        return self.hits[:limit]


def test_service_normalizes_query_and_preserves_server_scope_and_filters() -> None:
    repository = RecordingSearchRepository((_hit(),))
    service = ClauseSearchService(repository)
    filters = ClauseSearchFilters(
        terms_edition_id=EDITION_ID,
        effective_on=date(2026, 1, 1),
        insurer_key="sample-insurer",
        product_key="sample-policy",
    )

    result = service.search(SCOPE, "  입원,\n 의료비!! ", filters, limit=7)

    assert result == (_hit(),)
    assert repository.calls == [
        {
            "scope": SCOPE,
            "normalized_query": "입원 의료비",
            "filters": filters,
            "limit": 7,
        }
    ]


@pytest.mark.parametrize("query", ["", " \n\t ", "!?!", "가" * 161])
def test_service_rejects_empty_or_overlong_queries_without_repository_call(query: str) -> None:
    repository = RecordingSearchRepository()
    service = ClauseSearchService(repository)

    with pytest.raises(InvalidSearchQuery):
        service.search(SCOPE, query, ClauseSearchFilters())

    assert repository.calls == []


@pytest.mark.parametrize("limit", [0, 51, -1, True])
def test_service_rejects_invalid_limits(limit: int) -> None:
    repository = RecordingSearchRepository()
    service = ClauseSearchService(repository)

    with pytest.raises(InvalidSearchQuery):
        service.search(SCOPE, "입원", ClauseSearchFilters(), limit=limit)

    assert repository.calls == []


def test_service_fails_closed_on_stale_normalization_version() -> None:
    repository = RecordingSearchRepository(
        (replace(_hit(), normalization_version="unicode-nfc-v0"),)
    )
    service = ClauseSearchService(repository)

    with pytest.raises(SearchIndexVersionMismatch):
        service.search(SCOPE, "입원", ClauseSearchFilters())


def test_search_sql_uses_bound_parameters_and_deterministic_tie_breakers() -> None:
    assert "%(household_space_id)s" in SEARCH_SQL
    assert "%(normalized_query)s" in SEARCH_SQL
    assert "%(terms_edition_id)s" in SEARCH_SQL
    assert "%(effective_on)s" in SEARCH_SQL
    assert "%(insurer_key)s" in SEARCH_SQL
    assert "%(product_key)s" in SEARCH_SQL
    assert "%(limit)s" in SEARCH_SQL
    assert "plainto_tsquery('simple', %(normalized_query)s)" in SEARCH_SQL
    assert "ts_rank_cd" in SEARCH_SQL
    assert "similarity" in SEARCH_SQL
    assert "c.household_space_id = %(household_space_id)s" in SEARCH_SQL
    assert "c.deleted_at IS NULL" in SEARCH_SQL
    assert "t.deleted_at IS NULL" in SEARCH_SQL
    assert "ORDER BY relevance DESC, title_similarity DESC" in SEARCH_SQL
    assert "physical_page_start, clause_id" in SEARCH_SQL
    assert "raw_query" not in SEARCH_SQL.lower()
