"""Scoped explicit review use cases; no provider dependency in the API."""

from typing import Protocol
from uuid import UUID

from familycare_api.common.scope import HouseholdScope
from familycare_api.guidance_review.models import GuidanceReviewJob, GuidanceReviewRequest


class GuidanceReviewStore(Protocol):
    def enqueue(
        self, scope: HouseholdScope, event_id: UUID, *, run_id: UUID, expected_event_version: int
    ) -> GuidanceReviewJob: ...

    def find_for_run(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        *,
        run_id: UUID,
        expected_event_version: int,
    ) -> GuidanceReviewJob | None: ...

    def get_job(
        self,
        scope: HouseholdScope,
        job_id: UUID,
        *,
        run_id: UUID | None = None,
    ) -> GuidanceReviewJob: ...

    def cancel(
        self,
        scope: HouseholdScope,
        job_id: UUID,
        *,
        run_id: UUID | None = None,
    ) -> GuidanceReviewJob: ...


class GuidanceReviewService:
    def __init__(self, scope: HouseholdScope, repository: GuidanceReviewStore) -> None:
        self.scope, self.repository = scope, repository

    def request(self, event_id: UUID, request: GuidanceReviewRequest) -> GuidanceReviewJob:
        return self.repository.enqueue(
            self.scope,
            event_id,
            run_id=request.decision_run_id,
            expected_event_version=request.expected_event_version,
        )

    def find_for_run(
        self,
        event_id: UUID,
        *,
        run_id: UUID,
        expected_event_version: int,
    ) -> GuidanceReviewJob | None:
        return self.repository.find_for_run(
            self.scope, event_id, run_id=run_id, expected_event_version=expected_event_version
        )

    def get(self, job_id: UUID, *, run_id: UUID | None = None) -> GuidanceReviewJob:
        if run_id is None:
            return self.repository.get_job(self.scope, job_id)
        return self.repository.get_job(self.scope, job_id, run_id=run_id)

    def cancel(self, job_id: UUID, *, run_id: UUID | None = None) -> GuidanceReviewJob:
        if run_id is None:
            return self.repository.cancel(self.scope, job_id)
        return self.repository.cancel(self.scope, job_id, run_id=run_id)
