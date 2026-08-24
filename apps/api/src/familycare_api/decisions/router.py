"""Versioned HTTP routes for MedicalEvent analysis and immutable results."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.decisions.schemas import (
    CoverageDecisionResponse,
    DecisionErrorResponse,
    ExpectedVersionRequest,
    MedicalEventCreateRequest,
    MedicalEventResponse,
    MedicalEventUpdateRequest,
)
from familycare_api.decisions.service import DecisionService

ScopeDependency = Annotated[HouseholdScope, Depends(resolve_household_scope)]


def get_decision_service(scope: ScopeDependency) -> DecisionService:
    return DecisionService.from_environment(scope)


ServiceDependency = Annotated[DecisionService, Depends(get_decision_service)]
router = APIRouter(prefix="/api/v1/medical-events", tags=["coverage decisions"])

_COMMON_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": DecisionErrorResponse, "description": "Authentication required"},
    404: {"model": DecisionErrorResponse, "description": "Scoped record not found"},
    409: {"model": DecisionErrorResponse, "description": "Version conflict"},
    422: {"model": DecisionErrorResponse, "description": "Sanitized invalid request"},
    503: {"model": DecisionErrorResponse, "description": "Local database unavailable"},
}


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.post(
    "",
    response_model=MedicalEventResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_COMMON_ERRORS,
)
def create_medical_event(
    request: MedicalEventCreateRequest,
    response: Response,
    service: ServiceDependency,
) -> MedicalEventResponse:
    _no_store(response)
    return MedicalEventResponse.from_value(service.create_medical_event(request))


@router.get("/trash", response_model=list[MedicalEventResponse], responses=_COMMON_ERRORS)
def list_deleted_medical_events(
    response: Response,
    service: ServiceDependency,
) -> list[MedicalEventResponse]:
    _no_store(response)
    return [MedicalEventResponse.from_value(item) for item in service.list_deleted_medical_events()]


@router.get("/{event_id}", response_model=MedicalEventResponse, responses=_COMMON_ERRORS)
def get_medical_event(
    event_id: UUID,
    response: Response,
    service: ServiceDependency,
) -> MedicalEventResponse:
    _no_store(response)
    return MedicalEventResponse.from_value(service.get_medical_event(event_id))


@router.patch("/{event_id}", response_model=MedicalEventResponse, responses=_COMMON_ERRORS)
def update_medical_event(
    event_id: UUID,
    request: MedicalEventUpdateRequest,
    response: Response,
    service: ServiceDependency,
) -> MedicalEventResponse:
    _no_store(response)
    return MedicalEventResponse.from_value(service.update_medical_event(event_id, request))


@router.delete(
    "/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=_COMMON_ERRORS,
)
def delete_medical_event(
    event_id: UUID,
    request: ExpectedVersionRequest,
    service: ServiceDependency,
) -> Response:
    service.delete_medical_event(event_id, expected_version=request.expected_version)
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})


@router.post(
    "/{event_id}/restore",
    response_model=MedicalEventResponse,
    responses=_COMMON_ERRORS,
)
def restore_medical_event(
    event_id: UUID,
    request: ExpectedVersionRequest,
    response: Response,
    service: ServiceDependency,
) -> MedicalEventResponse:
    _no_store(response)
    return MedicalEventResponse.from_value(
        service.restore_medical_event(event_id, expected_version=request.expected_version)
    )


@router.post(
    "/{event_id}/analyze",
    response_model=CoverageDecisionResponse,
    responses=_COMMON_ERRORS,
)
def analyze_medical_event(
    event_id: UUID,
    response: Response,
    service: ServiceDependency,
) -> CoverageDecisionResponse:
    _no_store(response)
    return CoverageDecisionResponse.from_value(service.analyze_medical_event(event_id))


@router.get(
    "/{event_id}/results/{version}",
    response_model=CoverageDecisionResponse,
    responses=_COMMON_ERRORS,
)
def get_decision_result(
    event_id: UUID,
    version: int,
    response: Response,
    service: ServiceDependency,
) -> CoverageDecisionResponse:
    _no_store(response)
    return CoverageDecisionResponse.from_value(service.get_decision_result(event_id, version))


__all__ = ["get_decision_service", "router"]
