"""Authenticated HTTP routes for insurance ledger reconciliation."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from familycare_api.identity.context import AuthContext, resolve_auth_context
from familycare_api.insurance_reconciliation.schemas import (
    DocumentResolutionRequest,
    DocumentResolutionResponse,
    InsuranceReconciliationErrorResponse,
    MemberInsuranceReconciliationResponse,
    OperationalLinkMutationResponse,
    OperationalLinkRequest,
)
from familycare_api.insurance_reconciliation.service import (
    InsuranceReconciliationService,
)

AuthDependency = Annotated[AuthContext, Depends(resolve_auth_context)]


def get_insurance_reconciliation_service(
    context: AuthDependency,
) -> InsuranceReconciliationService:
    return InsuranceReconciliationService.from_environment(context)


ServiceDependency = Annotated[
    InsuranceReconciliationService,
    Depends(get_insurance_reconciliation_service),
]

router = APIRouter(prefix="/api/v1", tags=["insurance reconciliation"])

_COMMON_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": InsuranceReconciliationErrorResponse, "description": "Authentication required"},
    404: {"model": InsuranceReconciliationErrorResponse, "description": "Scoped record not found"},
    409: {"model": InsuranceReconciliationErrorResponse, "description": "State conflict"},
    422: {"model": InsuranceReconciliationErrorResponse, "description": "Invalid request"},
    503: {"model": InsuranceReconciliationErrorResponse, "description": "Database unavailable"},
}


@router.get(
    "/family-members/{member_id}/insurance-reconciliation",
    response_model=MemberInsuranceReconciliationResponse,
    responses=_COMMON_ERRORS,
)
def get_member_insurance_reconciliation(
    member_id: UUID,
    response: Response,
    service: ServiceDependency,
) -> MemberInsuranceReconciliationResponse:
    response.headers["Cache-Control"] = "no-store"
    return MemberInsuranceReconciliationResponse.from_domain(service.get_member(member_id))


@router.post(
    "/private-knowledge/current/contracts/{contract_id}/operational-link",
    response_model=OperationalLinkMutationResponse,
    responses=_COMMON_ERRORS,
)
def confirm_operational_link(
    contract_id: UUID,
    request: OperationalLinkRequest,
    response: Response,
    service: ServiceDependency,
) -> OperationalLinkMutationResponse:
    response.headers["Cache-Control"] = "no-store"
    return OperationalLinkMutationResponse.from_domain(
        service.confirm_operational_link(
            contract_id,
            decision=request.decision,
            conflict=request.conflict,
            policy_contract_id=request.policy_contract_id,
            reason_code=request.reason_code,
            expected_current_link_id=request.expected_current_link_id,
        )
    )


@router.post(
    "/document-batch-items/{item_id}/resolution",
    response_model=DocumentResolutionResponse,
    responses=_COMMON_ERRORS,
)
def confirm_document_resolution(
    item_id: UUID,
    request: DocumentResolutionRequest,
    response: Response,
    service: ServiceDependency,
) -> DocumentResolutionResponse:
    response.headers["Cache-Control"] = "no-store"
    return DocumentResolutionResponse.from_domain(
        service.confirm_document_resolution(
            item_id,
            resolution=request.resolution,
            replacement_item_id=request.replacement_item_id,
            reason_code=request.reason_code,
            expected_current_resolution_id=request.expected_current_resolution_id,
        )
    )


__all__ = ["get_insurance_reconciliation_service", "router"]
