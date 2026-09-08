"""Public review state stays independent from the saved local answer."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ReviewState = Literal[
    "queued", "running", "partial", "completed", "disagreement", "failed", "cancelled"
]


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


class GuidanceReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_run_id: UUID
    expected_event_version: int = Field(ge=1, le=2_147_483_647)
