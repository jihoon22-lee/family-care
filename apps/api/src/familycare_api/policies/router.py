"""Versioned HTTP routes for the policy ledger."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.policies.schemas import (
    ExpectedVersionRequest,
    FamilyMemberCreateRequest,
    FamilyMemberResponse,
    FamilyMemberUpdateRequest,
    PolicyCreateRequest,
    PolicyErrorResponse,
    PolicyResponse,
    PolicyUpdateRequest,
    RiderResponse,
)
from familycare_api.policies.service import PolicyLedgerService

ScopeDependency = Annotated[HouseholdScope, Depends(resolve_household_scope)]


def get_policy_ledger_service(scope: ScopeDependency) -> PolicyLedgerService:
    """Construct a short-lived service from trusted scope and local configuration."""

    return PolicyLedgerService.from_environment(scope)


ServiceDependency = Annotated[PolicyLedgerService, Depends(get_policy_ledger_service)]
router = APIRouter(prefix="/api/v1", tags=["policy ledger"])

_COMMON_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": PolicyErrorResponse, "description": "Authentication required"},
    404: {"model": PolicyErrorResponse, "description": "Scoped record not found"},
    409: {"model": PolicyErrorResponse, "description": "Version or state conflict"},
    422: {
        "model": PolicyErrorResponse,
        "description": "Sanitized invalid request or Evidence",
    },
    503: {"model": PolicyErrorResponse, "description": "Local database unavailable"},
}


@router.get("/family-members", response_model=list[FamilyMemberResponse], responses=_COMMON_ERRORS)
def list_family_members(service: ServiceDependency) -> list[FamilyMemberResponse]:
    return [FamilyMemberResponse.from_domain(item) for item in service.list_family_members()]


@router.post(
    "/family-members",
    response_model=FamilyMemberResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_COMMON_ERRORS,
)
def create_family_member(
    request: FamilyMemberCreateRequest,
    service: ServiceDependency,
) -> FamilyMemberResponse:
    return FamilyMemberResponse.from_domain(
        service.create_family_member(request.display_name, request.internal_alias)
    )


@router.get(
    "/family-members/trash",
    response_model=list[FamilyMemberResponse],
    responses=_COMMON_ERRORS,
)
def list_deleted_family_members(service: ServiceDependency) -> list[FamilyMemberResponse]:
    return [
        FamilyMemberResponse.from_domain(item)
        for item in service.list_family_members(deleted_only=True)
    ]


@router.get(
    "/family-members/{member_id}",
    response_model=FamilyMemberResponse,
    responses=_COMMON_ERRORS,
)
def get_family_member(member_id: UUID, service: ServiceDependency) -> FamilyMemberResponse:
    return FamilyMemberResponse.from_domain(service.get_family_member(member_id))


@router.patch(
    "/family-members/{member_id}",
    response_model=FamilyMemberResponse,
    responses=_COMMON_ERRORS,
)
def update_family_member(
    member_id: UUID,
    request: FamilyMemberUpdateRequest,
    service: ServiceDependency,
) -> FamilyMemberResponse:
    return FamilyMemberResponse.from_domain(
        service.update_family_member(
            member_id,
            expected_version=request.expected_version,
            display_name=request.display_name,
            internal_alias=request.internal_alias,
        )
    )


@router.delete(
    "/family-members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=_COMMON_ERRORS,
)
def delete_family_member(
    member_id: UUID,
    request: ExpectedVersionRequest,
    service: ServiceDependency,
) -> Response:
    service.delete_family_member(member_id, expected_version=request.expected_version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/family-members/{member_id}/restore",
    response_model=FamilyMemberResponse,
    responses=_COMMON_ERRORS,
)
def restore_family_member(
    member_id: UUID,
    request: ExpectedVersionRequest,
    service: ServiceDependency,
) -> FamilyMemberResponse:
    return FamilyMemberResponse.from_domain(
        service.restore_family_member(member_id, expected_version=request.expected_version)
    )


@router.get("/policies", response_model=list[PolicyResponse], responses=_COMMON_ERRORS)
def list_policies(service: ServiceDependency) -> list[PolicyResponse]:
    return [PolicyResponse.from_domain(item) for item in service.list_policies()]


@router.post(
    "/policies",
    response_model=PolicyResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_COMMON_ERRORS,
)
def create_policy(
    request: PolicyCreateRequest,
    service: ServiceDependency,
) -> PolicyResponse:
    return PolicyResponse.from_domain(
        service.create_policy(
            source_document_version_id=request.source_document_version_id,
            source_evidence_id=request.source_evidence_id,
            insurer_display=request.insurer_display,
            insurer_key=request.insurer_key,
            product_display=request.product_display,
            product_key=request.product_key,
            contract_date=request.contract_date,
            coverage_start_date=request.coverage_start_date,
            coverage_end_date=request.coverage_end_date,
            status=request.status,
            status_evidence_id=request.status_evidence_id,
            parties=tuple(party.to_domain() for party in request.parties),
        )
    )


@router.get("/policies/trash", response_model=list[PolicyResponse], responses=_COMMON_ERRORS)
def list_deleted_policies(service: ServiceDependency) -> list[PolicyResponse]:
    return [PolicyResponse.from_domain(item) for item in service.list_policies(deleted_only=True)]


@router.get(
    "/policies/{policy_id}",
    response_model=PolicyResponse,
    responses=_COMMON_ERRORS,
)
def get_policy(policy_id: UUID, service: ServiceDependency) -> PolicyResponse:
    return PolicyResponse.from_domain(service.get_policy(policy_id))


@router.patch(
    "/policies/{policy_id}",
    response_model=PolicyResponse,
    responses=_COMMON_ERRORS,
)
def update_policy(
    policy_id: UUID,
    request: PolicyUpdateRequest,
    service: ServiceDependency,
) -> PolicyResponse:
    return PolicyResponse.from_domain(
        service.update_policy(
            policy_id,
            expected_version=request.expected_version,
            status=request.status,
            status_evidence_id=request.status_evidence_id,
            coverage_end_date=request.coverage_end_date,
            change_coverage_end_date="coverage_end_date" in request.model_fields_set,
        )
    )


@router.delete(
    "/policies/{policy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=_COMMON_ERRORS,
)
def delete_policy(
    policy_id: UUID,
    request: ExpectedVersionRequest,
    service: ServiceDependency,
) -> Response:
    service.delete_policy(policy_id, expected_version=request.expected_version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/policies/{policy_id}/restore",
    response_model=PolicyResponse,
    responses=_COMMON_ERRORS,
)
def restore_policy(
    policy_id: UUID,
    request: ExpectedVersionRequest,
    service: ServiceDependency,
) -> PolicyResponse:
    return PolicyResponse.from_domain(
        service.restore_policy(policy_id, expected_version=request.expected_version)
    )


@router.get(
    "/policies/{policy_id}/riders",
    response_model=list[RiderResponse],
    responses=_COMMON_ERRORS,
)
def list_policy_riders(policy_id: UUID, service: ServiceDependency) -> list[RiderResponse]:
    return [RiderResponse.from_domain(item) for item in service.list_policy_riders(policy_id)]


__all__ = ["get_policy_ledger_service", "router"]
