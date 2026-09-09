"""Typed result shared by the synthetic database fixture and explicit evaluation CLI."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import MedicalEvent
from familycare_api.decisions.service import DecisionService
from familycare_api.guidance_review.sources import ReviewSources


@dataclass(frozen=True, slots=True)
class FixedReviewFixture:
    database_url: str
    scope: HouseholdScope
    event: MedicalEvent
    original: Any
    coverage_keys: Mapping[UUID, str]
    sources: ReviewSources
    service: DecisionService
