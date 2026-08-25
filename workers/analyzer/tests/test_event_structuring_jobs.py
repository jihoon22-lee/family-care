"""TDD contract tests for the isolated MedicalEvent structuring queue."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import psycopg
import pytest
from familycare_worker.ai.event_structurer import (
    EventStructuringPayloadInvalid,
    EventStructuringResult,
    FactValidationIssue,
    OptionalQuestion,
    StructuredFactCandidate,
)
from familycare_worker.ai.provider import (
    ProviderConfigurationError,
    RetryableProviderError,
)
from familycare_worker.event_jobs import (
    STRUCTURING_ERROR_CODES,
    EventStructuringJobQueue,
    EventStructuringJobRecord,
    StructuringErrorCode,
    StructuringJobState,
    _project_confirmed_facts,
    map_structuring_error,
)
from familycare_worker.runner import EventStructuringJobRunner
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

EVENT_ID = UUID("00000000-0000-4000-8000-000000000201")
JOB_ID = UUID("00000000-0000-4000-8000-000000000301")
HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000000101")
FACT_ID = UUID("00000000-0000-4000-8000-000000000401")


def _job(*, event_version: int = 3) -> EventStructuringJobRecord:
    return EventStructuringJobRecord(
        id=JOB_ID,
        household_space_id=HOUSEHOLD_ID,
        medical_event_id=EVENT_ID,
        event_version=event_version,
        state="running",
        attempts=1,
        max_attempts=10,
        error_code=None,
        available_at=datetime.now(UTC),
        lease_owner="worker-a",
        lease_expires_at=datetime.now(UTC),
        heartbeat_at=datetime.now(UTC),
        provider_request_id=None,
        mode="pre_visit",
        situation="Synthetic event situation",
        event_date=date(2026, 8, 25),
        visit_date=None,
    )


def _result() -> EventStructuringResult:
    return EventStructuringResult(
        facts=(
            StructuredFactCandidate(
                fact_id=FACT_ID,
                field_id="condition_class",
                value="synthetic-condition",
                source="ai",
                state="confirmed",
                confidence="medium",
            ),
        ),
        questions=(OptionalQuestion(question_code="admission", field_id="admission"),),
        provider_request_id="synthetic-provider-request-001",
        issues=(FactValidationIssue(field_id="admission", code="INVALID_VALUE"),),
    )


class FakeQueue:
    def __init__(self, job: EventStructuringJobRecord) -> None:
        self.job = job
        self.cancelled = False
        self.completed: tuple[EventStructuringJobRecord, EventStructuringResult] | None = None
        self.failed: tuple[UUID, str] | None = None
        self.heartbeats = 0

    def claim_next_job(
        self,
        worker_id: str,
        *,
        lease_seconds: int | None = None,
    ) -> EventStructuringJobRecord | None:
        del worker_id, lease_seconds
        if self.completed is not None:
            return None
        return self.job

    def heartbeat(self, job_id: UUID, worker_id: str) -> bool:
        assert job_id == self.job.id
        assert worker_id == "worker-a"
        self.heartbeats += 1
        return True

    def cancel_if_event_version_changed(
        self,
        job: EventStructuringJobRecord,
        worker_id: str,
    ) -> bool:
        del job, worker_id
        return self.cancelled

    def complete_job(
        self,
        job: EventStructuringJobRecord,
        worker_id: str,
        result: EventStructuringResult,
    ) -> bool:
        assert worker_id == "worker-a"
        self.completed = (job, result)
        return True

    def fail_job(
        self,
        job_id: UUID,
        worker_id: str,
        error_code: StructuringErrorCode,
    ) -> StructuringJobState:
        assert job_id == self.job.id
        assert worker_id == "worker-a"
        self.failed = (job_id, error_code)
        return "retryable_failed"


class FakeProvider:
    def __init__(self, response: Mapping[str, object] | BaseException) -> None:
        self.response = response
        self.requests: list[Mapping[str, object]] = []

    def complete(
        self,
        *,
        model: str,
        schema_name: str,
        system_instruction: str,
        input_payload: Mapping[str, object],
    ) -> Any:
        del model, schema_name, system_instruction
        self.requests.append(dict(input_payload))
        if isinstance(self.response, BaseException):
            raise self.response
        from familycare_worker.ai.provider import ProviderResponse

        return ProviderResponse(payload=self.response, request_id="synthetic-provider-request-001")


def _provider_payload() -> dict[str, object]:
    return {
        "schema_version": "1",
        "facts": [
            {
                "fact_id": str(FACT_ID),
                "field_id": "condition_class",
                "value": "synthetic-condition",
                "source": "ai",
                "state": "confirmed",
                "confidence": "medium",
                "evidence_ids": [],
            },
            {
                "fact_id": "00000000-0000-4000-8000-000000000402",
                "field_id": "event_date",
                "value": "2026-08-25",
                "source": "ai",
                "state": "confirmed",
                "confidence": "high",
                "evidence_ids": [],
            },
            {
                "fact_id": "00000000-0000-4000-8000-000000000403",
                "field_id": "visit_date",
                "value": "2026-08-26",
                "source": "ai",
                "state": "confirmed",
                "confidence": "medium",
                "evidence_ids": [],
            },
            {
                "fact_id": "00000000-0000-4000-8000-000000000404",
                "field_id": "admission",
                "value": False,
                "source": "ai",
                "state": "confirmed",
                "confidence": "low",
                "evidence_ids": [],
            },
            {
                "fact_id": "00000000-0000-4000-8000-000000000405",
                "field_id": "outpatient",
                "value": "yes",
                "source": "ai",
                "state": "confirmed",
                "confidence": "low",
                "evidence_ids": [],
            },
        ],
        "questions": [{"question_code": "admission", "field_id": "admission"}],
    }


def test_structuring_error_codes_match_the_transport_contract() -> None:
    assert (
        frozenset(
            {
                "STRUCTURING_AUTHENTICATION_FAILED",
                "STRUCTURING_INVALID_RESPONSE",
                "STRUCTURING_PROVIDER_TIMEOUT",
                "STRUCTURING_RATE_LIMITED",
                "STRUCTURING_UNAVAILABLE",
            }
        )
        == STRUCTURING_ERROR_CODES
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (ProviderConfigurationError(), "STRUCTURING_AUTHENTICATION_FAILED"),
        (EventStructuringPayloadInvalid(), "STRUCTURING_INVALID_RESPONSE"),
        (RetryableProviderError(), "STRUCTURING_PROVIDER_TIMEOUT"),
        (TimeoutError("synthetic-timeout"), "STRUCTURING_PROVIDER_TIMEOUT"),
        (RuntimeError("synthetic-provider-failure"), "STRUCTURING_UNAVAILABLE"),
    ),
)
def test_structuring_error_mapping_never_exposes_exception_text(
    error: BaseException,
    expected: str,
) -> None:
    assert map_structuring_error(error) == expected
    assert "synthetic" not in map_structuring_error(error)


def test_runner_calls_provider_with_only_bounded_event_context_and_persists_validated_result() -> (
    None
):
    queue = FakeQueue(_job())
    provider = FakeProvider(_provider_payload())
    runner = EventStructuringJobRunner(
        queue=queue,
        provider=provider,
        structurer_model="synthetic-model",
    )

    assert runner.run_once("worker-a") is True
    assert len(provider.requests) == 1
    assert provider.requests[0] == {
        "schema_version": "1",
        "situation": "Synthetic event situation",
        "mode": "pre_visit",
        "event_date": "2026-08-25",
        "visit_date": None,
    }
    assert queue.completed is not None
    persisted_job, result = queue.completed
    assert persisted_job.id == JOB_ID
    assert result.facts[0].field_id == "condition_class"
    assert result.questions[0].field_id == "admission"
    assert result.issues[0].code == "INVALID_VALUE"
    assert "Synthetic event situation" not in repr(result)


def test_runner_cancels_before_provider_call_when_event_version_drifted() -> None:
    queue = FakeQueue(_job())
    queue.cancelled = True
    provider = FakeProvider(_provider_payload())
    runner = EventStructuringJobRunner(queue=queue, provider=provider, structurer_model="synthetic")

    assert runner.run_once("worker-a") is True
    assert provider.requests == []
    assert queue.completed is None
    assert queue.failed is None


def test_runner_maps_provider_failures_to_safe_queue_errors() -> None:
    queue = FakeQueue(_job())
    provider = FakeProvider(ProviderConfigurationError())
    runner = EventStructuringJobRunner(queue=queue, provider=provider, structurer_model="synthetic")

    assert runner.run_once("worker-a") is True
    assert queue.failed == (JOB_ID, "STRUCTURING_AUTHENTICATION_FAILED")


def test_confirmed_event_facts_project_only_supported_values_and_preserve_users() -> None:
    result = EventStructuringResult(
        facts=(
            StructuredFactCandidate(
                fact_id=FACT_ID,
                field_id="condition_class",
                value="synthetic-ai-condition",
                source="ai",
                state="confirmed",
                confidence="high",
            ),
            StructuredFactCandidate(
                fact_id=UUID("00000000-0000-4000-8000-000000000402"),
                field_id="admission",
                value=False,
                source="ai",
                state="confirmed",
                confidence="medium",
            ),
            StructuredFactCandidate(
                fact_id=UUID("00000000-0000-4000-8000-000000000403"),
                field_id="event_date",
                value="2026-08-25",
                source="ai",
                state="confirmed",
                confidence="high",
            ),
            StructuredFactCandidate(
                fact_id=UUID("00000000-0000-4000-8000-000000000404"),
                field_id="visit_date",
                value="2026-08-26",
                source="ai",
                state="confirmed",
                confidence="medium",
            ),
            StructuredFactCandidate(
                fact_id=UUID("00000000-0000-4000-8000-000000000405"),
                field_id="diagnosis_label",
                value="synthetic-diagnosis",
                source="ai",
                state="ambiguous",
                confidence="low",
            ),
        ),
        questions=(),
        provider_request_id=None,
    )

    facts, confirmations, event_date, visit_date, changed_fields = _project_confirmed_facts(
        result,
        facts={"MedicalEvent.classification": "synthetic-user-condition"},
        confirmations={"MedicalEvent.classification": "user"},
        event_date=None,
        visit_date=None,
    )

    assert facts == {
        "MedicalEvent.classification": "synthetic-user-condition",
        "MedicalEvent.admission_days": 0,
    }
    assert confirmations == {
        "MedicalEvent.classification": "user",
        "MedicalEvent.admission_days": "ai_structured",
    }
    assert event_date == date(2026, 8, 25)
    assert visit_date == date(2026, 8, 26)
    assert changed_fields == (
        "MedicalEvent.admission_days",
        "MedicalEvent.event_date",
        "MedicalEvent.visit_date",
    )


def test_queue_rejects_invalid_database_and_lease_configuration() -> None:
    with pytest.raises(ValueError):
        EventStructuringJobQueue("")
    with pytest.raises(ValueError):
        EventStructuringJobQueue("postgresql://synthetic", default_lease_seconds=0)


def test_completed_job_reads_do_not_require_the_claimed_event_version() -> None:
    read_sql = EventStructuringJobQueue._select_sql()
    claim_sql = EventStructuringJobQueue._select_sql(require_event_version=True)

    assert "event.version = job.event_version" not in read_sql
    assert "event.version = job.event_version" in claim_sql


def _psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture()
def seeded_database() -> Any:
    database_url = os.environ.get("FAMILYCARE_DATABASE_URL")
    if not database_url:
        pytest.skip("FAMILYCARE_DATABASE_URL is required for PostgreSQL integration tests")
    household_id = UUID("00000000-0000-4000-8000-000000000501")
    member_id = UUID("00000000-0000-4000-8000-000000000502")
    event_id = UUID("00000000-0000-4000-8000-000000000503")
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            "DELETE FROM medical_event_fact_audit WHERE household_space_id = %s", (household_id,)
        )
        connection.execute(
            "DELETE FROM medical_event_fact_versions WHERE household_space_id = %s", (household_id,)
        )
        connection.execute(
            "DELETE FROM medical_event_structuring_jobs WHERE household_space_id = %s",
            (household_id,),
        )
        connection.execute(
            "DELETE FROM medical_events WHERE household_space_id = %s", (household_id,)
        )
        connection.execute(
            "DELETE FROM family_members WHERE household_space_id = %s", (household_id,)
        )
        connection.execute("DELETE FROM household_spaces WHERE id = %s", (household_id,))
        connection.execute(
            """
            INSERT INTO household_spaces (id, space_key, display_name)
            VALUES (%s, 'synthetic-event-queue', 'Synthetic Event Queue')
            """,
            (household_id,),
        )
        connection.execute(
            """
            INSERT INTO family_members (id, household_space_id, display_name, internal_alias)
            VALUES (%s, %s, 'Synthetic Member', 'synthetic-member')
            """,
            (member_id, household_id),
        )
        connection.execute(
            """
            INSERT INTO medical_events (
              id, household_space_id, family_member_id, mode, situation_text,
              facts_json, confirmation_json, version
            ) VALUES (%s, %s, %s, 'pre_visit', 'Synthetic event situation', %s, %s, 1)
            """,
            (event_id, household_id, member_id, Jsonb({}), Jsonb({})),
        )
    try:
        yield database_url, household_id, event_id
    finally:
        with psycopg.connect(_psycopg_url(database_url)) as connection:
            connection.execute(
                "DELETE FROM medical_event_fact_audit WHERE household_space_id = %s",
                (household_id,),
            )
            connection.execute(
                "DELETE FROM medical_event_fact_versions WHERE household_space_id = %s",
                (household_id,),
            )
            connection.execute(
                "DELETE FROM medical_event_structuring_jobs WHERE household_space_id = %s",
                (household_id,),
            )
            connection.execute(
                "DELETE FROM medical_events WHERE household_space_id = %s", (household_id,)
            )
            connection.execute(
                "DELETE FROM family_members WHERE household_space_id = %s", (household_id,)
            )
            connection.execute("DELETE FROM household_spaces WHERE id = %s", (household_id,))


@pytest.mark.integration
def test_postgres_queue_claims_heartbeats_and_retries(seeded_database: Any) -> None:
    database_url, household_id, event_id = seeded_database
    job_id = UUID("00000000-0000-4000-8000-000000000504")
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO medical_event_structuring_jobs (
              id, household_space_id, medical_event_id, event_version,
              state, structurer_version, available_at, max_attempts
            ) VALUES (%s, %s, %s, 1, 'queued', 'synthetic-event-v1', clock_timestamp(), 3)
            """,
            (job_id, household_id, event_id),
        )
    queue = EventStructuringJobQueue(database_url, default_lease_seconds=30)
    claimed = queue.claim_next_job("worker-a")
    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.attempts == 1
    assert queue.heartbeat(job_id, "worker-b") is False
    assert queue.heartbeat(job_id, "worker-a") is True
    assert queue.fail_job(job_id, "worker-a", "STRUCTURING_PROVIDER_TIMEOUT") == "retryable_failed"
    stored = queue.get_job(job_id)
    assert stored is not None
    assert stored.state == "retryable_failed"
    assert stored.error_code == "STRUCTURING_PROVIDER_TIMEOUT"


@pytest.mark.integration
def test_postgres_runner_persists_validated_facts_and_not_raw_situation(
    seeded_database: Any,
) -> None:
    database_url, household_id, event_id = seeded_database
    job_id = UUID("00000000-0000-4000-8000-000000000505")
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO medical_event_structuring_jobs (
              id, household_space_id, medical_event_id, event_version,
              state, structurer_version, available_at, max_attempts
            ) VALUES (%s, %s, %s, 1, 'queued', 'synthetic-event-v1', clock_timestamp(), 3)
            """,
            (job_id, household_id, event_id),
        )
    queue = EventStructuringJobQueue(database_url, default_lease_seconds=30)
    runner = EventStructuringJobRunner(
        queue=queue,
        provider=FakeProvider(_provider_payload()),
        structurer_model="synthetic",
    )
    assert runner.run_once("worker-a") is True
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        job = connection.execute(
            """
            SELECT state, error_code, provider_request_id
            FROM medical_event_structuring_jobs
            WHERE id = %s
            """,
            (job_id,),
        ).fetchone()
        version = connection.execute(
            """
            SELECT event_version, facts_json, questions_json, issue_codes_json
            FROM medical_event_fact_versions
            WHERE structuring_job_id = %s
            """,
            (job_id,),
        ).fetchone()
        event = connection.execute(
            """
            SELECT version, event_date, visit_date, facts_json, confirmation_json
            FROM medical_events
            WHERE id = %s
            """,
            (event_id,),
        ).fetchone()
        audit = connection.execute(
            """
            SELECT event_version, changed_fields_json, reason_code
            FROM medical_event_fact_audit
            WHERE fact_version_id = (
              SELECT id
              FROM medical_event_fact_versions
              WHERE structuring_job_id = %s
            )
            """,
            (job_id,),
        ).fetchone()
    assert job == {
        "state": "succeeded",
        "error_code": None,
        "provider_request_id": "synthetic-provider-request-001",
    }
    assert version is not None
    assert "Synthetic event situation" not in str(version)
    assert version["event_version"] == 2
    assert version["facts_json"]["condition_class"]["state"] == "confirmed"
    assert version["facts_json"]["admission"]["value"] is False
    assert version["facts_json"]["event_date"]["value"] == "2026-08-25"
    assert version["questions_json"] == [{"question_code": "admission", "field_id": "admission"}]
    assert version["issue_codes_json"] == [{"code": "INVALID_VALUE"}]
    assert event == {
        "version": 2,
        "event_date": date(2026, 8, 25),
        "visit_date": date(2026, 8, 26),
        "facts_json": {
            "MedicalEvent.admission_days": 0,
            "MedicalEvent.classification": "synthetic-condition",
        },
        "confirmation_json": {
            "MedicalEvent.admission_days": "ai_structured",
            "MedicalEvent.classification": "ai_structured",
        },
    }
    assert audit is not None
    assert audit["event_version"] == 2
    assert audit["changed_fields_json"] == [
        "admission",
        "condition_class",
        "event_date",
        "visit_date",
    ]
    assert audit["reason_code"] == "STRUCTURING_COMPLETED"
    completed = queue.get_job(job_id)
    assert completed is not None
    assert completed.state == "succeeded"
    assert completed.event_version == 1


@pytest.mark.integration
def test_postgres_runner_preserves_user_confirmed_event_facts(seeded_database: Any) -> None:
    database_url, household_id, event_id = seeded_database
    job_id = UUID("00000000-0000-4000-8000-000000000506")
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            """
            UPDATE medical_events
            SET facts_json = %s, confirmation_json = %s
            WHERE id = %s AND household_space_id = %s
            """,
            (
                Jsonb(
                    {
                        "MedicalEvent.classification": "synthetic-user-condition",
                        "MedicalEvent.admission_days": 4,
                    }
                ),
                Jsonb(
                    {
                        "MedicalEvent.classification": "user",
                        "MedicalEvent.admission_days": "user",
                    }
                ),
                event_id,
                household_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO medical_event_structuring_jobs (
              id, household_space_id, medical_event_id, event_version,
              state, structurer_version, available_at, max_attempts
            ) VALUES (%s, %s, %s, 1, 'queued', 'synthetic-event-v1', clock_timestamp(), 3)
            """,
            (job_id, household_id, event_id),
        )
    queue = EventStructuringJobQueue(database_url, default_lease_seconds=30)
    runner = EventStructuringJobRunner(
        queue=queue,
        provider=FakeProvider(_provider_payload()),
        structurer_model="synthetic",
    )

    assert runner.run_once("worker-a") is True
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        event = connection.execute(
            """
            SELECT version, facts_json, confirmation_json
            FROM medical_events
            WHERE id = %s
            """,
            (event_id,),
        ).fetchone()
        fact_version = connection.execute(
            """
            SELECT event_version
            FROM medical_event_fact_versions
            WHERE structuring_job_id = %s
            """,
            (job_id,),
        ).fetchone()

    assert event == {
        "version": 2,
        "facts_json": {
            "MedicalEvent.classification": "synthetic-user-condition",
            "MedicalEvent.admission_days": 4,
        },
        "confirmation_json": {
            "MedicalEvent.classification": "user",
            "MedicalEvent.admission_days": "user",
        },
    }
    assert fact_version == {"event_version": 2}
