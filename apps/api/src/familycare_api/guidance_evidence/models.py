"""A source reference selects evidence; the request cannot replace its content."""

import math
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from familycare_api.common.coverage_identity import CanonicalCoverageRef
from familycare_api.guidance.models import GuidanceEvidenceRef


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GuidanceEvidenceRequest(EvidenceModel):
    decision_run_id: UUID
    expected_event_version: int = Field(ge=1, le=2_147_483_647)
    coverage: CanonicalCoverageRef
    evidence: GuidanceEvidenceRef
    review_job_id: UUID | None = None


class GuidanceEvidenceDetail(EvidenceModel):
    schema_version: Literal["1"] = "1"
    evidence: GuidanceEvidenceRef
    content_kind: Literal["ORIGINAL", "SUMMARY", "UNAVAILABLE"]
    document_label: str = Field(min_length=1, max_length=200)
    document_version_id: UUID | None = None
    source_document_ref: UUID | None = None
    terms_edition_id: UUID | None = None
    terms_edition_label: str | None = Field(default=None, min_length=1, max_length=200)
    # The validated reference owns its source-specific bounds; equality is checked below.
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    clause_label: str | None = Field(default=None, min_length=1, max_length=160)
    text: str | None = Field(default=None, min_length=1, max_length=2048)
    truncated: bool = False
    bbox: tuple[float, float, float, float] | None = None
    reason_codes: tuple[Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,79}$")], ...] = Field(
        default=(), max_length=16
    )

    @model_validator(mode="after")
    def coherent_source(self) -> Self:
        if (
            self.page_end < self.page_start
            or self.page_start != self.evidence.page_start
            or self.page_end != self.evidence.page_end
            or (self.content_kind == "UNAVAILABLE") != (self.text is None)
            or (self.content_kind == "SUMMARY" and self.evidence.kind != "TERMS_SECTION")
            or (self.content_kind == "UNAVAILABLE" and self.truncated)
            or (
                self.bbox is not None
                and (
                    any(not math.isfinite(n) or n < 0 for n in self.bbox)
                    or self.bbox[0] > self.bbox[2]
                    or self.bbox[1] > self.bbox[3]
                )
            )
        ):
            raise ValueError("GUIDANCE_EVIDENCE_DETAIL_INVALID")
        return self


def unavailable(
    reference: GuidanceEvidenceRef, code: str = "EVIDENCE_ORIGINAL_UNAVAILABLE"
) -> GuidanceEvidenceDetail:
    return GuidanceEvidenceDetail(
        evidence=reference,
        content_kind="UNAVAILABLE",
        document_label="약관 문서"
        if reference.kind != "OPERATIONAL_EVIDENCE"
        else "보험 근거 문서",
        document_version_id=getattr(reference, "document_version_id", None),
        terms_edition_id=getattr(reference, "terms_edition_id", None),
        page_start=reference.page_start,
        page_end=reference.page_end,
        reason_codes=(code,),
    )


def bounded_text(value: str) -> tuple[str, bool]:
    # Preserve source whitespace and punctuation; only the visible length is bounded.
    return value[:2048], len(value) > 2048
