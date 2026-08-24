"""Deterministic query validation around PostgreSQL Clause search."""

from __future__ import annotations

from typing import Protocol

from familycare_api.clauses.domain import ClauseSearchFilters, ClauseSearchHit
from familycare_api.clauses.errors import InvalidSearchQuery, SearchIndexVersionMismatch
from familycare_api.clauses.normalization import (
    MAX_SEARCH_QUERY_LENGTH,
    NORMALIZATION_VERSION,
    normalize_search_query,
)
from familycare_api.common.scope import HouseholdScope


class ClauseSearchReader(Protocol):
    def search(
        self,
        scope: HouseholdScope,
        normalized_query: str,
        filters: ClauseSearchFilters,
        *,
        limit: int,
    ) -> tuple[ClauseSearchHit, ...]: ...


class ClauseSearchService:
    """Validate input without logging it, then enforce the index version."""

    def __init__(self, repository: ClauseSearchReader) -> None:
        self.repository = repository

    def search(
        self,
        scope: HouseholdScope,
        query: str,
        filters: ClauseSearchFilters,
        *,
        limit: int = 20,
    ) -> tuple[ClauseSearchHit, ...]:
        try:
            normalized = normalize_search_query(query)
        except TypeError:
            raise InvalidSearchQuery from None
        if (
            not normalized
            or len(normalized) > MAX_SEARCH_QUERY_LENGTH
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 50
        ):
            raise InvalidSearchQuery
        hits = self.repository.search(
            scope,
            normalized,
            filters,
            limit=limit,
        )
        if any(hit.normalization_version != NORMALIZATION_VERSION for hit in hits):
            raise SearchIndexVersionMismatch
        return hits


__all__ = ["ClauseSearchReader", "ClauseSearchService"]
