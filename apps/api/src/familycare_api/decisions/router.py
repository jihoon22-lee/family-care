"""Versioned HTTP routes for MedicalEvent analysis and immutable results."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Response, status

from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.decisions.calculation_schemas import (
    BenefitCalculationResponse,
    BenefitCalculationsResponse,
    ReceiptLineCreateRequest,
    ReceiptLineDeleteRequest,
    ReceiptLineResponse,
    ReceiptLinesResponse,
    ReceiptLineUpdateRequest,
)
from familycare_api.decisions.calculation_service import CalculationService
from familycare_api.decisions.schemas import (
    CoverageDecisionResponse,
    DecisionErrorResponse,
    ExpectedVersionRequest,
    MedicalEventCreateRequest,
    MedicalEventResponse,
    MedicalEventUpdateRequest,
)
from familycare_api.decisions.service import DecisionService
from familycare_api.decisions.structuring_schemas import (
    StructureAcceptedResponse,
    StructuringJobResponse,
)
from familycare_api.decisions.structuring_service import EventStructuringService

ScopeDependency = Annotated[HouseholdScope, Depends(resolve_household_scope)]


def get_decision_service(scope: ScopeDependency) -> DecisionService:
    return DecisionService.from_environment(scope)


ServiceDependency = Annotated[DecisionService, Depends(get_decision_service)]


def get_event_structuring_service(scope: ScopeDependency) -> EventStructuringService:
    return EventStructuringService.from_environment(scope)


StructuringServiceDependency = Annotated[
    EventStructuringService,
    Depends(get_event_structuring_service),
]


def get_calculation_service(scope: ScopeDependency) -> CalculationService:
    return CalculationService.from_environment(scope)


CalculationServiceDependency = Annotated[
    CalculationService,
    Depends(get_calculation_service),
]
router = APIRouter(prefix="/api/v1/medical-events", tags=["coverage decisions"])
structuring_job_router = APIRouter(
    prefix="/api/v1/medical-event-structuring-jobs",
    tags=["medical event structuring"],
)

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


@router.post(
    "/{event_id}/structure",
    response_model=StructureAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_COMMON_ERRORS,
)
def structure_medical_event(
    event_id: UUID,
    request: ExpectedVersionRequest,
    response: Response,
    service: StructuringServiceDependency,
) -> StructureAcceptedResponse:
    _no_store(response)
    return StructureAcceptedResponse.from_value(
        service.enqueue(event_id, expected_version=request.expected_version)
    )


@structuring_job_router.get(
    "/{job_id}",
    response_model=StructuringJobResponse,
    responses=_COMMON_ERRORS,
)
def get_structuring_job(
    job_id: UUID,
    response: Response,
    service: StructuringServiceDependency,
) -> StructuringJobResponse:
    _no_store(response)
    return StructuringJobResponse.from_value(service.get_job(job_id))


@router.get(
    "/{event_id}/results/{version}",
    response_model=CoverageDecisionResponse,
    responses=_COMMON_ERRORS,
)
def get_decision_result(
    event_id: UUID,
    version: Annotated[int, Path(ge=1, le=2_147_483_647)],
    response: Response,
    service: ServiceDependency,
) -> CoverageDecisionResponse:
    _no_store(response)
    return CoverageDecisionResponse.from_value(service.get_decision_result(event_id, version))


@router.post(
    "/{event_id}/receipt-lines",
    response_model=ReceiptLineResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_COMMON_ERRORS,
)
def create_receipt_line(
    event_id: UUID,
    request: ReceiptLineCreateRequest,
    response: Response,
    service: CalculationServiceDependency,
) -> ReceiptLineResponse:
    _no_store(response)
    return ReceiptLineResponse.from_domain(service.create_receipt_line(event_id, request))


@router.get(
    "/{event_id}/receipt-lines",
    response_model=ReceiptLinesResponse,
    responses=_COMMON_ERRORS,
)
def list_receipt_lines(
    event_id: UUID,
    response: Response,
    service: CalculationServiceDependency,
) -> ReceiptLinesResponse:
    _no_store(response)
    return ReceiptLinesResponse(
        schema_version="1",
        receipt_lines=tuple(
            ReceiptLineResponse.from_domain(value) for value in service.list_receipt_lines(event_id)
        ),
    )


@router.patch(
    "/{event_id}/receipt-lines/{line_id}",
    response_model=ReceiptLineResponse,
    responses=_COMMON_ERRORS,
)
def update_receipt_line(
    event_id: UUID,
    line_id: UUID,
    request: ReceiptLineUpdateRequest,
    response: Response,
    service: CalculationServiceDependency,
) -> ReceiptLineResponse:
    _no_store(response)
    return ReceiptLineResponse.from_domain(service.update_receipt_line(event_id, line_id, request))


@router.delete(
    "/{event_id}/receipt-lines/{line_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=_COMMON_ERRORS,
)
def delete_receipt_line(
    event_id: UUID,
    line_id: UUID,
    request: ReceiptLineDeleteRequest,
    service: CalculationServiceDependency,
) -> Response:
    service.delete_receipt_line(
        event_id,
        line_id,
        expected_version=request.expected_version,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})


@router.get(
    "/{event_id}/calculations",
    response_model=BenefitCalculationsResponse,
    responses=_COMMON_ERRORS,
)
def get_benefit_calculations(
    event_id: UUID,
    response: Response,
    service: CalculationServiceDependency,
) -> BenefitCalculationsResponse:
    _no_store(response)
    return BenefitCalculationsResponse(
        schema_version="1",
        calculations=tuple(
            BenefitCalculationResponse.from_value(value)
            for value in service.get_calculations(event_id)
        ),
    )


__all__ = [
    "get_calculation_service",
    "get_decision_service",
    "get_event_structuring_service",
    "router",
    "structuring_job_router",
]
