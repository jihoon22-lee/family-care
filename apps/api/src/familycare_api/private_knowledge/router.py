"""Authenticated read-only routes for current private insurance knowledge."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.private_knowledge.query import PrivateKnowledgeQueryService
from familycare_api.private_knowledge.schemas import (
    CurrentKnowledgeResponse,
    KnowledgeContractDetailResponse,
    KnowledgeContractPageResponse,
    PrivateKnowledgeErrorResponse,
)

ScopeDependency = Annotated[HouseholdScope, Depends(resolve_household_scope)]


def get_private_knowledge_query_service(
    scope: ScopeDependency,
) -> PrivateKnowledgeQueryService:
    return PrivateKnowledgeQueryService.from_environment(scope)


ServiceDependency = Annotated[
    PrivateKnowledgeQueryService,
    Depends(get_private_knowledge_query_service),
]

router = APIRouter(prefix="/api/v1/private-knowledge", tags=["private knowledge"])

_COMMON_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": PrivateKnowledgeErrorResponse, "description": "Authentication required"},
    404: {"model": PrivateKnowledgeErrorResponse, "description": "Scoped snapshot not found"},
    409: {"model": PrivateKnowledgeErrorResponse, "description": "Response bound exceeded"},
    422: {"model": PrivateKnowledgeErrorResponse, "description": "Invalid request"},
    503: {"model": PrivateKnowledgeErrorResponse, "description": "Database unavailable"},
}


@router.get(
    "/current",
    response_model=CurrentKnowledgeResponse,
    responses=_COMMON_ERRORS,
)
def get_current_private_knowledge(
    response: Response,
    service: ServiceDependency,
) -> CurrentKnowledgeResponse:
    response.headers["Cache-Control"] = "no-store"
    return service.current()


@router.get(
    "/current/contracts",
    response_model=KnowledgeContractPageResponse,
    responses=_COMMON_ERRORS,
)
def list_current_private_knowledge_contracts(
    response: Response,
    service: ServiceDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after: UUID | None = None,
    family_member_id: UUID | None = None,
) -> KnowledgeContractPageResponse:
    response.headers["Cache-Control"] = "no-store"
    return service.list_contracts(
        limit=limit,
        after=after,
        family_member_id=family_member_id,
    )


@router.get(
    "/current/contracts/{contract_id}",
    response_model=KnowledgeContractDetailResponse,
    responses=_COMMON_ERRORS,
)
def get_current_private_knowledge_contract(
    contract_id: UUID,
    response: Response,
    service: ServiceDependency,
    section_limit: Annotated[int, Query(ge=1, le=50)] = 20,
    section_after: UUID | None = None,
) -> KnowledgeContractDetailResponse:
    response.headers["Cache-Control"] = "no-store"
    return service.get_contract(
        contract_id,
        section_limit=section_limit,
        section_after=section_after,
    )


__all__ = ["get_private_knowledge_query_service", "router"]
