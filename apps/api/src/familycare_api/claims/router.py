"""Versioned no-store routes for manual claim tracking."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from familycare_api.claims.domain import ClaimStatus
from familycare_api.claims.schemas import (
    ChecklistUpdateRequest,
    ClaimCaseListResponse,
    ClaimCaseResponse,
    ClaimCreateRequest,
    ClaimTransitionRequest,
    ClaimUpdateRequest,
    ExpectedVersionRequest,
)
from familycare_api.claims.service import ClaimService
from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.decisions.schemas import DecisionErrorResponse

ScopeDependency = Annotated[HouseholdScope, Depends(resolve_household_scope)]


def get_claim_service(scope: ScopeDependency) -> ClaimService:
    return ClaimService.from_environment(scope)


ServiceDependency = Annotated[ClaimService, Depends(get_claim_service)]
medical_event_claim_router = APIRouter(prefix="/api/v1/medical-events", tags=["claim workflow"])
router = APIRouter(prefix="/api/v1/claims", tags=["claim workflow"])

_COMMON_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": DecisionErrorResponse, "description": "Authentication required"},
    404: {"model": DecisionErrorResponse, "description": "Scoped record not found"},
    409: {"model": DecisionErrorResponse, "description": "Claim state or version conflict"},
    422: {"model": DecisionErrorResponse, "description": "Sanitized invalid request"},
    503: {"model": DecisionErrorResponse, "description": "Local database unavailable"},
}


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@medical_event_claim_router.post(
    "/{event_id}/claims",
    response_model=ClaimCaseResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_COMMON_ERRORS,
)
def create_claim_case(
    event_id: UUID,
    request: ClaimCreateRequest,
    response: Response,
    service: ServiceDependency,
) -> object:
    _no_store(response)
    return service.create_claim_case(event_id, request)


@router.get("", response_model=ClaimCaseListResponse, responses=_COMMON_ERRORS)
def list_claim_cases(
    response: Response,
    service: ServiceDependency,
    event_id: UUID | None = None,
    claim_status: Annotated[ClaimStatus | None, Query(alias="status")] = None,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> object:
    _no_store(response)
    return service.list_claim_cases(
        event_id=event_id,
        status=claim_status,
        cursor=cursor,
        limit=limit,
    )


@router.get("/trash", response_model=ClaimCaseListResponse, responses=_COMMON_ERRORS)
def list_deleted_claim_cases(response: Response, service: ServiceDependency) -> object:
    _no_store(response)
    return service.list_claim_cases(deleted_only=True, limit=100)


@router.get("/{claim_id}", response_model=ClaimCaseResponse, responses=_COMMON_ERRORS)
def get_claim_case(claim_id: UUID, response: Response, service: ServiceDependency) -> object:
    _no_store(response)
    return service.get_claim_case(claim_id)


@router.patch("/{claim_id}", response_model=ClaimCaseResponse, responses=_COMMON_ERRORS)
def update_claim_case(
    claim_id: UUID,
    request: ClaimUpdateRequest,
    response: Response,
    service: ServiceDependency,
) -> object:
    _no_store(response)
    return service.update_claim_case(claim_id, request)


@router.post(
    "/{claim_id}/transitions",
    response_model=ClaimCaseResponse,
    responses=_COMMON_ERRORS,
)
def transition_claim_case(
    claim_id: UUID,
    request: ClaimTransitionRequest,
    response: Response,
    service: ServiceDependency,
) -> object:
    _no_store(response)
    return service.transition_claim(claim_id, request)


@router.patch(
    "/{claim_id}/checklist/{item_id}",
    response_model=ClaimCaseResponse,
    responses=_COMMON_ERRORS,
)
def update_claim_checklist(
    claim_id: UUID,
    item_id: UUID,
    request: ChecklistUpdateRequest,
    response: Response,
    service: ServiceDependency,
) -> object:
    _no_store(response)
    return service.update_checklist_item(claim_id, item_id, request)


@router.delete(
    "/{claim_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=_COMMON_ERRORS,
)
def delete_claim_case(
    claim_id: UUID, request: ExpectedVersionRequest, service: ServiceDependency
) -> Response:
    service.delete_claim_case(claim_id, expected_version=request.expected_version)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post(
    "/{claim_id}/restore",
    response_model=ClaimCaseResponse,
    responses=_COMMON_ERRORS,
)
def restore_claim_case(
    claim_id: UUID,
    request: ExpectedVersionRequest,
    response: Response,
    service: ServiceDependency,
) -> object:
    _no_store(response)
    return service.restore_claim_case(claim_id, expected_version=request.expected_version)


__all__ = ["get_claim_service", "medical_event_claim_router", "router"]
