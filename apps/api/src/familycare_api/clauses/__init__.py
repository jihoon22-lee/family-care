"""Household-scoped Clause normalization and domain projections."""

from familycare_api.clauses.domain import (
    Clause,
    ClauseSearchFilters,
    ClauseSearchHit,
    ClauseType,
    TermsEdition,
)
from familycare_api.clauses.normalization import (
    MAX_EXCERPT_LENGTH,
    MAX_SEARCH_QUERY_LENGTH,
    NORMALIZATION_VERSION,
    bounded_excerpt,
    normalize_clause_text,
    normalize_search_query,
)

__all__ = [
    "MAX_EXCERPT_LENGTH",
    "MAX_SEARCH_QUERY_LENGTH",
    "NORMALIZATION_VERSION",
    "Clause",
    "ClauseSearchFilters",
    "ClauseSearchHit",
    "ClauseType",
    "TermsEdition",
    "bounded_excerpt",
    "normalize_clause_text",
    "normalize_search_query",
]
