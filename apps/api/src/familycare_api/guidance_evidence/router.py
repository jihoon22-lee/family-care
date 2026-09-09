"""No-store disclosure of one exact saved guidance reference at a time."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.decisions.router import _COMMON_ERRORS
from familycare_api.guidance_evidence.models import GuidanceEvidenceDetail, GuidanceEvidenceRequest
from familycare_api.guidance_evidence.service import GuidanceEvidenceService


def get_guidance_evidence_service(
    scope: Annotated[HouseholdScope, Depends(resolve_household_scope)],
) -> GuidanceEvidenceService:
    return GuidanceEvidenceService.from_environment(scope)


router = APIRouter(prefix="/api/v1/medical-events", tags=["guidance evidence"])


@router.post(
    "/{event_id}/guidance-evidence",
    response_model=GuidanceEvidenceDetail,
    responses=_COMMON_ERRORS,
)
def get_guidance_evidence(
    event_id: UUID,
    request: GuidanceEvidenceRequest,
    response: Response,
    service: Annotated[GuidanceEvidenceService, Depends(get_guidance_evidence_service)],
) -> GuidanceEvidenceDetail:
    response.headers["Cache-Control"] = "no-store"
    return service.get_detail(event_id, request)
