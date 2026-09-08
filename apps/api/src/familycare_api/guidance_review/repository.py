"""Only explicit requests create review jobs; tenant and source checks stay local."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.errors import (
    DecisionInvalid,
    DecisionRepositoryUnavailable,
    MedicalEventNotFound,
)
from familycare_api.decisions.repository import DecisionRepository, _medical_event
from familycare_api.guidance.models import LocalGuidanceResponse
from familycare_api.guidance_review.models import GuidanceReviewJob
from familycare_api.policies.errors import VersionConflict

REVIEW_PROMPT_REVISION = "guidance-review-v1"
DEFAULT_REVIEW_MODEL = "gpt-5.6-terra"


class GuidanceReviewRepository:
    def __init__(self, database_url: str, *, model: str | None = None) -> None:
        self.decisions = DecisionRepository(database_url)
        self.database_url = self.decisions.database_url
        self.model = model or os.getenv("FAMILYCARE_GUIDANCE_REVIEW_MODEL", DEFAULT_REVIEW_MODEL)
        if (
            not isinstance(self.model, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", self.model) is None
        ):
            raise DecisionInvalid

    def enqueue(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        *,
        run_id: UUID,
        expected_event_version: int,
    ) -> GuidanceReviewJob:
        # A waiter may have taken its repeatable-read snapshot before the first
        # transaction committed. Retry the local transaction with a fresh snapshot.
        for _ in range(3):
            try:
                return self._enqueue(
                    scope,
                    event_id,
                    run_id=run_id,
                    expected_event_version=expected_event_version,
                )
            except psycopg.errors.SerializationFailure, psycopg.errors.UniqueViolation:
                continue
        raise DecisionRepositoryUnavailable

    def _enqueue(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        *,
        run_id: UUID,
        expected_event_version: int,
    ) -> GuidanceReviewJob:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                connection.execute("SET LOCAL lock_timeout='5s'")
                event_row = self.decisions._event_row(connection, scope, event_id, for_update=True)
                if event_row is None:
                    raise MedicalEventNotFound
                event = _medical_event(event_row)
                if event.version != expected_event_version:
                    raise VersionConflict
                run = connection.execute(
                    "SELECT * FROM decision_runs WHERE id=%s AND household_space_id=%s "
                    "AND medical_event_id=%s",
                    (run_id, scope.household_space_id, event_id),
                ).fetchone()
                if run is None:
                    raise MedicalEventNotFound
                guidance = LocalGuidanceResponse.model_validate(run["local_guidance_json"])
                if (
                    run["event_version"] != expected_event_version
                    or guidance.family_member_id != event.family_member_id
                    or self.decisions._local_guidance_is_stale(connection, scope, event, guidance)
                    is not False
                ):
                    raise DecisionInvalid
                digest = hashlib.sha256(
                    json.dumps(
                        [
                            event_row,
                            guidance.model_dump(mode="json"),
                            self.model,
                            REVIEW_PROMPT_REVISION,
                        ],
                        sort_keys=True,
                        default=str,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
                # The event lock serializes duplicate clicks before identity lookup.
                job = connection.execute(
                    "SELECT * FROM guidance_review_jobs WHERE household_space_id=%s "
                    "AND medical_event_id=%s AND input_digest=%s",
                    (scope.household_space_id, event_id, digest),
                ).fetchone()
                if job is None:
                    job = connection.execute(
                        "INSERT INTO guidance_review_jobs(household_space_id,family_member_id,"
                        "medical_event_id,decision_run_id,event_version,input_digest,source_digest,"
                        "model,prompt_revision,state) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued') "
                        "RETURNING *",
                        (
                            scope.household_space_id,
                            event.family_member_id,
                            event_id,
                            run_id,
                            event.version,
                            digest,
                            guidance.versions.status_digest,
                            self.model,
                            REVIEW_PROMPT_REVISION,
                        ),
                    ).fetchone()
                assert job is not None
                return _job(job)
        except ValidationError, ValueError:
            raise DecisionInvalid from None
        except psycopg.errors.SerializationFailure, psycopg.errors.UniqueViolation:
            raise
        except psycopg.Error:
            raise DecisionRepositoryUnavailable from None

    def get_job(self, scope: HouseholdScope, job_id: UUID) -> GuidanceReviewJob:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                row = _scoped_job(connection, scope, job_id)
                event_row = self.decisions._event_row(connection, scope, row["medical_event_id"])
                assert event_row is not None
                run = connection.execute(
                    "SELECT local_guidance_json FROM decision_runs WHERE id=%s",
                    (row["decision_run_id"],),
                ).fetchone()
                assert run is not None
                guidance = LocalGuidanceResponse.model_validate(run["local_guidance_json"])
                stale = (
                    self.decisions._local_guidance_is_stale(
                        connection, scope, _medical_event(event_row), guidance
                    )
                    is not False
                )
                return _job(row, stale=stale)
        except ValidationError, ValueError, psycopg.Error:
            raise DecisionRepositoryUnavailable from None

    def cancel(self, scope: HouseholdScope, job_id: UUID) -> GuidanceReviewJob:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = _scoped_job(connection, scope, job_id)
                event = self.decisions._event_row(
                    connection, scope, row["medical_event_id"], for_update=True
                )
                if event is None:
                    raise MedicalEventNotFound
                cancelled = connection.execute(
                    "UPDATE guidance_review_jobs SET state='cancelled',"
                    "completed_at=clock_timestamp(),"
                    "lease_token=NULL,lease_expires_at=NULL WHERE id=%s "
                    "AND household_space_id=%s AND state IN ('queued','running') RETURNING *",
                    (job_id, scope.household_space_id),
                ).fetchone()
                return _job(cancelled or _scoped_job(connection, scope, job_id))
        except psycopg.Error:
            raise DecisionRepositoryUnavailable from None


def _scoped_job(
    connection: psycopg.Connection[dict[str, Any]], scope: HouseholdScope, job_id: UUID
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT j.* FROM guidance_review_jobs j JOIN medical_events e "
        "ON e.id=j.medical_event_id AND e.household_space_id=j.household_space_id "
        "WHERE j.id=%s AND j.household_space_id=%s AND e.deleted_at IS NULL",
        (job_id, scope.household_space_id),
    ).fetchone()
    if row is None:
        raise MedicalEventNotFound
    return row


def _job(row: dict[str, Any], *, stale: bool = False) -> GuidanceReviewJob:
    return GuidanceReviewJob.model_validate(
        {
            key: row[key]
            for key in (
                "id",
                "medical_event_id",
                "decision_run_id",
                "event_version",
                "state",
                "http_attempts",
                "error_code",
                "created_at",
                "completed_at",
            )
        }
        | {"stale": stale}
    )
