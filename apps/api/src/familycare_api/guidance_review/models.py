"""Public review state stays independent from the saved local answer."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from familycare_api.common.coverage_identity import CanonicalCoverageRef
from familycare_api.guidance.models import GuidanceCandidate, LocalGuidanceResponse

ReviewState = Literal[
    "queued", "running", "partial", "completed", "disagreement", "failed", "cancelled"
]

Code = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,79}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GuidanceReviewSourceCitation(ReviewModel):
    kind: Literal["REVIEW_SOURCE_CITATION"] = "REVIEW_SOURCE_CITATION"
    packet_id: str = Field(min_length=1, max_length=256)
    citation_id: UUID
    document_version_id: UUID
    terms_edition_id: UUID
    source_node_id: str = Field(min_length=1, max_length=128)
    page_start: int = Field(ge=1, le=500)
    page_end: int = Field(ge=1, le=500)
    start: int = Field(ge=0, le=262144)
    end: int = Field(ge=1, le=262144)
    source_layer: Literal["native", "ocr"]
    bbox: tuple[float, float, float, float]
    source_sha256: Digest
    quote: str = Field(min_length=1, max_length=8192)


class GuidanceReviewCoverageScope(ReviewModel):
    ref: CanonicalCoverageRef
    contract_label: str = Field(max_length=800)
    coverage_label: str = Field(max_length=800)
    source_state: Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE"]
    reviewed_packets: int = Field(ge=0, le=8)
    total_packets: int = Field(ge=0, le=8)
    reason_codes: tuple[Code, ...] = Field(default=(), max_length=64)


class GuidanceReviewScope(ReviewModel):
    total_coverages: int = Field(ge=0)
    indexed_coverages: int = Field(ge=0, le=128)
    total_packets: int = Field(ge=0, le=8)
    reviewed_packets: int = Field(ge=0, le=8)
    unreviewed_packets: int = Field(ge=0, le=8)
    omitted_packets: int = Field(ge=0, le=8)
    expected_regions: int = Field(default=0, ge=0)
    supplied_regions: int = Field(default=0, ge=0)
    unsupplied_regions: int = Field(default=0, ge=0)
    complete: bool
    reason_codes: tuple[Code, ...] = Field(default=(), max_length=64)
    coverages: tuple[GuidanceReviewCoverageScope, ...] = Field(default=(), max_length=128)


class GuidanceReviewFinding(ReviewModel):
    kind: Literal["AGREEMENT", "CORRECTION", "ADDITIONAL_CANDIDATE", "EXCEPTION", "CONFLICT"]
    coverage: CanonicalCoverageRef | None
    status: Literal["APPLIED", "AGREEMENT", "OPINION", "REJECTED"]
    reason_codes: tuple[Code, ...] = Field(min_length=1, max_length=64)
    evidence: tuple[GuidanceReviewSourceCitation, ...] = Field(default=(), max_length=64)
    affected_fact_paths: tuple[str, ...] = Field(default=(), max_length=32)
    publication_id: UUID | None = None


class GuidanceReviewDifference(ReviewModel):
    coverage: CanonicalCoverageRef
    change: Literal["ADDED", "REMOVED", "CHANGED"]
    before: GuidanceCandidate | None
    after: GuidanceCandidate | None


class GuidanceReviewResult(ReviewModel):
    schema_version: Literal["1"] = "1"
    source_digest: Digest
    scope: GuidanceReviewScope
    findings: tuple[GuidanceReviewFinding, ...] = Field(max_length=16)
    differences: tuple[GuidanceReviewDifference, ...] = Field(max_length=256)
    guidance: LocalGuidanceResponse
    reason_codes: tuple[Code, ...] = Field(min_length=1, max_length=64)


class GuidanceReviewUsage(ReviewModel):
    input_tokens: int | None = Field(ge=0)
    output_tokens: int | None = Field(ge=0)
    total_tokens: int | None = Field(ge=0)
    reserved_input_tokens: int = Field(ge=0, le=65536)
    reserved_output_tokens: int = Field(ge=0, le=8000)
    requests_reserved: int = Field(ge=0, le=2)
    usage_complete: bool


class GuidanceReviewJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    id: UUID
    medical_event_id: UUID
    decision_run_id: UUID
    event_version: int = Field(ge=1)
    state: ReviewState
    http_attempts: int = Field(ge=0, le=2)
    error_code: str | None = Field(default=None, pattern=r"^REVIEW_[A-Z_]{1,64}$")
    stale: bool = False
    created_at: datetime
    completed_at: datetime | None = None
    result: GuidanceReviewResult | None = None
    usage: GuidanceReviewUsage | None = None


class GuidanceReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_run_id: UUID
    expected_event_version: int = Field(ge=1, le=2_147_483_647)
