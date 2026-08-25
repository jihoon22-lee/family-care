"""No-store Evidence disclosure route."""

from __future__ import annotations

from typing import Annotated, Literal, Self
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.decisions.evidence_service import EvidenceDetail, EvidenceService
from familycare_api.decisions.schemas import DecisionErrorResponse


class EvidenceDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    evidence_id: UUID
    document_version_id: UUID
    document_label: str = Field(min_length=1, max_length=200)
    physical_page: int = Field(ge=1, le=500)
    clause_label: str | None = Field(default=None, max_length=160)
    bounded_excerpt: str = Field(min_length=1, max_length=480)
    bbox: tuple[float, float, float, float] | None
    review_state: Literal["AI_VERIFIED", "NEEDS_REVIEW", "USER_CONFIRMED"]

    @classmethod
    def from_value(cls, value: EvidenceDetail | dict[str, object]) -> Self:
        if isinstance(value, dict):
            return cls.model_validate(value)
        return cls(
            evidence_id=value.evidence_id,
            document_version_id=value.document_version_id,
            document_label=value.document_label,
            physical_page=value.physical_page,
            clause_label=value.clause_label,
            bounded_excerpt=value.bounded_excerpt,
            bbox=value.bbox,
            review_state=value.review_state,
        )


ScopeDependency = Annotated[HouseholdScope, Depends(resolve_household_scope)]


def get_evidence_service(scope: ScopeDependency) -> EvidenceService:
    return EvidenceService.from_environment(scope)


EvidenceServiceDependency = Annotated[EvidenceService, Depends(get_evidence_service)]
router = APIRouter(prefix="/api/v1/evidence", tags=["evidence"])


@router.get(
    "/{evidence_id}",
    response_model=EvidenceDetailResponse,
    responses={
        401: {"model": DecisionErrorResponse},
        404: {"model": DecisionErrorResponse},
        422: {"model": DecisionErrorResponse},
        503: {"model": DecisionErrorResponse},
    },
)
def get_evidence(
    evidence_id: UUID,
    response: Response,
    service: EvidenceServiceDependency,
) -> EvidenceDetailResponse:
    response.headers["Cache-Control"] = "no-store"
    return EvidenceDetailResponse.from_value(service.get_evidence(evidence_id))


__all__ = ["EvidenceDetailResponse", "get_evidence_service", "router"]
