"""Household-scoped TermsEdition, Clause hierarchy, and search routes."""

from __future__ import annotations

import os
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from familycare_api.clauses.domain import ClauseSearchFilters
from familycare_api.clauses.errors import ClauseRepositoryUnavailable, InvalidSearchQuery
from familycare_api.clauses.repository import ClauseSearchRepository
from familycare_api.clauses.schemas import (
    ClauseErrorResponse,
    ClauseHierarchyNodeResponse,
    ClauseHierarchyResponse,
    ClauseSearchHitResponse,
    ClauseSearchQuery,
    ClauseSearchResponse,
    TermsEditionResponse,
)
from familycare_api.clauses.search import ClauseSearchService
from familycare_api.clauses.service import ClauseCatalogService
from familycare_api.common.scope import HouseholdScope, resolve_household_scope

ScopeDependency = Annotated[HouseholdScope, Depends(resolve_household_scope)]


def get_clause_catalog_service(scope: ScopeDependency) -> ClauseCatalogService:
    """Construct a request-local catalog service after deriving trusted scope."""

    del scope
    return ClauseCatalogService.from_environment()


def get_clause_search_service(scope: ScopeDependency) -> ClauseSearchService:
    """Construct a request-local search service after deriving trusted scope."""

    del scope
    database_url = os.getenv("FAMILYCARE_DATABASE_URL")
    if not database_url:
        raise ClauseRepositoryUnavailable
    return ClauseSearchService(ClauseSearchRepository(database_url))


CatalogServiceDependency = Annotated[ClauseCatalogService, Depends(get_clause_catalog_service)]
SearchServiceDependency = Annotated[ClauseSearchService, Depends(get_clause_search_service)]

router = APIRouter(prefix="/api/v1", tags=["clause search"])

_COMMON_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ClauseErrorResponse, "description": "Authentication required"},
    404: {"model": ClauseErrorResponse, "description": "Scoped terms edition not found"},
    409: {"model": ClauseErrorResponse, "description": "Search index version conflict"},
    422: {"model": ClauseErrorResponse, "description": "Sanitized invalid request"},
    503: {"model": ClauseErrorResponse, "description": "Local database unavailable"},
}


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.get(
    "/terms-editions",
    response_model=list[TermsEditionResponse],
    responses=_COMMON_ERRORS,
)
def list_terms_editions(
    response: Response,
    scope: ScopeDependency,
    service: CatalogServiceDependency,
) -> list[TermsEditionResponse]:
    _no_store(response)
    return [
        TermsEditionResponse.from_domain(edition)
        for edition in service.list_terms_editions(scope)
        if edition.in_scope(scope)
    ]


@router.get(
    "/terms-editions/{terms_edition_id}/clauses",
    response_model=ClauseHierarchyResponse,
    responses=_COMMON_ERRORS,
)
def get_clause_hierarchy(
    terms_edition_id: UUID,
    response: Response,
    scope: ScopeDependency,
    service: CatalogServiceDependency,
) -> ClauseHierarchyResponse:
    _no_store(response)
    clauses = service.get_clause_hierarchy(scope, terms_edition_id)
    return ClauseHierarchyResponse(
        terms_edition_id=terms_edition_id,
        clauses=tuple(
            ClauseHierarchyNodeResponse.from_domain(clause)
            for clause in clauses
            if clause.in_scope(scope) and clause.terms_edition_id == terms_edition_id
        ),
    )


@router.post(
    "/clauses/search",
    response_model=ClauseSearchResponse,
    responses=_COMMON_ERRORS,
)
def search_clauses(
    request: ClauseSearchQuery,
    http_request: Request,
    response: Response,
    scope: ScopeDependency,
    service: SearchServiceDependency,
) -> ClauseSearchResponse:
    _no_store(response)
    if http_request.query_params:
        raise InvalidSearchQuery
    hits = service.search(
        scope,
        request.q,
        ClauseSearchFilters(
            terms_edition_id=request.terms_edition_id,
            effective_on=request.effective_on,
            insurer_key=request.insurer_key,
            product_key=request.product_key,
        ),
        limit=request.limit,
    )
    return ClauseSearchResponse(
        schema_version="1",
        normalization_version="unicode-nfc-v1",
        query_matched_count=len(hits),
        hits=tuple(ClauseSearchHitResponse.from_domain(hit) for hit in hits),
    )


__all__ = [
    "CatalogServiceDependency",
    "ScopeDependency",
    "SearchServiceDependency",
    "get_clause_catalog_service",
    "get_clause_search_service",
    "router",
]
