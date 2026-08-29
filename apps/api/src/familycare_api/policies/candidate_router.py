"""Household-scoped policy-candidate review routes."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.contracts.generated_business import PolicyCandidateFieldId
from familycare_api.identity.context import AuthContext, Unauthenticated
from familycare_api.policies.candidate_errors import InvalidCandidateCorrection
from familycare_api.policies.candidate_models import (
    CandidateConfirmationRequest,
    CandidateCorrectionRequest,
    CandidateErrorResponse,
    CandidateRejectionRequest,
    PolicyReviewItem,
)
from familycare_api.policies.candidate_service import CandidateReviewService

ScopeDependency = Annotated[HouseholdScope, Depends(resolve_household_scope)]


def get_candidate_review_service(scope: ScopeDependency) -> CandidateReviewService:
    """Construct a request-local service from trusted server scope."""

    del scope
    return CandidateReviewService.from_environment()


ServiceDependency = Annotated[CandidateReviewService, Depends(get_candidate_review_service)]
router = APIRouter(prefix="/api/v1", tags=["policy candidate review"])

_COMMON_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": CandidateErrorResponse, "description": "Scoped review item not found"},
    409: {"model": CandidateErrorResponse, "description": "Candidate version conflict"},
    422: {"model": CandidateErrorResponse, "description": "Invalid candidate correction"},
}


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _actor_id(request: Request, scope: HouseholdScope) -> UUID:
    context = getattr(request.state, "auth_context", None)
    if (
        not isinstance(context, AuthContext)
        or context.household_space_id != scope.household_space_id
    ):
        raise Unauthenticated
    return context.user_id


@router.get(
    "/review-items",
    response_model=list[PolicyReviewItem],
    responses=_COMMON_ERRORS,
)
def list_review_items(
    response: Response,
    scope: ScopeDependency,
    service: ServiceDependency,
    domain: Literal["policy", "rider_clause", "coverage_rule"] = "policy",
    status: Literal["NEEDS_REVIEW", "AI_VERIFIED", "USER_CONFIRMED"] = "NEEDS_REVIEW",
    family_member_id: UUID | None = None,
) -> list[PolicyReviewItem]:
    _no_store(response)
    if family_member_id is None:
        return service.list_review_items(scope=scope, status=status, domain=domain)
    return service.list_review_items(
        scope=scope,
        status=status,
        domain=domain,
        family_member_id=family_member_id,
    )


@router.get(
    "/review-items/{review_item_id}",
    response_model=PolicyReviewItem,
    responses=_COMMON_ERRORS,
)
def get_review_item(
    review_item_id: UUID,
    response: Response,
    scope: ScopeDependency,
    service: ServiceDependency,
) -> PolicyReviewItem:
    _no_store(response)
    return service.get_review_item(scope=scope, review_item_id=review_item_id)


@router.patch(
    "/policies/{policy_id}/candidate-fields/{field_id}",
    response_model=PolicyReviewItem,
    responses=_COMMON_ERRORS,
)
def correct_candidate_field(
    policy_id: UUID,
    field_id: PolicyCandidateFieldId,
    request: CandidateCorrectionRequest,
    http_request: Request,
    response: Response,
    scope: ScopeDependency,
    service: ServiceDependency,
) -> PolicyReviewItem:
    if request.field_id != field_id:
        raise InvalidCandidateCorrection
    _no_store(response)
    return service.correct_field(
        scope=scope,
        policy_id=policy_id,
        request=request,
        actor_id=_actor_id(http_request, scope),
    )


@router.patch(
    "/review-items/{review_item_id}/candidate-fields/{field_id}",
    response_model=PolicyReviewItem,
    responses=_COMMON_ERRORS,
)
def correct_review_item_field(
    review_item_id: UUID,
    field_id: PolicyCandidateFieldId,
    request: CandidateCorrectionRequest,
    http_request: Request,
    response: Response,
    scope: ScopeDependency,
    service: ServiceDependency,
) -> PolicyReviewItem:
    """Correct one exact review item even when several candidates share a policy."""

    if request.field_id != field_id:
        raise InvalidCandidateCorrection
    _no_store(response)
    return service.correct_field(
        scope=scope,
        review_item_id=review_item_id,
        request=request,
        actor_id=_actor_id(http_request, scope),
    )


@router.patch(
    "/review-items/{review_item_id}/fields/{field_id}",
    response_model=PolicyReviewItem,
    responses=_COMMON_ERRORS,
)
def correct_typed_review_item_field(
    review_item_id: UUID,
    field_id: PolicyCandidateFieldId,
    request: CandidateCorrectionRequest,
    http_request: Request,
    response: Response,
    scope: ScopeDependency,
    service: ServiceDependency,
) -> PolicyReviewItem:
    """Create a child version from one generated typed field correction."""

    if request.field_id != field_id:
        raise InvalidCandidateCorrection
    _no_store(response)
    return service.correct_field(
        scope=scope,
        review_item_id=review_item_id,
        request=request,
        actor_id=_actor_id(http_request, scope),
    )


@router.post(
    "/review-items/{review_item_id}/confirm",
    response_model=PolicyReviewItem,
    responses=_COMMON_ERRORS,
)
def confirm_candidate(
    review_item_id: UUID,
    request: CandidateConfirmationRequest,
    http_request: Request,
    response: Response,
    scope: ScopeDependency,
    service: ServiceDependency,
) -> PolicyReviewItem:
    _no_store(response)
    return service.confirm(
        scope=scope,
        review_item_id=review_item_id,
        request=request,
        actor_id=_actor_id(http_request, scope),
    )


@router.post(
    "/review-items/{review_item_id}/reject",
    response_model=PolicyReviewItem,
    responses=_COMMON_ERRORS,
)
def reject_candidate(
    review_item_id: UUID,
    request: CandidateRejectionRequest,
    http_request: Request,
    response: Response,
    scope: ScopeDependency,
    service: ServiceDependency,
) -> PolicyReviewItem:
    _no_store(response)
    return service.reject(
        scope=scope,
        review_item_id=review_item_id,
        request=request,
        actor_id=_actor_id(http_request, scope),
    )


__all__ = ["get_candidate_review_service", "router"]
