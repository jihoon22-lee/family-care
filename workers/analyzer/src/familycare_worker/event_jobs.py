"""Transactional PostgreSQL queue for non-authoritative event structuring."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Literal, Protocol, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from familycare_worker.ai.event_structurer import (
    EventFactField,
    EventStructuringPayloadInvalid,
    EventStructuringProviderError,
    EventStructuringResult,
)
from familycare_worker.ai.provider import (
    ProviderBoundaryError,
    ProviderConfigurationError,
    ProviderValidationError,
    RetryableProviderError,
)
from familycare_worker.jobs import psycopg_database_url

DEFAULT_STRUCTURING_LEASE_SECONDS = 180
MAX_STRUCTURING_LEASE_SECONDS = 3_600
MAX_STRUCTURING_BACKOFF_SECONDS = 300
MAX_STRUCTURING_ATTEMPTS = 10
# The browser falls back to deterministic local analysis after 60 seconds. A
# provider result must become terminal before that boundary so it can never
# arrive later and replace the event version used by the local result.
MAX_STRUCTURING_JOB_AGE_SECONDS = 55
_WORKER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PROVIDER_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_STRUCTURING_ERROR_CODES = frozenset(
    {
        "STRUCTURING_AUTHENTICATION_FAILED",
        "STRUCTURING_INVALID_RESPONSE",
        "STRUCTURING_PROVIDER_TIMEOUT",
        "STRUCTURING_RATE_LIMITED",
        "STRUCTURING_UNAVAILABLE",
    }
)
STRUCTURING_ERROR_CODES = _STRUCTURING_ERROR_CODES
type StructuringErrorCode = Literal[
    "STRUCTURING_AUTHENTICATION_FAILED",
    "STRUCTURING_INVALID_RESPONSE",
    "STRUCTURING_PROVIDER_TIMEOUT",
    "STRUCTURING_RATE_LIMITED",
    "STRUCTURING_UNAVAILABLE",
]
type StructuringJobState = Literal[
    "queued",
    "running",
    "succeeded",
    "retryable_failed",
    "permanently_failed",
    "cancelled",
]
_NORMALIZATION_HINT_FIELDS = frozenset(
    {
        "condition_class",
        "diagnosis_code",
        "procedure_code",
        "anatomical_site_code",
        "pathology_code",
        "treatment_setting",
        "treatment_context",
        "separately_billed_treatment",
    }
)


class StructuringJobQueueError(RuntimeError):
    """Base queue error whose message contains no database or input data."""


class StructuringJobNotFound(StructuringJobQueueError):
    def __init__(self) -> None:
        super().__init__("STRUCTURING_JOB_NOT_FOUND")


class StructuringJobStateConflict(StructuringJobQueueError):
    def __init__(self) -> None:
        super().__init__("STRUCTURING_JOB_STATE_CONFLICT")


class InvalidStructuringJob(StructuringJobQueueError):
    def __init__(self) -> None:
        super().__init__("INVALID_STRUCTURING_JOB")


@dataclass(frozen=True, slots=True)
class EventStructuringJobRecord:
    """Validated queue row plus the short-lived event input context."""

    id: UUID
    household_space_id: UUID
    medical_event_id: UUID
    event_version: int
    state: StructuringJobState
    attempts: int
    max_attempts: int
    error_code: StructuringErrorCode | None
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    provider_request_id: str | None
    mode: Literal["pre_visit", "post_treatment"] = field(repr=False)
    situation: str = field(repr=False)
    event_date: date | None = field(repr=False)
    visit_date: date | None = field(repr=False)
    normalization_hints: Mapping[EventFactField, tuple[str | bool, ...]] = field(
        default_factory=dict,
        repr=False,
    )


class EventStructuringQueue(Protocol):
    """Minimal queue boundary used by the event runner and its fake tests."""

    def claim_next_job(
        self,
        worker_id: str,
        *,
        lease_seconds: int | None = None,
    ) -> EventStructuringJobRecord | None: ...

    def heartbeat(self, job_id: UUID, worker_id: str) -> bool: ...

    def cancel_if_event_version_changed(
        self,
        job: EventStructuringJobRecord,
        worker_id: str,
    ) -> bool: ...

    def complete_job(
        self,
        job: EventStructuringJobRecord,
        worker_id: str,
        result: EventStructuringResult,
    ) -> bool: ...

    def fail_job(
        self,
        job_id: UUID,
        worker_id: str,
        error_code: StructuringErrorCode,
    ) -> StructuringJobState: ...


def map_structuring_error(error: BaseException) -> StructuringErrorCode:
    """Map provider failures to the closed transport error vocabulary."""

    if isinstance(error, ProviderConfigurationError):
        return "STRUCTURING_AUTHENTICATION_FAILED"
    if isinstance(error, EventStructuringPayloadInvalid | ProviderValidationError):
        return "STRUCTURING_INVALID_RESPONSE"
    if isinstance(error, RetryableProviderError | TimeoutError):
        return "STRUCTURING_PROVIDER_TIMEOUT"
    if isinstance(error, (EventStructuringProviderError, ProviderBoundaryError)):
        return "STRUCTURING_UNAVAILABLE"
    return "STRUCTURING_UNAVAILABLE"


def _validate_worker_id(worker_id: str) -> str:
    if not isinstance(worker_id, str) or _WORKER_ID_PATTERN.fullmatch(worker_id) is None:
        raise ValueError("invalid worker identity")
    return worker_id


def _validate_lease_seconds(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_STRUCTURING_LEASE_SECONDS
    ):
        raise ValueError("invalid lease duration")
    return value


def _error_code(value: object) -> StructuringErrorCode | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in _STRUCTURING_ERROR_CODES:
        raise InvalidStructuringJob
    return cast(StructuringErrorCode, value)


def _row_to_job(row: Mapping[str, object]) -> EventStructuringJobRecord:
    try:
        job_id = row["id"]
        household_space_id = row["household_space_id"]
        event_id = row["medical_event_id"]
        event_version = row["event_version"]
        state = row["state"]
        attempts = row["attempts"]
        max_attempts = row["max_attempts"]
        available_at = row["available_at"]
        mode = row["mode"]
        situation = row["situation_text"]
        if not all(isinstance(value, UUID) for value in (job_id, household_space_id, event_id)):
            raise InvalidStructuringJob
        if (
            isinstance(event_version, bool)
            or not isinstance(event_version, int)
            or event_version < 1
            or state
            not in {
                "queued",
                "running",
                "succeeded",
                "retryable_failed",
                "permanently_failed",
                "cancelled",
            }
        ):
            raise InvalidStructuringJob
        if (
            isinstance(attempts, bool)
            or not isinstance(attempts, int)
            or isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or not 0 <= attempts <= max_attempts <= MAX_STRUCTURING_ATTEMPTS
        ):
            raise InvalidStructuringJob
        if not isinstance(available_at, datetime):
            raise InvalidStructuringJob
        if mode not in {"pre_visit", "post_treatment"}:
            raise InvalidStructuringJob
        if not isinstance(situation, str) or not situation.strip() or len(situation) > 2_000:
            raise InvalidStructuringJob
        provider_request_id = row.get("provider_request_id")
        if provider_request_id is not None and (
            not isinstance(provider_request_id, str)
            or not 1 <= len(provider_request_id) <= 128
            or _PROVIDER_REQUEST_ID_PATTERN.fullmatch(provider_request_id) is None
        ):
            raise InvalidStructuringJob
        return EventStructuringJobRecord(
            id=cast(UUID, job_id),
            household_space_id=cast(UUID, household_space_id),
            medical_event_id=cast(UUID, event_id),
            event_version=event_version,
            state=state,
            attempts=attempts,
            max_attempts=max_attempts,
            error_code=_error_code(row.get("error_code")),
            available_at=available_at,
            lease_owner=cast(str | None, row.get("lease_owner")),
            lease_expires_at=cast(datetime | None, row.get("lease_expires_at")),
            heartbeat_at=cast(datetime | None, row.get("heartbeat_at")),
            provider_request_id=provider_request_id,
            mode=mode,
            situation=situation.strip(),
            event_date=cast(date | None, row.get("event_date")),
            visit_date=cast(date | None, row.get("visit_date")),
        )
    except KeyError, TypeError, ValueError:
        raise InvalidStructuringJob from None


def _project_confirmed_facts(
    result: EventStructuringResult,
    *,
    facts: Mapping[str, object | None],
    confirmations: Mapping[str, object],
    event_date: date | None,
    visit_date: date | None,
) -> tuple[
    dict[str, object | None],
    dict[str, object],
    date | None,
    date | None,
    tuple[str, ...],
]:
    """Project the small set of safe AI facts into the authoritative event.

    The fact-version row retains every validated candidate, including
    ambiguous values.  Only confirmed AI values with a deterministic mapping
    are copied to ``medical_events``; an existing user confirmation always
    wins.  Dates remain candidate facts because projecting an AI date into the
    authoritative event columns would incorrectly upgrade its provenance.
    """

    projected_facts = dict(facts)
    projected_confirmations = dict(confirmations)
    projected_event_date = event_date
    projected_visit_date = visit_date
    changed: set[str] = set()

    for candidate in result.facts:
        if candidate.source != "ai" or candidate.state != "confirmed":
            continue
        if candidate.field_id == "condition_class":
            field = "MedicalEvent.classification"
            if (
                isinstance(candidate.value, str)
                and candidate.value.strip()
                and not _has_user_confirmation(projected_confirmations, field)
            ):
                projected_facts[field] = candidate.value
                projected_confirmations[field] = "ai_structured"
                changed.add(field)
        elif candidate.field_id == "admission":
            field = "MedicalEvent.admission_days"
            if candidate.value is False and not _has_user_confirmation(
                projected_confirmations, field
            ):
                projected_facts[field] = 0
                projected_confirmations[field] = "ai_structured"
                changed.add(field)

    return (
        projected_facts,
        projected_confirmations,
        projected_event_date,
        projected_visit_date,
        tuple(sorted(changed)),
    )


def _has_user_confirmation(confirmations: Mapping[str, object], field: str) -> bool:
    short_field = field.partition(".")[2]
    return confirmations.get(field) == "user" or confirmations.get(short_field) == "user"


class EventStructuringJobQueue:
    """Open short-lived connections for lease-safe event queue operations."""

    def __init__(
        self,
        database_url: str,
        *,
        default_lease_seconds: int = DEFAULT_STRUCTURING_LEASE_SECONDS,
    ) -> None:
        self.database_url = psycopg_database_url(database_url)
        self.default_lease_seconds = _validate_lease_seconds(default_lease_seconds)

    @staticmethod
    def _select_sql(*, require_event_version: bool = False) -> str:
        event_version_clause = (
            "             AND event.version = job.event_version\n" if require_event_version else ""
        )
        return f"""
            SELECT job.*, event.mode, event.situation_text,
                   event.event_date, event.visit_date
            FROM medical_event_structuring_jobs AS job
            JOIN medical_events AS event
              ON event.id = job.medical_event_id
             AND event.household_space_id = job.household_space_id
             AND event.deleted_at IS NULL
{event_version_clause}
            WHERE job.id = %s
        """

    def claim_next_job(
        self,
        worker_id: str,
        *,
        lease_seconds: int | None = None,
    ) -> EventStructuringJobRecord | None:
        """Recover stale work, cancel drifted events, then claim one due job."""

        owner = _validate_worker_id(worker_id)
        lease = _validate_lease_seconds(
            self.default_lease_seconds if lease_seconds is None else lease_seconds
        )
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            connection.execute(
                """
                UPDATE medical_event_structuring_jobs AS job
                SET state = 'cancelled',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    error_code = NULL,
                    updated_at = clock_timestamp()
                WHERE job.state IN ('queued', 'running', 'retryable_failed')
                  AND NOT EXISTS (
                    SELECT 1
                    FROM medical_events AS event
                    WHERE event.id = job.medical_event_id
                      AND event.household_space_id = job.household_space_id
                      AND event.deleted_at IS NULL
                      AND event.version = job.event_version
                  )
                """
            )
            connection.execute(
                """
                UPDATE medical_event_structuring_jobs
                SET state = 'permanently_failed',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    error_code = 'STRUCTURING_PROVIDER_TIMEOUT',
                    completed_at = NULL,
                    updated_at = clock_timestamp()
                WHERE state IN ('queued', 'running', 'retryable_failed')
                  AND created_at + (%s * interval '1 second') <= clock_timestamp()
                """,
                (MAX_STRUCTURING_JOB_AGE_SECONDS,),
            )
            connection.execute(
                """
                UPDATE medical_event_structuring_jobs
                SET state = CASE
                        WHEN attempts >= max_attempts THEN 'permanently_failed'
                        ELSE 'retryable_failed'
                    END,
                    available_at = clock_timestamp(),
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    error_code = 'STRUCTURING_PROVIDER_TIMEOUT',
                    updated_at = clock_timestamp()
                WHERE state = 'running'
                  AND lease_expires_at <= clock_timestamp()
                """
            )
            connection.execute(
                """
                UPDATE medical_event_structuring_jobs
                SET state = 'permanently_failed',
                    error_code = COALESCE(error_code, 'STRUCTURING_UNAVAILABLE'),
                    updated_at = clock_timestamp()
                WHERE state IN ('queued', 'retryable_failed')
                  AND attempts >= max_attempts
                """
            )
            row = connection.execute(
                """
                WITH candidate AS (
                    SELECT job.id
                    FROM medical_event_structuring_jobs AS job
                    JOIN medical_events AS event
                      ON event.id = job.medical_event_id
                     AND event.household_space_id = job.household_space_id
                     AND event.deleted_at IS NULL
                     AND event.version = job.event_version
                    WHERE job.state IN ('queued', 'retryable_failed')
                      AND job.available_at <= clock_timestamp()
                      AND job.attempts < job.max_attempts
                      AND job.created_at + (%s * interval '1 second') > clock_timestamp()
                    ORDER BY job.available_at, job.created_at, job.id
                    FOR UPDATE OF job SKIP LOCKED
                    LIMIT 1
                )
                UPDATE medical_event_structuring_jobs AS job
                SET state = 'running',
                    lease_owner = %s,
                    lease_expires_at = clock_timestamp() + (%s * interval '1 second'),
                    heartbeat_at = clock_timestamp(),
                    attempts = job.attempts + 1,
                    error_code = NULL,
                    updated_at = clock_timestamp()
                FROM candidate
                WHERE job.id = candidate.id
                RETURNING job.id
                """,
                (MAX_STRUCTURING_JOB_AGE_SECONDS, owner, lease),
            ).fetchone()
            if row is None:
                return None
            claimed = connection.execute(
                self._select_sql(require_event_version=True), (row["id"],)
            ).fetchone()
            if claimed is None:
                return None
            try:
                job = _row_to_job(claimed)
                return replace(
                    job,
                    normalization_hints=self._normalization_hints(
                        connection,
                        job.household_space_id,
                    ),
                )
            except InvalidStructuringJob:
                connection.execute(
                    """
                    UPDATE medical_event_structuring_jobs
                    SET state = 'permanently_failed',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        heartbeat_at = NULL,
                        error_code = 'STRUCTURING_INVALID_RESPONSE',
                        updated_at = clock_timestamp()
                    WHERE id = %s
                    """,
                    (row["id"],),
                )
                return None

    @staticmethod
    def _normalization_hints(
        connection: psycopg.Connection[dict[str, object]],
        household_space_id: UUID,
    ) -> Mapping[EventFactField, tuple[str | bool, ...]]:
        rows = connection.execute(
            """
            SELECT normalizer.field_path, normalizer.normalized_value_json
            FROM private_knowledge_fact_normalizer_publications AS normalizer
            JOIN private_knowledge_rule_import_runs AS run
              ON run.id = normalizer.rule_import_run_id
             AND run.knowledge_import_run_id = normalizer.knowledge_import_run_id
             AND run.household_space_id = normalizer.household_space_id
             AND run.state = 'APPLIED'
             AND run.is_current = true
            JOIN private_knowledge_import_runs AS knowledge
              ON knowledge.id = run.knowledge_import_run_id
             AND knowledge.household_space_id = run.household_space_id
             AND knowledge.state = 'APPLIED'
             AND knowledge.is_current = true
            WHERE normalizer.household_space_id = %s
              AND normalizer.review_state IN ('AI_VERIFIED', 'USER_CONFIRMED')
            ORDER BY normalizer.field_path, normalizer.priority DESC, normalizer.id
            LIMIT 512
            """,
            (household_space_id,),
        ).fetchall()
        values: dict[EventFactField, list[str | bool]] = {}
        for row in rows:
            field_path = row.get("field_path")
            raw_value = row.get("normalized_value_json")
            if field_path == "MedicalEvent.classification":
                field_id = "condition_class"
            elif isinstance(field_path, str) and field_path.startswith("MedicalEvent."):
                field_id = field_path.removeprefix("MedicalEvent.")
            else:
                continue
            if field_id not in _NORMALIZATION_HINT_FIELDS or not isinstance(raw_value, str | bool):
                continue
            typed_field = cast(EventFactField, field_id)
            field_values = values.setdefault(typed_field, [])
            if raw_value not in field_values and len(field_values) < 32:
                field_values.append(raw_value)
        return {field_id: tuple(items) for field_id, items in values.items()}

    def heartbeat(
        self,
        job_id: UUID,
        worker_id: str,
        *,
        lease_seconds: int | None = None,
    ) -> bool:
        """Extend a live lease only for its current owner."""

        owner = _validate_worker_id(worker_id)
        lease = _validate_lease_seconds(
            self.default_lease_seconds if lease_seconds is None else lease_seconds
        )
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                """
                UPDATE medical_event_structuring_jobs
                SET heartbeat_at = clock_timestamp(),
                    lease_expires_at = clock_timestamp() + (%s * interval '1 second'),
                    updated_at = clock_timestamp()
                WHERE id = %s
                  AND state = 'running'
                  AND lease_owner = %s
                  AND lease_expires_at > clock_timestamp()
                RETURNING id
                """,
                (lease, job_id, owner),
            ).fetchone()
            return row is not None

    def get_job(self, job_id: UUID) -> EventStructuringJobRecord | None:
        """Read the current scoped event context for a claimed or completed job."""

        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(self._select_sql(), (job_id,)).fetchone()
        if row is None:
            return None
        return _row_to_job(row)

    def cancel_job(
        self,
        job_id: UUID,
        worker_id: str | None = None,
    ) -> StructuringJobState:
        """Cancel a job safely; repeated cancellation is idempotent."""

        owner = _validate_worker_id(worker_id) if worker_id is not None else None
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT state, lease_owner,
                       lease_expires_at > clock_timestamp() AS lease_valid
                FROM medical_event_structuring_jobs
                WHERE id = %s
                FOR UPDATE
                """,
                (job_id,),
            ).fetchone()
            if row is None:
                raise StructuringJobNotFound
            if row["state"] == "succeeded":
                raise StructuringJobStateConflict
            if owner is not None and (
                row["state"] != "running" or row["lease_owner"] != owner or not row["lease_valid"]
            ):
                raise StructuringJobStateConflict
            if row["state"] != "cancelled":
                connection.execute(
                    """
                    UPDATE medical_event_structuring_jobs
                    SET state = 'cancelled',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        heartbeat_at = NULL,
                        error_code = NULL,
                        updated_at = clock_timestamp()
                    WHERE id = %s
                    """,
                    (job_id,),
                )
            return "cancelled"

    def cancel_if_event_version_changed(
        self,
        job: EventStructuringJobRecord,
        worker_id: str,
    ) -> bool:
        """Cancel a live claim when its event no longer has the claimed version."""

        owner = _validate_worker_id(worker_id)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            job_row = connection.execute(
                """
                SELECT state, lease_owner,
                       lease_expires_at > clock_timestamp() AS lease_valid
                FROM medical_event_structuring_jobs
                WHERE id = %s
                FOR UPDATE
                """,
                (job.id,),
            ).fetchone()
            if job_row is None:
                raise StructuringJobNotFound
            if (
                job_row["state"] != "running"
                or job_row["lease_owner"] != owner
                or not job_row["lease_valid"]
            ):
                raise StructuringJobStateConflict
            event_row = connection.execute(
                """
                SELECT version
                FROM medical_events
                WHERE id = %s AND household_space_id = %s AND deleted_at IS NULL
                FOR SHARE
                """,
                (job.medical_event_id, job.household_space_id),
            ).fetchone()
            if event_row is not None and event_row["version"] == job.event_version:
                return False
            self._cancel_locked(connection, job.id)
            return True

    def fail_job(
        self,
        job_id: UUID,
        worker_id: str,
        error_code: StructuringErrorCode,
    ) -> StructuringJobState:
        """Record a sanitized retryable or permanent failure."""

        owner = _validate_worker_id(worker_id)
        if error_code not in _STRUCTURING_ERROR_CODES:
            raise ValueError("invalid structuring error code")
        retryable = error_code in {
            "STRUCTURING_PROVIDER_TIMEOUT",
            "STRUCTURING_RATE_LIMITED",
            "STRUCTURING_UNAVAILABLE",
        }
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT state, lease_owner, lease_expires_at,
                       lease_expires_at > clock_timestamp() AS lease_valid,
                       attempts, max_attempts
                FROM medical_event_structuring_jobs
                WHERE id = %s
                FOR UPDATE
                """,
                (job_id,),
            ).fetchone()
            if row is None:
                raise StructuringJobNotFound
            if row["state"] != "running" or row["lease_owner"] != owner or not row["lease_valid"]:
                raise StructuringJobStateConflict
            will_retry = retryable and row["attempts"] < row["max_attempts"]
            next_state: StructuringJobState = (
                "retryable_failed" if will_retry else "permanently_failed"
            )
            backoff_seconds = min(2 ** int(row["attempts"]), MAX_STRUCTURING_BACKOFF_SECONDS)
            connection.execute(
                """
                UPDATE medical_event_structuring_jobs
                SET state = %s,
                    available_at = CASE
                        WHEN %s THEN clock_timestamp() + (%s * interval '1 second')
                        ELSE available_at
                    END,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    error_code = %s,
                    updated_at = clock_timestamp()
                WHERE id = %s
                """,
                (next_state, will_retry, backoff_seconds, error_code, job_id),
            )
            return next_state

    def complete_job(
        self,
        job: EventStructuringJobRecord,
        worker_id: str,
        result: EventStructuringResult,
    ) -> bool:
        """Persist only validated candidates and finish a live claim atomically."""

        owner = _validate_worker_id(worker_id)
        if not isinstance(result, EventStructuringResult):
            raise ValueError("invalid structuring result")
        facts_json = {
            fact.field_id: {
                "fact_id": str(fact.fact_id),
                "value": fact.value,
                "source": fact.source,
                "state": fact.state,
                "confidence": fact.confidence,
                "evidence_ids": [str(evidence_id) for evidence_id in fact.evidence_ids],
            }
            for fact in result.facts
        }
        questions_json = [
            {"question_code": item.question_code, "field_id": item.field_id}
            for item in result.questions
        ]
        issues_json = [{"code": issue.code} for issue in result.issues]
        changed_fields = sorted(facts_json)
        provider_request_id = result.provider_request_id
        if (
            provider_request_id is not None
            and _PROVIDER_REQUEST_ID_PATTERN.fullmatch(provider_request_id) is None
        ):
            provider_request_id = None
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT state, lease_owner,
                       lease_expires_at > clock_timestamp() AS lease_valid,
                       created_at + (%s * interval '1 second') > clock_timestamp()
                         AS deadline_valid
                FROM medical_event_structuring_jobs
                WHERE id = %s
                FOR UPDATE
                """,
                (MAX_STRUCTURING_JOB_AGE_SECONDS, job.id),
            ).fetchone()
            if row is None:
                raise StructuringJobNotFound
            if row["state"] != "running" or row["lease_owner"] != owner or not row["lease_valid"]:
                raise StructuringJobStateConflict
            if not row["deadline_valid"]:
                self._expire_locked(connection, job.id)
                return False
            event_row = connection.execute(
                """
                SELECT version, event_date, visit_date, facts_json, confirmation_json
                FROM medical_events
                WHERE id = %s AND household_space_id = %s AND deleted_at IS NULL
                FOR UPDATE
                """,
                (job.medical_event_id, job.household_space_id),
            ).fetchone()
            if event_row is None or event_row["version"] != job.event_version:
                self._cancel_locked(connection, job.id)
                return False
            existing_facts = event_row["facts_json"]
            existing_confirmations = event_row["confirmation_json"]
            if not isinstance(existing_facts, Mapping) or not isinstance(
                existing_confirmations, Mapping
            ):
                raise StructuringJobQueueError
            (
                projected_facts,
                projected_confirmations,
                projected_event_date,
                projected_visit_date,
                _projected_fields,
            ) = _project_confirmed_facts(
                result,
                facts=cast(Mapping[str, object | None], existing_facts),
                confirmations=cast(Mapping[str, object], existing_confirmations),
                event_date=cast(date | None, event_row["event_date"]),
                visit_date=cast(date | None, event_row["visit_date"]),
            )
            updated_event = connection.execute(
                """
                UPDATE medical_events
                SET event_date = %s,
                    visit_date = %s,
                    facts_json = %s,
                    confirmation_json = %s,
                    version = version + 1,
                    updated_at = clock_timestamp()
                WHERE id = %s
                  AND household_space_id = %s
                  AND version = %s
                  AND deleted_at IS NULL
                  AND EXISTS (
                    SELECT 1
                    FROM medical_event_structuring_jobs AS live_job
                    WHERE live_job.id = %s
                      AND live_job.created_at + (%s * interval '1 second')
                            > clock_timestamp()
                  )
                RETURNING version
                """,
                (
                    projected_event_date,
                    projected_visit_date,
                    Jsonb(projected_facts),
                    Jsonb(projected_confirmations),
                    job.medical_event_id,
                    job.household_space_id,
                    job.event_version,
                    job.id,
                    MAX_STRUCTURING_JOB_AGE_SECONDS,
                ),
            ).fetchone()
            if updated_event is None:
                self._expire_locked(connection, job.id)
                return False
            new_event_version = int(updated_event["version"])
            previous = connection.execute(
                """
                SELECT id
                FROM medical_event_fact_versions
                WHERE medical_event_id = %s
                  AND household_space_id = %s
                  AND is_current = true
                ORDER BY event_version DESC, version DESC, id DESC
                LIMIT 1
                FOR UPDATE
                """,
                (job.medical_event_id, job.household_space_id),
            ).fetchone()
            connection.execute(
                """
                UPDATE medical_event_fact_versions
                SET is_current = false, version_state = 'superseded'
                WHERE medical_event_id = %s
                  AND household_space_id = %s
                  AND is_current = true
                """,
                (job.medical_event_id, job.household_space_id),
            )
            next_version = connection.execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1 AS next_version
                FROM medical_event_fact_versions
                WHERE medical_event_id = %s AND event_version = %s
                """,
                (job.medical_event_id, new_event_version),
            ).fetchone()
            if next_version is None:
                raise StructuringJobQueueError
            fact_version_id = connection.execute(
                """
                INSERT INTO medical_event_fact_versions (
                  household_space_id, medical_event_id, structuring_job_id,
                  parent_version_id, event_version, version, source, version_state,
                  facts_json, questions_json, issue_codes_json, is_current
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, 'ai', 'candidate', %s, %s, %s, true
                )
                RETURNING id
                """,
                (
                    job.household_space_id,
                    job.medical_event_id,
                    job.id,
                    previous["id"] if previous is not None else None,
                    new_event_version,
                    next_version["next_version"],
                    Jsonb(facts_json),
                    Jsonb(questions_json),
                    Jsonb(issues_json),
                ),
            ).fetchone()
            if fact_version_id is None:
                raise StructuringJobQueueError
            connection.execute(
                """
                INSERT INTO medical_event_fact_audit (
                  household_space_id, medical_event_id, fact_version_id,
                  parent_version_id, event_version, action, actor_kind,
                  changed_fields_json, reason_code
                ) VALUES (%s, %s, %s, %s, %s, 'created', 'ai', %s, %s)
                """,
                (
                    job.household_space_id,
                    job.medical_event_id,
                    fact_version_id["id"],
                    previous["id"] if previous is not None else None,
                    new_event_version,
                    Jsonb(changed_fields),
                    "STRUCTURING_COMPLETED",
                ),
            )
            connection.execute(
                """
                UPDATE medical_event_structuring_jobs
                SET state = 'succeeded',
                    provider_request_id = %s,
                    completed_at = clock_timestamp(),
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    error_code = NULL,
                    updated_at = clock_timestamp()
                WHERE id = %s
                """,
                (provider_request_id, job.id),
            )
            return True

    @staticmethod
    def _cancel_locked(connection: psycopg.Connection[dict[str, object]], job_id: UUID) -> None:
        connection.execute(
            """
            UPDATE medical_event_structuring_jobs
            SET state = 'cancelled',
                lease_owner = NULL,
                lease_expires_at = NULL,
                heartbeat_at = NULL,
                error_code = NULL,
                updated_at = clock_timestamp()
            WHERE id = %s
            """,
            (job_id,),
        )

    @staticmethod
    def _expire_locked(connection: psycopg.Connection[dict[str, object]], job_id: UUID) -> None:
        connection.execute(
            """
            UPDATE medical_event_structuring_jobs
            SET state = 'permanently_failed',
                lease_owner = NULL,
                lease_expires_at = NULL,
                heartbeat_at = NULL,
                error_code = 'STRUCTURING_PROVIDER_TIMEOUT',
                completed_at = NULL,
                updated_at = clock_timestamp()
            WHERE id = %s
            """,
            (job_id,),
        )


__all__ = [
    "DEFAULT_STRUCTURING_LEASE_SECONDS",
    "EventStructuringJobRecord",
    "EventStructuringJobQueue",
    "EventStructuringQueue",
    "InvalidStructuringJob",
    "MAX_STRUCTURING_ATTEMPTS",
    "MAX_STRUCTURING_JOB_AGE_SECONDS",
    "StructuringErrorCode",
    "StructuringJobNotFound",
    "StructuringJobQueueError",
    "StructuringJobState",
    "StructuringJobStateConflict",
    "STRUCTURING_ERROR_CODES",
    "map_structuring_error",
]
