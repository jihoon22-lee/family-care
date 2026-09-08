"""Explicit review routes keep local result retrieval independent."""

import os
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.decisions.errors import DecisionRepositoryUnavailable
from familycare_api.decisions.router import _COMMON_ERRORS
from familycare_api.guidance_review.models import GuidanceReviewJob, GuidanceReviewRequest
from familycare_api.guidance_review.repository import GuidanceReviewRepository
from familycare_api.guidance_review.service import GuidanceReviewService


def get_review_repository() -> GuidanceReviewRepository:
    database_url = os.getenv("FAMILYCARE_DATABASE_URL")
    if not database_url:
        raise DecisionRepositoryUnavailable
    return GuidanceReviewRepository(database_url)


def get_review_service(
    scope: Annotated[HouseholdScope, Depends(resolve_household_scope)],
    repository: Annotated[GuidanceReviewRepository, Depends(get_review_repository)],
) -> GuidanceReviewService:
    return GuidanceReviewService(scope, repository)


ServiceDependency = Annotated[GuidanceReviewService, Depends(get_review_service)]
router = APIRouter(prefix="/api/v1", tags=["guidance review"])


@router.post(
    "/medical-events/{event_id}/guidance-reviews",
    status_code=202,
    response_model=GuidanceReviewJob,
    responses=_COMMON_ERRORS,
)
def request_guidance_review(
    event_id: UUID, request: GuidanceReviewRequest, response: Response, service: ServiceDependency
) -> GuidanceReviewJob:
    response.headers["Cache-Control"] = "no-store"
    return service.request(event_id, request)


@router.get(
    "/guidance-reviews/{job_id}", response_model=GuidanceReviewJob, responses=_COMMON_ERRORS
)
def get_guidance_review(
    job_id: UUID, response: Response, service: ServiceDependency
) -> GuidanceReviewJob:
    response.headers["Cache-Control"] = "no-store"
    return service.get(job_id)


@router.post(
    "/guidance-reviews/{job_id}/cancel", response_model=GuidanceReviewJob, responses=_COMMON_ERRORS
)
def cancel_guidance_review(
    job_id: UUID, response: Response, service: ServiceDependency
) -> GuidanceReviewJob:
    response.headers["Cache-Control"] = "no-store"
    return service.cancel(job_id)
