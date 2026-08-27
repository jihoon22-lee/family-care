"""Household-scoped HTTP routes for reviewed insurance-document inventory."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from familycare_api.identity.context import AuthContext, resolve_auth_context
from familycare_api.insurance_documents.schemas import (
    ComponentCreateRequest,
    DocumentSetCreateRequest,
    DocumentSetItemCreateRequest,
    ExpectedItemVersionRequest,
    InsuranceDocumentComponentResponse,
    InsuranceDocumentErrorResponse,
    InsuranceDocumentSetItemMutationResponse,
    InsuranceDocumentSetResponse,
    MemberInsuranceDocumentInventoryResponse,
)
from familycare_api.insurance_documents.service import InsuranceDocumentService

AuthDependency = Annotated[AuthContext, Depends(resolve_auth_context)]


def get_insurance_document_service(context: AuthDependency) -> InsuranceDocumentService:
    """Build a request-local service from the authenticated household and actor."""

    return InsuranceDocumentService.from_environment(context)


ServiceDependency = Annotated[
    InsuranceDocumentService,
    Depends(get_insurance_document_service),
]

router = APIRouter(prefix="/api/v1", tags=["insurance document inventory"])

_COMMON_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": InsuranceDocumentErrorResponse, "description": "Authentication required"},
    404: {"model": InsuranceDocumentErrorResponse, "description": "Scoped record not found"},
    409: {"model": InsuranceDocumentErrorResponse, "description": "Version or state conflict"},
    422: {
        "model": InsuranceDocumentErrorResponse,
        "description": "Sanitized invalid request or Evidence",
    },
    503: {
        "model": InsuranceDocumentErrorResponse,
        "description": "Local database unavailable",
    },
}


@router.get(
    "/family-members/{member_id}/insurance-document-inventory",
    response_model=MemberInsuranceDocumentInventoryResponse,
    responses=_COMMON_ERRORS,
)
def get_member_insurance_document_inventory(
    member_id: UUID,
    response: Response,
    service: ServiceDependency,
) -> MemberInsuranceDocumentInventoryResponse:
    response.headers["Cache-Control"] = "no-store"
    return MemberInsuranceDocumentInventoryResponse.from_domain(service.get_inventory(member_id))


@router.post(
    "/family-members/{member_id}/insurance-document-components",
    response_model=InsuranceDocumentComponentResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_COMMON_ERRORS,
)
def create_insurance_document_component(
    member_id: UUID,
    request: ComponentCreateRequest,
    service: ServiceDependency,
) -> InsuranceDocumentComponentResponse:
    return InsuranceDocumentComponentResponse.from_domain(
        service.create_component(
            member_id,
            document_batch_item_id=request.document_batch_item_id,
            role=request.role,
            page_start=request.page_start,
            page_end=request.page_end,
            evidence_id=request.evidence_id,
            review_state=request.review_state,
        )
    )


@router.post(
    "/family-members/{member_id}/insurance-document-sets",
    response_model=InsuranceDocumentSetResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_COMMON_ERRORS,
)
def create_insurance_document_set(
    member_id: UUID,
    request: DocumentSetCreateRequest,
    service: ServiceDependency,
) -> InsuranceDocumentSetResponse:
    return InsuranceDocumentSetResponse.from_domain(
        service.create_document_set(
            member_id,
            policy_contract_id=request.policy_contract_id,
            insurer_display=request.insurer_display,
            product_display=request.product_display,
            display_label=request.display_label,
        )
    )


@router.post(
    "/insurance-document-sets/{document_set_id}/items",
    response_model=InsuranceDocumentSetItemMutationResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_COMMON_ERRORS,
)
def attach_insurance_document_set_item(
    document_set_id: UUID,
    request: DocumentSetItemCreateRequest,
    service: ServiceDependency,
) -> InsuranceDocumentSetItemMutationResponse:
    return InsuranceDocumentSetItemMutationResponse.from_domain(
        service.attach_set_item(
            document_set_id,
            insurance_document_component_id=request.insurance_document_component_id,
            match_state=request.match_state,
            evidence_id=request.evidence_id,
            expected_set_version=request.expected_set_version,
        )
    )


@router.delete(
    "/insurance-document-set-items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=_COMMON_ERRORS,
)
def detach_insurance_document_set_item(
    item_id: UUID,
    request: ExpectedItemVersionRequest,
    service: ServiceDependency,
) -> Response:
    service.detach_set_item(item_id, expected_version=request.expected_version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/insurance-document-sets/{document_set_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=_COMMON_ERRORS,
)
def delete_insurance_document_set(
    document_set_id: UUID,
    request: ExpectedItemVersionRequest,
    service: ServiceDependency,
) -> Response:
    service.delete_document_set(
        document_set_id,
        expected_version=request.expected_version,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["get_insurance_document_service", "router"]
