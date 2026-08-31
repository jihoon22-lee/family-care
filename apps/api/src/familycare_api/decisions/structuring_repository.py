"""Scoped PostgreSQL persistence for optional MedicalEvent structuring."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import MedicalEvent
from familycare_api.decisions.errors import (
    DecisionInvalid,
    DecisionRepositoryUnavailable,
    MedicalEventNotFound,
)
from familycare_api.decisions.structuring_schemas import (
    FactConfidence,
    FactFieldId,
    FactIssueCode,
    FactSource,
    FactState,
    StructuringErrorCode,
    StructuringJobState,
    is_valid_structured_fact_value,
)
from familycare_api.decisions.structuring_service import (
    FactIssue,
    OptionalQuestion,
    StructuredFact,
    StructuringJob,
)
from familycare_api.policies.errors import VersionConflict

STRUCTURER_VERSION = "event-structurer-v1"
_FACT_FIELDS = frozenset(
    {
        "event_date",
        "visit_date",
        "condition_class",
        "diagnosis_label",
        "treatment_kind",
        "admission",
        "outpatient",
        "pharmacy",
        "diagnosis_code",
        "procedure_code",
        "anatomical_site_code",
        "pathology_code",
        "treatment_setting",
        "treatment_context",
        "separately_billed_treatment",
    }
)
_FACT_SOURCES = frozenset({"user", "ai", "system"})
_FACT_STATES = frozenset({"confirmed", "ambiguous", "missing", "conflict"})
_FACT_CONFIDENCE = frozenset({"high", "medium", "low"})
_JOB_STATES = frozenset(
    {
        "queued",
        "running",
        "succeeded",
        "retryable_failed",
        "permanently_failed",
        "cancelled",
    }
)
_ERROR_CODES = frozenset(
    {
        "STRUCTURING_AUTHENTICATION_FAILED",
        "STRUCTURING_INVALID_RESPONSE",
        "STRUCTURING_PROVIDER_TIMEOUT",
        "STRUCTURING_RATE_LIMITED",
        "STRUCTURING_UNAVAILABLE",
    }
)


def _database_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise DecisionRepositoryUnavailable
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


class EventStructuringRepository:
    """Keep structuring queue records independent from PDF analysis jobs."""

    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def enqueue(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        expected_version: int,
    ) -> StructuringJob:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                event = connection.execute(
                    """
                    SELECT id, version, situation_text
                    FROM medical_events
                    WHERE id = %s AND household_space_id = %s AND deleted_at IS NULL
                    FOR UPDATE
                    """,
                    (event_id, scope.household_space_id),
                ).fetchone()
                if event is None:
                    raise MedicalEventNotFound
                if int(event["version"]) != expected_version:
                    raise VersionConflict
                situation = event.get("situation_text")
                if (
                    not isinstance(situation, str)
                    or not situation.strip()
                    or len(situation) > 2_000
                ):
                    raise DecisionInvalid
                existing = connection.execute(
                    """
                    SELECT *
                    FROM medical_event_structuring_jobs
                    WHERE household_space_id = %s
                      AND medical_event_id = %s
                      AND event_version = %s
                      AND state IN ('queued', 'running')
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (scope.household_space_id, event_id, expected_version),
                ).fetchone()
                if existing is not None:
                    return _job(existing)
                row = connection.execute(
                    """
                    INSERT INTO medical_event_structuring_jobs (
                      household_space_id, medical_event_id, event_version, state,
                      structurer_version, available_at, max_attempts
                    ) VALUES (%s, %s, %s, 'queued', %s, clock_timestamp(), 3)
                    RETURNING *
                    """,
                    (
                        scope.household_space_id,
                        event_id,
                        expected_version,
                        STRUCTURER_VERSION,
                    ),
                ).fetchone()
        except MedicalEventNotFound, DecisionInvalid, VersionConflict:
            raise
        except psycopg.Error:
            raise DecisionRepositoryUnavailable from None
        if row is None:
            raise DecisionRepositoryUnavailable
        return _job(row)

    def get_job(self, scope: HouseholdScope, job_id: UUID) -> StructuringJob:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    SELECT job.*,
                           version.facts_json,
                           version.questions_json,
                           version.issue_codes_json
                    FROM medical_event_structuring_jobs AS job
                    JOIN medical_events AS event
                      ON event.id = job.medical_event_id
                     AND event.household_space_id = job.household_space_id
                     AND event.deleted_at IS NULL
                    LEFT JOIN LATERAL (
                      SELECT facts_json, questions_json, issue_codes_json
                      FROM medical_event_fact_versions
                      WHERE structuring_job_id = job.id
                        AND household_space_id = job.household_space_id
                      ORDER BY version DESC, id DESC
                      LIMIT 1
                    ) AS version ON true
                    WHERE job.id = %s AND job.household_space_id = %s
                    """,
                    (job_id, scope.household_space_id),
                ).fetchone()
        except psycopg.Error:
            raise DecisionRepositoryUnavailable from None
        if row is None:
            raise MedicalEventNotFound
        return _job(row)

    def apply_user_override(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        *,
        expected_version: int,
        facts: Mapping[FactFieldId, str | bool | None],
    ) -> MedicalEvent:
        from familycare_api.decisions.repository import DecisionRepository

        return DecisionRepository(self.database_url).update_medical_event(
            scope,
            event_id,
            expected_version=expected_version,
            structured_facts=facts,
        )


def _job(row: Mapping[str, Any]) -> StructuringJob:
    try:
        state = row["state"]
        attempts = row["attempts"]
        error_code = row.get("error_code")
        if state not in _JOB_STATES:
            raise ValueError
        if isinstance(attempts, bool) or not isinstance(attempts, int) or not 0 <= attempts <= 10:
            raise ValueError
        if error_code is not None and error_code not in _ERROR_CODES:
            raise ValueError
        return StructuringJob(
            id=cast(UUID, row["id"]),
            medical_event_id=cast(UUID, row["medical_event_id"]),
            event_version=int(row["event_version"]),
            state=cast(StructuringJobState, state),
            attempts=attempts,
            facts=_facts(row.get("facts_json")),
            questions=_questions(row.get("questions_json")),
            issues=_issues(row.get("issue_codes_json")),
            error_code=cast(StructuringErrorCode | None, error_code),
        )
    except KeyError, TypeError, ValueError:
        raise DecisionRepositoryUnavailable from None


def _facts(value: object) -> tuple[StructuredFact, ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping) or len(value) > 32:
        raise DecisionRepositoryUnavailable
    result: list[StructuredFact] = []
    try:
        for field_id in sorted(value):
            raw = value[field_id]
            if field_id not in _FACT_FIELDS or not isinstance(raw, Mapping):
                raise ValueError
            source = raw.get("source")
            state = raw.get("state")
            confidence = raw.get("confidence")
            raw_value = raw.get("value")
            raw_evidence = raw.get("evidence_ids", [])
            if (
                source not in _FACT_SOURCES
                or state not in _FACT_STATES
                or confidence not in _FACT_CONFIDENCE
                or not isinstance(raw_evidence, list)
                or len(raw_evidence) > 8
                or not is_valid_structured_fact_value(field_id, raw_value)
            ):
                raise ValueError
            result.append(
                StructuredFact(
                    fact_id=UUID(str(raw["fact_id"])),
                    field_id=cast(FactFieldId, field_id),
                    value=raw_value,
                    source=cast(FactSource, source),
                    state=cast(FactState, state),
                    confidence=cast(FactConfidence, confidence),
                    evidence_ids=tuple(UUID(str(item)) for item in raw_evidence),
                )
            )
    except KeyError, TypeError, ValueError:
        raise DecisionRepositoryUnavailable from None
    return tuple(result)


def _questions(value: object) -> tuple[OptionalQuestion, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 16:
        raise DecisionRepositoryUnavailable
    result: list[OptionalQuestion] = []
    try:
        for raw in value:
            if not isinstance(raw, Mapping):
                raise ValueError
            question_code = raw.get("question_code")
            field_id = raw.get("field_id")
            if question_code not in _FACT_FIELDS or field_id not in _FACT_FIELDS:
                raise ValueError
            result.append(
                OptionalQuestion(
                    question_code=cast(FactFieldId, question_code),
                    field_id=cast(FactFieldId, field_id),
                )
            )
    except TypeError, ValueError:
        raise DecisionRepositoryUnavailable from None
    return tuple(result)


def _issues(value: object) -> tuple[FactIssue, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 16:
        raise DecisionRepositoryUnavailable
    allowed = {
        "INVENTED_FIELD",
        "INVALID_VALUE",
        "INVALID_STATE",
        "DUPLICATE_FIELD",
        "INVENTED_QUESTION",
        "INVENTED_EVIDENCE",
        "UNSUPPORTED_SOURCE",
        "INVALID_CONFIDENCE",
    }
    result: list[FactIssue] = []
    try:
        for raw in value:
            if not isinstance(raw, Mapping) or set(raw) != {"code"}:
                raise ValueError
            code = raw.get("code")
            if code not in allowed:
                raise ValueError
            result.append(FactIssue(code=cast(FactIssueCode, code)))
    except TypeError, ValueError:
        raise DecisionRepositoryUnavailable from None
    return tuple(result)


def _merge_user_overrides(
    current_facts: object,
    current_questions: object,
    overrides: Mapping[FactFieldId, str | bool | None],
) -> tuple[dict[str, dict[str, object]], list[dict[str, str]], tuple[str, ...], bool]:
    """Create a user-owned projection while preserving the prior version as parent."""

    if not overrides or len(overrides) > 32:
        raise DecisionInvalid
    facts: dict[str, dict[str, object]] = {
        str(fact.field_id): {
            "fact_id": str(fact.fact_id),
            "value": fact.value,
            "source": fact.source,
            "state": fact.state,
            "confidence": fact.confidence,
            "evidence_ids": [str(item) for item in fact.evidence_ids],
        }
        for fact in _facts(current_facts)
    }
    questions: list[dict[str, str]] = [
        {"question_code": item.question_code, "field_id": item.field_id}
        for item in _questions(current_questions)
    ]
    changed: list[str] = []
    conflict = False
    for field_id, value in overrides.items():
        if field_id not in _FACT_FIELDS:
            raise DecisionInvalid
        if not is_valid_structured_fact_value(field_id, value):
            raise DecisionInvalid
        previous = facts.get(field_id)
        if previous is not None and previous.get("value") != value:
            conflict = True
        facts[field_id] = {
            "fact_id": str(uuid4()),
            "value": value,
            "source": "user",
            "state": "missing" if value is None else "confirmed",
            "confidence": "high",
            "evidence_ids": [],
        }
        changed.append(field_id)
    changed_set = set(changed)
    remaining_questions = [
        question for question in questions if question["field_id"] not in changed_set
    ]
    return facts, remaining_questions, tuple(sorted(changed_set)), conflict


__all__ = [
    "EventStructuringRepository",
    "STRUCTURER_VERSION",
]
