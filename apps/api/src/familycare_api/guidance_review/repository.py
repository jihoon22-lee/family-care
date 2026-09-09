"""Only explicit requests create review jobs; tenant and source checks stay local."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.errors import (
    DecisionInvalid,
    DecisionRepositoryUnavailable,
    MedicalEventNotFound,
)
from familycare_api.decisions.repository import DecisionRepository, _medical_event
from familycare_api.guidance.models import LocalGuidanceResponse
from familycare_api.guidance_review.binding import read_review_input, require_review_match
from familycare_api.guidance_review.models import GuidanceReviewJob, GuidanceReviewUsage
from familycare_api.guidance_review.sources import read_review_sources
from familycare_api.policies.errors import VersionConflict
from familycare_api.terms_knowledge.work_repository import _privacy_digest

REVIEW_PROMPT_REVISION = "guidance-review-proposals-v1"
DEFAULT_REVIEW_MODEL = "gpt-5.6-terra"


class GuidanceReviewRepository:
    def __init__(self, database_url: str, *, model: str | None = None) -> None:
        self.decisions = DecisionRepository(database_url)
        self.database_url = self.decisions.database_url
        selected_model = model or os.getenv("FAMILYCARE_GUIDANCE_REVIEW_MODEL", DEFAULT_REVIEW_MODEL)
        if (
            not isinstance(selected_model, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", selected_model) is None
        ):
            raise DecisionInvalid
        self.model = selected_model

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
                requested = read_review_input(
                    connection,
                    scope,
                    event_row,
                    run_id,
                    model=self.model,
                    prompt_revision=REVIEW_PROMPT_REVISION,
                    decisions=self.decisions,
                )
                sources, privacy_digest, digest = (
                    requested.sources,
                    requested.privacy_digest,
                    requested.input_digest,
                )
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
                            sources.digest_sha256,
                            self.model,
                            REVIEW_PROMPT_REVISION,
                        ),
                    ).fetchone()
                    assert job is not None
                    connection.execute(
                        "INSERT INTO guidance_review_inputs(review_job_id,sources_json,"
                        "event_json,privacy_digest) VALUES(%s,%s,%s,%s)",
                        (
                            job["id"],
                            Jsonb(sources.to_payload()),
                            Jsonb(json.loads(json.dumps(asdict(event), default=str))),
                            privacy_digest,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO guidance_review_contexts(review_job_id,scope_digest) "
                        "VALUES(%s,guidance_review_scope_digest(%s))",
                        (job["id"], job["id"]),
                    )
                assert job is not None
                return _details(connection, job, matched_run_id=run_id)
        except ValidationError, ValueError:
            raise DecisionInvalid from None
        except psycopg.errors.SerializationFailure, psycopg.errors.UniqueViolation:
            raise
        except psycopg.Error:
            raise DecisionRepositoryUnavailable from None

    def find_for_run(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        *,
        run_id: UUID,
        expected_event_version: int,
    ) -> GuidanceReviewJob | None:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                event_row = self.decisions._event_row(connection, scope, event_id)
                if event_row is None:
                    raise MedicalEventNotFound
                if event_row["version"] != expected_event_version:
                    raise VersionConflict
                requested = read_review_input(
                    connection,
                    scope,
                    event_row,
                    run_id,
                    model=self.model,
                    prompt_revision=REVIEW_PROMPT_REVISION,
                    decisions=self.decisions,
                )
                row = connection.execute(
                    "SELECT * FROM guidance_review_jobs WHERE household_space_id=%s "
                    "AND medical_event_id=%s AND input_digest=%s",
                    (scope.household_space_id, event_id, requested.input_digest),
                ).fetchone()
                if row is None:
                    return None
                require_review_match(
                    connection, scope, event_row, run_id, row, decisions=self.decisions
                )
                return _details(connection, row, matched_run_id=run_id)
        except ValidationError, ValueError, psycopg.Error:
            raise DecisionRepositoryUnavailable from None

    def get_job(
        self,
        scope: HouseholdScope,
        job_id: UUID,
        *,
        run_id: UUID | None = None,
    ) -> GuidanceReviewJob:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                row = _scoped_job(connection, scope, job_id)
                event_row = self.decisions._event_row(connection, scope, row["medical_event_id"])
                assert event_row is not None
                if run_id is not None:
                    require_review_match(
                        connection, scope, event_row, run_id, row, decisions=self.decisions
                    )
                    return _details(connection, row, matched_run_id=run_id)
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
                if not stale:
                    sources = read_review_sources(
                        connection, scope, _medical_event(event_row), self.decisions
                    )
                    stored = connection.execute(
                        "SELECT privacy_digest FROM guidance_review_inputs WHERE review_job_id=%s",
                        (job_id,),
                    ).fetchone()
                    stale = (
                        sources.digest_sha256 != row["source_digest"]
                        or stored is None
                        or stored["privacy_digest"] != _privacy_digest(connection, scope)
                    )
                if not stale:
                    current = connection.execute(
                        "SELECT scope_digest=guidance_review_scope_digest(review_job_id) "
                        "AS current "
                        "FROM guidance_review_contexts WHERE review_job_id=%s",
                        (job_id,),
                    ).fetchone()
                    stale = current is None or current["current"] is not True
                return _details(connection, row, stale=stale)
        except ValidationError, ValueError, psycopg.Error:
            raise DecisionRepositoryUnavailable from None

    def cancel(
        self,
        scope: HouseholdScope,
        job_id: UUID,
        *,
        run_id: UUID | None = None,
    ) -> GuidanceReviewJob:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = _scoped_job(connection, scope, job_id)
                event = self.decisions._event_row(
                    connection, scope, row["medical_event_id"], for_update=True
                )
                if event is None:
                    raise MedicalEventNotFound
                if run_id is not None:
                    require_review_match(
                        connection, scope, event, run_id, row, decisions=self.decisions
                    )
                cancelled = connection.execute(
                    "UPDATE guidance_review_jobs SET state='cancelled',"
                    "completed_at=clock_timestamp(),"
                    "lease_token=NULL,lease_expires_at=NULL WHERE id=%s "
                    "AND household_space_id=%s AND state IN ('queued','running') RETURNING *",
                    (job_id, scope.household_space_id),
                ).fetchone()
                return _details(
                    connection,
                    cancelled or _scoped_job(connection, scope, job_id),
                    matched_run_id=run_id,
                )
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


def _details(
    connection: psycopg.Connection[dict[str, Any]],
    row: dict[str, Any],
    *,
    stale: bool = False,
    matched_run_id: UUID | None = None,
) -> GuidanceReviewJob:
    result = connection.execute(
        "SELECT result_json FROM guidance_review_results WHERE review_job_id=%s",
        (row["id"],),
    ).fetchone()
    requests = connection.execute(
        "SELECT input_token_bound,output_token_bound,usage_json FROM guidance_review_requests "
        "WHERE review_job_id=%s ORDER BY reserved_at,id",
        (row["id"],),
    ).fetchall()
    known = all(
        isinstance(request["usage_json"], dict)
        and all(
            type(request["usage_json"].get(key)) is int and request["usage_json"][key] >= 0
            for key in ("input_tokens", "output_tokens", "total_tokens")
        )
        and request["usage_json"]["input_tokens"] + request["usage_json"]["output_tokens"]
        == request["usage_json"]["total_tokens"]
        for request in requests
    )
    usage = GuidanceReviewUsage(
        input_tokens=sum(request["usage_json"]["input_tokens"] for request in requests)
        if known
        else None,
        output_tokens=sum(request["usage_json"]["output_tokens"] for request in requests)
        if known
        else None,
        total_tokens=sum(request["usage_json"]["total_tokens"] for request in requests)
        if known
        else None,
        reserved_input_tokens=sum(request["input_token_bound"] for request in requests),
        reserved_output_tokens=sum(request["output_token_bound"] for request in requests),
        requests_reserved=len(requests),
        usage_complete=known,
    )
    return GuidanceReviewJob.model_validate(
        _job(row, stale=stale).model_dump()
        | {
            "result": result["result_json"] if result else None,
            "usage": usage,
            "matched_decision_run_id": None if stale else matched_run_id or row["decision_run_id"],
        }
    )
