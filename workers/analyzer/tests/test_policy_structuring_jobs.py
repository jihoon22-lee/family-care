"""TDD contract tests for the private policy structuring queue."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg
import pytest
from familycare_worker.ai.provider import (
    EvidenceSlice,
    ProviderConfigurationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
    RetryableProviderError,
)
from familycare_worker.ai.schemas import (
    CandidateField,
    CandidatePipelineResult,
    PolicyCandidate,
)
from familycare_worker.policy_candidates import (
    InvalidPolicyCandidateBatch,
    PolicyCandidatePublisher,
)
from familycare_worker.policy_jobs import (
    DEFAULT_POLICY_STRUCTURING_LEASE_SECONDS,
    MAX_POLICY_STRUCTURING_ATTEMPTS,
    MAX_POLICY_STRUCTURING_BACKOFF_SECONDS,
    MAX_POLICY_STRUCTURING_LEASE_SECONDS,
    POLICY_STRUCTURING_ERROR_CODES,
    InvalidPolicyStructuringJob,
    PolicyStructuringJobNotFound,
    PolicyStructuringJobQueue,
    PolicyStructuringJobRecord,
    PolicyStructuringJobStateConflict,
    PolicyStructuringNoEvidenceError,
    PolicyStructuringQueueUnavailable,
    PolicyStructuringRateLimitError,
    _backoff_seconds,
    _next_failure_state,
    _row_to_job,
    map_policy_structuring_error,
)
from psycopg.rows import dict_row

JOB_ID = UUID("00000000-0000-4000-8000-000000000601")
HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000000602")
BATCH_ITEM_ID = UUID("00000000-0000-4000-8000-000000000603")
MEMBER_ID = UUID("00000000-0000-4000-8000-000000000604")
VERSION_ID = UUID("00000000-0000-4000-8000-000000000605")
EXTRACTION_ID = UUID("00000000-0000-4000-8000-000000000606")
AGGREGATE_ID = UUID("00000000-0000-4000-8000-000000000607")


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": JOB_ID,
        "household_space_id": HOUSEHOLD_ID,
        "batch_item_id": BATCH_ITEM_ID,
        "family_member_id": MEMBER_ID,
        "document_version_id": VERSION_ID,
        "extraction_id": EXTRACTION_ID,
        "policy_aggregate_id": AGGREGATE_ID,
        "state": "running",
        "pipeline_version": "synthetic-policy-v1",
        "available_at": datetime.now(UTC),
        "lease_owner": "worker-a",
        "lease_expires_at": datetime.now(UTC),
        "heartbeat_at": datetime.now(UTC),
        "attempts": 1,
        "max_attempts": 5,
        "error_code": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "completed_at": None,
        # This simulates an accidental joined source row. It must not be copied.
        "source_key": "synthetic/private/policy.pdf",
    }
    row.update(overrides)
    return row


def test_policy_structuring_error_codes_are_closed_and_safe() -> None:
    assert (
        frozenset(
            {
                "POLICY_STRUCTURING_AUTHENTICATION_FAILED",
                "POLICY_STRUCTURING_INVALID_RESPONSE",
                "POLICY_STRUCTURING_NO_EVIDENCE",
                "POLICY_STRUCTURING_PROVIDER_TIMEOUT",
                "POLICY_STRUCTURING_RATE_LIMITED",
                "POLICY_STRUCTURING_UNAVAILABLE",
            }
        )
        == POLICY_STRUCTURING_ERROR_CODES
    )
    assert "source_key" not in PolicyStructuringJobRecord.__dataclass_fields__
    assert "path" not in PolicyStructuringJobRecord.__dataclass_fields__
    assert "text" not in PolicyStructuringJobRecord.__dataclass_fields__
    assert "provider_payload" not in PolicyStructuringJobRecord.__dataclass_fields__


def test_provider_failures_map_to_sanitized_policy_codes() -> None:
    assert (
        map_policy_structuring_error(ProviderConfigurationError())
        == "POLICY_STRUCTURING_AUTHENTICATION_FAILED"
    )
    assert (
        map_policy_structuring_error(ProviderValidationError())
        == "POLICY_STRUCTURING_INVALID_RESPONSE"
    )
    assert (
        map_policy_structuring_error(PolicyStructuringNoEvidenceError())
        == "POLICY_STRUCTURING_NO_EVIDENCE"
    )
    assert (
        map_policy_structuring_error(TimeoutError("synthetic timeout detail"))
        == "POLICY_STRUCTURING_PROVIDER_TIMEOUT"
    )
    assert (
        map_policy_structuring_error(ProviderTimeoutError())
        == "POLICY_STRUCTURING_PROVIDER_TIMEOUT"
    )
    assert (
        map_policy_structuring_error(ProviderRateLimitError()) == "POLICY_STRUCTURING_RATE_LIMITED"
    )
    assert (
        map_policy_structuring_error(ProviderUnavailableError()) == "POLICY_STRUCTURING_UNAVAILABLE"
    )
    assert (
        map_policy_structuring_error(PolicyStructuringRateLimitError())
        == "POLICY_STRUCTURING_RATE_LIMITED"
    )
    assert map_policy_structuring_error(RetryableProviderError()) == (
        "POLICY_STRUCTURING_PROVIDER_TIMEOUT"
    )
    assert map_policy_structuring_error(RuntimeError("private provider detail")) == (
        "POLICY_STRUCTURING_UNAVAILABLE"
    )


def test_row_validation_accepts_only_safe_scoped_metadata() -> None:
    job = _row_to_job(_row())

    assert job.id == JOB_ID
    assert job.household_space_id == HOUSEHOLD_ID
    assert job.batch_item_id == BATCH_ITEM_ID
    assert job.family_member_id == MEMBER_ID
    assert job.document_version_id == VERSION_ID
    assert job.extraction_id == EXTRACTION_ID
    assert job.policy_aggregate_id == AGGREGATE_ID
    assert job.state == "running"
    assert job.pipeline_version == "synthetic-policy-v1"
    assert "synthetic/private/policy.pdf" not in repr(job)


@pytest.mark.parametrize(
    "overrides",
    [
        {"pipeline_version": ""},
        {"pipeline_version": "x" * 65},
        {"state": "unknown"},
        {"attempts": -1},
        {"attempts": 6},
        {"max_attempts": 0},
        {"max_attempts": 6},
        {"error_code": "PRIVATE_PROVIDER_DETAIL"},
        {"state": "queued", "lease_owner": "worker-a"},
        {"state": "running", "lease_expires_at": None},
        {"state": "succeeded", "completed_at": None},
        {"state": "queued", "completed_at": datetime.now(UTC)},
    ],
)
def test_row_validation_rejects_invalid_queue_invariants(overrides: dict[str, object]) -> None:
    with pytest.raises(InvalidPolicyStructuringJob) as raised:
        _row_to_job(_row(**overrides))
    assert str(raised.value) == "INVALID_POLICY_STRUCTURING_JOB"
    assert "PRIVATE_PROVIDER_DETAIL" not in str(raised.value)


def test_failure_classification_is_terminal_or_bounded_retry() -> None:
    assert _next_failure_state("POLICY_STRUCTURING_AUTHENTICATION_FAILED", 1, 5) == (
        "permanently_failed",
        False,
    )
    assert _next_failure_state("POLICY_STRUCTURING_INVALID_RESPONSE", 1, 5) == (
        "permanently_failed",
        False,
    )
    assert _next_failure_state("POLICY_STRUCTURING_NO_EVIDENCE", 1, 5) == (
        "permanently_failed",
        False,
    )
    assert _next_failure_state("POLICY_STRUCTURING_PROVIDER_TIMEOUT", 1, 5) == (
        "retryable_failed",
        True,
    )
    assert _next_failure_state("POLICY_STRUCTURING_RATE_LIMITED", 5, 5) == (
        "permanently_failed",
        False,
    )
    assert 0 < _backoff_seconds(1) <= MAX_POLICY_STRUCTURING_BACKOFF_SECONDS
    assert _backoff_seconds(99) == MAX_POLICY_STRUCTURING_BACKOFF_SECONDS


def test_queue_configuration_and_sql_are_bounded_and_path_free() -> None:
    queue = PolicyStructuringJobQueue("postgresql+psycopg://synthetic")
    assert queue.default_lease_seconds == DEFAULT_POLICY_STRUCTURING_LEASE_SECONDS == 180
    assert MAX_POLICY_STRUCTURING_LEASE_SECONDS == 3_600
    assert MAX_POLICY_STRUCTURING_ATTEMPTS == 5
    with pytest.raises(ValueError):
        PolicyStructuringJobQueue("postgresql://synthetic", default_lease_seconds=0)

    claim_sql = queue._claim_sql()
    assert "FOR UPDATE OF job SKIP LOCKED" in claim_sql
    assert "attempts < job.max_attempts" in claim_sql
    assert "lease_owner = %s" in claim_sql
    assert "source_key" not in claim_sql
    assert "provider_payload" not in claim_sql


def test_psycopg_failures_become_a_fixed_queue_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def connect(*_: Any, **__: Any) -> Any:
        raise psycopg.OperationalError("synthetic database detail")

    monkeypatch.setattr("familycare_worker.policy_jobs.psycopg.connect", connect)
    queue = PolicyStructuringJobQueue("postgresql://synthetic")

    with pytest.raises(PolicyStructuringQueueUnavailable) as raised:
        queue.get_job(JOB_ID)
    assert str(raised.value) == "POLICY_STRUCTURING_QUEUE_UNAVAILABLE"
    assert "synthetic database detail" not in str(raised.value)


def test_queue_conflict_errors_have_fixed_messages() -> None:
    assert str(PolicyStructuringJobNotFound()) == "POLICY_STRUCTURING_JOB_NOT_FOUND"
    assert str(PolicyStructuringJobStateConflict()) == "POLICY_STRUCTURING_JOB_STATE_CONFLICT"


def _psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture()
def seeded_policy_database() -> Any:
    database_url = os.environ.get("FAMILYCARE_DATABASE_URL")
    if not database_url:
        pytest.skip("FAMILYCARE_DATABASE_URL is required for PostgreSQL integration tests")

    household_id = UUID("00000000-0000-4000-8000-000000000608")
    user_id = UUID("00000000-0000-4000-8000-000000000609")
    member_id = UUID("00000000-0000-4000-8000-000000000610")
    batch_id = UUID("00000000-0000-4000-8000-000000000611")
    document_ids = tuple(UUID(f"00000000-0000-4000-8000-00000000061{i}") for i in range(2, 5))
    version_ids = tuple(UUID(f"00000000-0000-4000-8000-00000000062{i}") for i in range(2, 5))
    extraction_ids = tuple(UUID(f"00000000-0000-4000-8000-00000000063{i}") for i in range(2, 5))
    batch_item_ids = tuple(UUID(f"00000000-0000-4000-8000-00000000064{i}") for i in range(2, 5))
    job_ids = tuple(UUID(f"00000000-0000-4000-8000-00000000065{i}") for i in range(2, 5))

    evidence_ids = tuple(UUID(f"00000000-0000-4000-8000-00000000066{i}") for i in range(2, 5))

    def cleanup(connection: psycopg.Connection[Any]) -> None:
        connection.execute(
            "DELETE FROM analysis_candidate_versions WHERE structuring_job_id IN (%s, %s, %s)",
            job_ids,
        )
        connection.execute(
            "DELETE FROM policy_structuring_jobs WHERE id IN (%s, %s, %s)",
            job_ids,
        )
        connection.execute("DELETE FROM evidence WHERE id IN (%s, %s, %s)", evidence_ids)
        connection.execute(
            "DELETE FROM document_batch_items WHERE id IN (%s, %s, %s)",
            batch_item_ids,
        )
        connection.execute("DELETE FROM document_batches WHERE id = %s", (batch_id,))
        connection.execute(
            "DELETE FROM extractions WHERE id IN (%s, %s, %s)",
            extraction_ids,
        )
        connection.execute(
            "DELETE FROM document_versions WHERE id IN (%s, %s, %s)",
            version_ids,
        )
        connection.execute(
            "DELETE FROM documents WHERE id IN (%s, %s, %s)",
            document_ids,
        )
        connection.execute("DELETE FROM app_users WHERE id = %s", (user_id,))
        connection.execute("DELETE FROM family_members WHERE id = %s", (member_id,))
        connection.execute("DELETE FROM household_spaces WHERE id = %s", (household_id,))

    with psycopg.connect(_psycopg_url(database_url)) as connection:
        cleanup(connection)
        connection.execute(
            """
            INSERT INTO household_spaces (id, space_key, display_name)
            VALUES (%s, 'synthetic-policy-job-space', 'Synthetic Policy Job Space')
            """,
            (household_id,),
        )
        connection.execute(
            """
            INSERT INTO app_users (
              id, household_space_id, username, display_name, password_hash
            ) VALUES (%s, %s, 'synthetic-policy-job-user', 'Synthetic Policy Job User',
                     '$argon2id$synthetic')
            """,
            (user_id, household_id),
        )
        connection.execute(
            """
            INSERT INTO family_members (id, household_space_id, display_name, internal_alias)
            VALUES (%s, %s, 'Synthetic Policy Member', 'synthetic-policy-member')
            """,
            (member_id, household_id),
        )
        connection.execute(
            """
            INSERT INTO document_batches (
              id, household_space_id, family_member_id, created_by, state
            ) VALUES (%s, %s, %s, %s, 'created')
            """,
            (batch_id, household_id, member_id, user_id),
        )
        for position, document_id in enumerate(document_ids):
            connection.execute(
                """
                INSERT INTO documents (
                  id, source_key, document_kind, status, byte_size, page_count
                ) VALUES (%s, %s, 'policy', 'ready', 128, 1)
                """,
                (document_id, f"synthetic/policy-{position}.pdf"),
            )
            connection.execute(
                """
                INSERT INTO document_versions (
                  id, document_id, version_number, content_sha256, byte_size, page_count
                ) VALUES (%s, %s, 1, %s, 128, 1)
                """,
                (version_ids[position], document_id, "a" * 64),
            )
            connection.execute(
                """
                INSERT INTO extractions (
                  id, document_version_id, extractor_name, extractor_version,
                  extractor_config_hash, quality_rule_version, status, succeeded_at
                ) VALUES (%s, %s, 'synthetic', 'synthetic-v1', %s, 'quality-v1',
                         'succeeded', clock_timestamp())
                """,
                (extraction_ids[position], version_ids[position], "b" * 64),
            )
            connection.execute(
                """
                INSERT INTO evidence (
                  id, household_space_id, document_version_id, extraction_id,
                  content_sha256, physical_page, review_state
                ) VALUES (%s, %s, %s, %s, %s, 1, 'NEEDS_REVIEW')
                """,
                (
                    evidence_ids[position],
                    household_id,
                    version_ids[position],
                    extraction_ids[position],
                    "a" * 64,
                ),
            )
            connection.execute(
                """
                INSERT INTO document_batch_items (
                  id, batch_id, document_id, source_id, source_key, display_label,
                  document_kind, state, available_at, completed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 'policy', 'succeeded',
                          clock_timestamp(), clock_timestamp())
                """,
                (
                    batch_item_ids[position],
                    batch_id,
                    document_id,
                    f"{position + 1:064x}",
                    f"synthetic/policy-{position}.pdf",
                    f"Synthetic Policy {position}",
                ),
            )

        connection.execute(
            """
            INSERT INTO policy_structuring_jobs (
              id, household_space_id, batch_item_id, family_member_id,
              document_version_id, extraction_id, state, pipeline_version,
              available_at, max_attempts
            ) VALUES
              (%s, %s, %s, %s, %s, %s, 'queued', 'synthetic-policy-v1',
               clock_timestamp() - interval '3 seconds', 3),
              (%s, %s, %s, %s, %s, %s, 'queued', 'synthetic-policy-v1',
               clock_timestamp() + interval '60 seconds', 5),
              (%s, %s, %s, %s, %s, %s, 'queued', 'synthetic-policy-v1',
               clock_timestamp() + interval '120 seconds', 5)
            """,
            (
                job_ids[0],
                household_id,
                batch_item_ids[0],
                member_id,
                version_ids[0],
                extraction_ids[0],
                job_ids[1],
                household_id,
                batch_item_ids[1],
                member_id,
                version_ids[1],
                extraction_ids[1],
                job_ids[2],
                household_id,
                batch_item_ids[2],
                member_id,
                version_ids[2],
                extraction_ids[2],
            ),
        )
    try:
        yield database_url, household_id, job_ids, evidence_ids
    finally:
        with psycopg.connect(_psycopg_url(database_url)) as connection:
            cleanup(connection)


@pytest.mark.integration
def test_postgres_policy_queue_claims_heartbeats_retries_and_completes(
    seeded_policy_database: Any,
) -> None:
    database_url, household_id, job_ids, _ = seeded_policy_database
    queue = PolicyStructuringJobQueue(database_url, default_lease_seconds=30)

    first = queue.claim_next_job("worker-a")
    assert first is not None
    assert first.id == job_ids[0]
    assert first.attempts == 1
    assert queue.heartbeat(first.id, "worker-b") is False
    assert queue.heartbeat(first.id, "worker-a") is True
    assert queue.fail_job(first.id, "worker-a", "POLICY_STRUCTURING_PROVIDER_TIMEOUT") == (
        "retryable_failed"
    )

    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            "UPDATE policy_structuring_jobs SET available_at = clock_timestamp() WHERE id = %s",
            (job_ids[0],),
        )
    second = queue.claim_next_job("worker-a")
    assert second is not None
    assert second.id == job_ids[0]
    assert second.attempts == 2
    assert queue.fail_job(second.id, "worker-a", "POLICY_STRUCTURING_PROVIDER_TIMEOUT") == (
        "retryable_failed"
    )

    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            """
            UPDATE policy_structuring_jobs
            SET state = 'running', lease_owner = 'stale-worker',
                lease_expires_at = clock_timestamp() - interval '1 second',
                heartbeat_at = clock_timestamp() - interval '2 seconds',
                available_at = clock_timestamp()
            WHERE id = %s
            """,
            (job_ids[0],),
        )
        connection.execute(
            """
            UPDATE policy_structuring_jobs
            SET available_at = clock_timestamp() + interval '60 seconds'
            WHERE id IN (%s, %s)
            """,
            (job_ids[1], job_ids[2]),
        )
    recovered = queue.claim_next_job("worker-a")
    assert recovered is not None
    assert recovered.id == job_ids[0]
    assert recovered.attempts == 3
    assert queue.fail_job(recovered.id, "worker-a", "POLICY_STRUCTURING_PROVIDER_TIMEOUT") == (
        "permanently_failed"
    )

    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            "UPDATE policy_structuring_jobs SET available_at = clock_timestamp() "
            "WHERE id IN (%s, %s)",
            (job_ids[1], job_ids[2]),
        )
    permanent = queue.claim_next_job("worker-a")
    assert permanent is not None
    assert permanent.id == job_ids[1]
    assert (
        queue.fail_job(
            permanent.id,
            "worker-a",
            "POLICY_STRUCTURING_AUTHENTICATION_FAILED",
        )
        == "permanently_failed"
    )

    completed = queue.claim_next_job("worker-a")
    assert completed is not None
    assert completed.id == job_ids[2]
    assert queue.complete_job(completed.id, "worker-a") is True
    stored = queue.get_job(completed.id)
    assert stored is not None
    assert stored.state == "succeeded"
    assert stored.household_space_id == household_id


def _candidate_batch(evidence_id: UUID) -> CandidatePipelineResult:
    contract_id = UUID("00000000-0000-4000-8000-000000000671")
    rider_id = UUID("00000000-0000-4000-8000-000000000672")

    def field(field_id: str, value: object) -> CandidateField:
        return CandidateField.model_validate(
            {"field_id": field_id, "value": value, "evidence_ids": (evidence_id,)},
            strict=True,
        )

    return CandidatePipelineResult(
        classification="SUCCESS",
        candidates=(
            PolicyCandidate(
                candidate_id=contract_id,
                candidate_kind="policy_contract",
                status="AI_VERIFIED",
                fields=(field("insurer", "Sample Insurer"), field("product_name", "Sample Plan")),
                issue_codes=(),
                provider_request_ids=("synthetic-structurer", "synthetic-contract-verifier"),
            ),
            PolicyCandidate(
                candidate_id=rider_id,
                candidate_kind="rider",
                status="AI_VERIFIED",
                fields=(
                    field("rider_name", "Sample Rider"),
                    field("rider_key", "sample-rider"),
                    field("benefit_type", "fixed"),
                ),
                issue_codes=(),
                provider_request_ids=("synthetic-structurer", "synthetic-rider-verifier"),
            ),
        ),
    )


@pytest.mark.integration
def test_postgres_candidate_batch_and_job_success_are_one_review_only_transaction(
    seeded_policy_database: Any,
) -> None:
    database_url, _, job_ids, evidence_ids = seeded_policy_database
    queue = PolicyStructuringJobQueue(database_url, default_lease_seconds=30)
    job = queue.claim_next_job("worker-a")
    assert job is not None and job.id == job_ids[0]
    evidence = (
        EvidenceSlice(
            evidence_id=evidence_ids[0],
            document_version_id=job.document_version_id,
            page=1,
            text="Sample minimized policy evidence.",
            bbox=None,
            document_kind="policy",
        ),
    )

    version_ids = PolicyCandidatePublisher(database_url).publish(
        job=job,
        worker_id="worker-a",
        result=_candidate_batch(evidence_ids[0]),
        evidence=evidence,
    )

    assert len(version_ids) == 2
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        stored_job = connection.execute(
            "SELECT state, completed_at FROM policy_structuring_jobs WHERE id = %s",
            (job.id,),
        ).fetchone()
        versions = connection.execute(
            """
            SELECT id, candidate_kind, aggregate_id, structuring_job_id,
                   source_candidate_id, status, provider_request_id
            FROM analysis_candidate_versions
            WHERE structuring_job_id = %s
            ORDER BY candidate_kind, source_candidate_id
            """,
            (job.id,),
        ).fetchall()
        field_count = connection.execute(
            """
            SELECT count(*) FROM analysis_candidate_fields
            WHERE candidate_version_id = ANY(%s)
            """,
            (list(version_ids),),
        ).fetchone()
        evidence_rows = connection.execute(
            """
            SELECT evidence_id, document_version_id, physical_page, bounded_excerpt
            FROM analysis_candidate_evidence
            WHERE candidate_version_id = ANY(%s)
            """,
            (list(version_ids),),
        ).fetchall()
        ledger_counts = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM policy_contracts WHERE id = %s) AS policies,
              (SELECT count(*) FROM riders WHERE policy_contract_id = %s) AS riders
            """,
            (job.policy_aggregate_id, job.policy_aggregate_id),
        ).fetchone()

    assert stored_job is not None
    assert stored_job["state"] == "succeeded"
    assert stored_job["completed_at"] is not None
    assert {row["id"] for row in versions} == set(version_ids)
    assert {row["candidate_kind"] for row in versions} == {"policy_contract", "rider"}
    assert all(row["aggregate_id"] == job.policy_aggregate_id for row in versions)
    assert all(row["structuring_job_id"] == job.id for row in versions)
    assert all(row["status"] == "NEEDS_REVIEW" for row in versions)
    assert {row["provider_request_id"] for row in versions} == {
        "synthetic-contract-verifier",
        "synthetic-rider-verifier",
    }
    assert field_count is not None and field_count["count"] == 5
    assert evidence_rows
    assert all(row["evidence_id"] == evidence_ids[0] for row in evidence_rows)
    assert all(row["document_version_id"] == job.document_version_id for row in evidence_rows)
    assert all(row["physical_page"] == 1 for row in evidence_rows)
    assert all(row["bounded_excerpt"] == evidence[0].text for row in evidence_rows)
    assert ledger_counts is not None
    assert ledger_counts["policies"] == 0
    assert ledger_counts["riders"] == 0


@pytest.mark.integration
def test_invalid_candidate_evidence_rolls_back_without_completing_the_job(
    seeded_policy_database: Any,
) -> None:
    database_url, _, job_ids, evidence_ids = seeded_policy_database
    queue = PolicyStructuringJobQueue(database_url, default_lease_seconds=30)
    job = queue.claim_next_job("worker-a")
    assert job is not None and job.id == job_ids[0]
    unknown_evidence_id = UUID("00000000-0000-4000-8000-000000000679")
    evidence = (
        EvidenceSlice(
            evidence_id=unknown_evidence_id,
            document_version_id=job.document_version_id,
            page=1,
            text="Sample unknown evidence.",
            bbox=None,
            document_kind="policy",
        ),
    )

    with pytest.raises(InvalidPolicyCandidateBatch):
        PolicyCandidatePublisher(database_url).publish(
            job=job,
            worker_id="worker-a",
            result=_candidate_batch(unknown_evidence_id),
            evidence=evidence,
        )

    stored = queue.get_job(job.id)
    assert stored is not None and stored.state == "running"
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        count = connection.execute(
            "SELECT count(*) FROM analysis_candidate_versions WHERE structuring_job_id = %s",
            (job.id,),
        ).fetchone()
    assert count == (0,)
    assert evidence_ids[0] != unknown_evidence_id
