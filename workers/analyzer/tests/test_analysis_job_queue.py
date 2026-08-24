"""PostgreSQL integration coverage for the AnalysisJob queue contract."""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from familycare_worker.jobs import AnalysisJobRecord, JobQueue, JobStateConflict
from familycare_worker.pdf.errors import IntakeErrorCode
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.integration

_INGESTION_TABLES = (
    "analysis_candidate_evidence",
    "analysis_candidate_fields",
    "analysis_candidate_versions",
    "policy_status_snapshots",
    "riders",
    "policy_parties",
    "policy_contracts",
    "evidence",
    "family_members",
    "household_spaces",
    "extraction_cells",
    "extraction_tables",
    "extraction_blocks",
    "extraction_pages",
    "extractions",
    "analysis_jobs",
    "document_versions",
    "documents",
)
_SOURCE_KEY = "synthetic/policy-001.pdf"
_QUEUE_SETTINGS = {
    "document_kind": "policy",
    "extractor_config": {
        "profile": "quality-v1",
        "quality_rule_version": "quality-v1",
        "table_strategy": "lines",
    },
}
_EXTRACTOR_CONFIG_HASH = hashlib.sha256(
    json.dumps(
        _QUEUE_SETTINGS["extractor_config"],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()


def _psycopg_url(database_url: str) -> str:
    """Adapt the shared SQLAlchemy-style URL for direct psycopg setup SQL."""

    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _reset_ingestion_tables(database_url: str) -> None:
    """Reset only Phase 1 ingestion rows while preserving the migration schema."""

    table_list = ", ".join(_INGESTION_TABLES)
    with psycopg.connect(_psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(f"TRUNCATE TABLE {table_list} RESTART IDENTITY")


@pytest.fixture()
def database_url() -> str:
    """Return the configured integration database and isolate the current test."""

    database_url = os.getenv("FAMILYCARE_DATABASE_URL")
    if not database_url:
        pytest.skip("FAMILYCARE_DATABASE_URL is required for PostgreSQL integration tests")
    _reset_ingestion_tables(database_url)
    return database_url


def _seed_job(
    database_url: str,
    *,
    state: str = "queued",
    attempts: int = 0,
    max_attempts: int = 3,
    available_at: datetime | None = None,
    lease_owner: str | None = None,
    lease_expires_at: datetime | None = None,
    heartbeat_at: datetime | None = None,
    source_key: str = _SOURCE_KEY,
) -> UUID:
    """Insert a synthetic document and its password-free queue envelope."""

    document_id = uuid4()
    job_id = uuid4()
    available_at = available_at or datetime.now(UTC)
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO documents (id, source_key, document_kind, status)
            VALUES (%s, %s, %s, 'pending')
            """,
            (document_id, source_key, "policy"),
        )
        connection.execute(
            """
            INSERT INTO analysis_jobs (
                id,
                document_id,
                source_key,
                settings_json,
                extractor_config_hash,
                state,
                available_at,
                lease_owner,
                lease_expires_at,
                heartbeat_at,
                attempts,
                max_attempts
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                job_id,
                document_id,
                source_key,
                Jsonb(_QUEUE_SETTINGS),
                _EXTRACTOR_CONFIG_HASH,
                state,
                available_at,
                lease_owner,
                lease_expires_at,
                heartbeat_at,
                attempts,
                max_attempts,
            ),
        )
    return job_id


def _state(value: Any) -> str:
    """Normalize a string or enum state without permitting arbitrary states."""

    return str(getattr(value, "value", value))


def _required_record(queue: JobQueue, job_id: UUID) -> AnalysisJobRecord:
    record = queue.get_job(job_id)
    assert record is not None
    return cast(AnalysisJobRecord, record)


def _claim(database_url: str, worker_id: str, lease_seconds: int) -> AnalysisJobRecord | None:
    queue = JobQueue(database_url, default_lease_seconds=lease_seconds)
    return queue.claim_next_job(worker_id, lease_seconds=lease_seconds)


def test_two_workers_claim_distinct_jobs_atomically(database_url: str) -> None:
    first_job_id = _seed_job(database_url, source_key="synthetic/policy-001.pdf")
    second_job_id = _seed_job(database_url, source_key="synthetic/policy-002.pdf")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(_claim, database_url, "worker-a", 30)
        second_future = executor.submit(_claim, database_url, "worker-b", 30)
        first = first_future.result()
        second = second_future.result()

    assert first is not None
    assert second is not None
    assert {first.id, second.id} == {first_job_id, second_job_id}
    assert {first.lease_owner, second.lease_owner} == {"worker-a", "worker-b"}
    assert {_state(first.state), _state(second.state)} == {"running"}
    assert first.attempts == second.attempts == 1


def test_claim_increments_attempt_and_keeps_settings_password_free(database_url: str) -> None:
    job_id = _seed_job(database_url)
    queue = JobQueue(database_url, default_lease_seconds=45)

    claimed = queue.claim_next_job("worker-a")

    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.attempts == 1
    assert claimed.settings == _QUEUE_SETTINGS
    assert "password" not in json.dumps(claimed.settings, sort_keys=True).lower()


def test_claim_rejects_settings_and_config_hash_drift(database_url: str) -> None:
    job_id = _seed_job(database_url)
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            "UPDATE analysis_jobs SET extractor_config_hash = %s WHERE id = %s",
            ("c" * 64, job_id),
        )
    queue = JobQueue(database_url, default_lease_seconds=30)

    assert queue.claim_next_job("worker-a") is None
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        stored = connection.execute(
            "SELECT state, error_code FROM analysis_jobs WHERE id = %s",
            (job_id,),
        ).fetchone()
    assert stored == ("permanently_failed", "INVALID_REQUEST")


def test_expired_lease_is_recovered_by_another_worker(database_url: str) -> None:
    now = datetime.now(UTC)
    job_id = _seed_job(
        database_url,
        state="running",
        attempts=1,
        lease_owner="worker-old",
        lease_expires_at=now - timedelta(seconds=1),
        heartbeat_at=now - timedelta(seconds=2),
    )
    queue = JobQueue(database_url, default_lease_seconds=30)

    recovered = queue.claim_next_job("worker-new")

    assert recovered is not None
    assert recovered.id == job_id
    assert _state(recovered.state) == "running"
    assert recovered.lease_owner == "worker-new"
    assert recovered.attempts == 2


def test_job_at_max_attempts_is_permanently_failed_and_not_reclaimed(database_url: str) -> None:
    job_id = _seed_job(database_url, attempts=3, max_attempts=3)
    queue = JobQueue(database_url, default_lease_seconds=30)

    claimed = queue.claim_next_job("worker-a")
    stored = _required_record(queue, job_id)

    assert claimed is None
    assert _state(stored.state) == "permanently_failed"
    assert stored.attempts == 3


def test_heartbeat_is_limited_to_the_current_lease_owner(database_url: str) -> None:
    job_id = _seed_job(database_url)
    queue = JobQueue(database_url, default_lease_seconds=30)
    claimed = queue.claim_next_job("worker-a")
    assert claimed is not None

    assert queue.heartbeat(job_id, "worker-b") is False
    assert queue.heartbeat(job_id, "worker-a") is True


def test_expired_owner_cannot_fail_job_after_losing_lease(database_url: str) -> None:
    job_id = _seed_job(database_url)
    queue = JobQueue(database_url, default_lease_seconds=30)
    claimed = queue.claim_next_job("worker-a")
    assert claimed is not None
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            """
            UPDATE analysis_jobs
            SET lease_expires_at = clock_timestamp() - interval '1 second'
            WHERE id = %s
            """,
            (job_id,),
        )

    with pytest.raises(JobStateConflict):
        queue.fail_job(job_id, "worker-a", IntakeErrorCode.PDF_CORRUPT)


def test_cancellation_is_idempotent_and_succeeded_jobs_are_protected(database_url: str) -> None:
    queued_job_id = _seed_job(database_url)
    succeeded_job_id = _seed_job(
        database_url,
        state="succeeded",
        attempts=1,
        source_key="synthetic/policy-002.pdf",
    )
    queue = JobQueue(database_url)

    queue.cancel_job(queued_job_id)
    queue.cancel_job(queued_job_id)
    assert _state(_required_record(queue, queued_job_id).state) == "cancelled"

    with pytest.raises(JobStateConflict):
        queue.cancel_job(succeeded_job_id)


@pytest.mark.parametrize(
    ("error_code", "expected_state"),
    (
        (IntakeErrorCode.EXTRACTION_TIMEOUT, "retryable_failed"),
        (IntakeErrorCode.RESOURCE_LIMIT_EXCEEDED, "retryable_failed"),
        (IntakeErrorCode.PDF_CORRUPT, "permanently_failed"),
        (IntakeErrorCode.TEMP_CLEANUP_FAILED, "permanently_failed"),
    ),
)
def test_fail_job_classifies_timeout_corruption_and_cleanup(
    database_url: str,
    error_code: IntakeErrorCode,
    expected_state: str,
) -> None:
    job_id = _seed_job(database_url)
    queue = JobQueue(database_url, default_lease_seconds=30)
    claimed = queue.claim_next_job("worker-a")
    assert claimed is not None

    result_state = queue.fail_job(job_id, "worker-a", error_code)
    stored = _required_record(queue, job_id)

    assert _state(result_state) == expected_state
    assert _state(stored.state) == expected_state
    assert _state(stored.error_code) == error_code.value


def test_retryable_failure_at_max_attempts_becomes_permanent(database_url: str) -> None:
    job_id = _seed_job(database_url, attempts=2, max_attempts=3)
    queue = JobQueue(database_url, default_lease_seconds=30)
    claimed = queue.claim_next_job("worker-a")
    assert claimed is not None and claimed.attempts == 3

    result_state = queue.fail_job(
        job_id,
        "worker-a",
        IntakeErrorCode.EXTRACTION_TIMEOUT,
    )

    assert _state(result_state) == "permanently_failed"
    assert _state(_required_record(queue, job_id).state) == "permanently_failed"
