"""Creation-time local guidance; never a recorded insurer payment."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field

from familycare_api.guidance.models import (
    GuidanceCandidate,
    GuidanceExpenses,
    GuidanceModel,
    GuidanceVersions,
)


class ClaimLocalGuidanceSnapshot(GuidanceModel):
    schema_version: Literal["claim-local-guidance-snapshot-v1"] = "claim-local-guidance-snapshot-v1"
    run_id: UUID
    medical_event_id: UUID
    family_member_id: UUID
    event_version: int = Field(ge=1)
    event_date: date | None
    versions: GuidanceVersions
    candidate: GuidanceCandidate
    expenses: GuidanceExpenses | None
