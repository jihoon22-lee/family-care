"""Durable range progress uses only a dedicated synthetic PostgreSQL database."""

from dataclasses import replace
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from familycare_worker.ai.range_structurer import PolicyRangeBatch
from familycare_worker.ai.schemas import CandidatePipelineResult
from familycare_worker.policy_jobs import PolicyStructuringJobQueue
from familycare_worker.policy_range_repository import PolicyRangeConflict, PolicyRangeRepository

from workers.analyzer.tests.test_document_preparation import _seed_page
from workers.analyzer.tests.test_document_structure_repository import (
    structure_database,  # noqa: F401
)
from workers.analyzer.tests.test_policy_structuring_jobs import (
    _psycopg_url,
    seeded_policy_database,  # noqa: F401
)

pytestmark = pytest.mark.integration
WORKER = "synthetic-range-worker"


@pytest.fixture()
def ranges_database(request: pytest.FixtureRequest) -> Any:
    url, source_job = request.getfixturevalue("structure_database")
    _seed_page(url, source_job)
    with psycopg.connect(_psycopg_url(url)) as connection:
        page = connection.execute(
            "SELECT id FROM extraction_pages WHERE extraction_id = %s", (source_job.extraction_id,)
        ).fetchone()[0]
        for position in range(1, 71):
            connection.execute(
                "INSERT INTO extraction_blocks(page_id, text, bbox, reading_order) "
                "VALUES (%s, %s, '[10,30,400,40]', %s)",
                (page, f"Synthetic source block {position}", position),
            )
    job = PolicyStructuringJobQueue(url).claim_next_job(WORKER)
    assert job is not None and job.id == source_job.id
    yield url, job


def _no_facts(work: Any, *, unresolved: bool = False) -> PolicyRangeBatch:
    return PolicyRangeBatch.model_validate(
        {
            "schema_version": "3",
            "candidates": (),
            "ranges": tuple(
                {
                    "chunk_id": key,
                    "candidate_ids": (),
                    "outcome": "UNRESOLVED" if unresolved else "NO_ENROLLMENT_FACTS",
                }
                for key in work.envelope.primary_chunk_ids
            ),
        }
    )


def test_preparation_retains_all_ranges_and_reuses_the_exact_pending_envelope(
    ranges_database: Any,
) -> None:
    url, job = ranges_database
    repository = PolicyRangeRepository(url)
    first = repository.next(job, WORKER, sensitive_terms=())
    again = PolicyRangeRepository(url).next(job, WORKER, sensitive_terms=())
    assert first is not None and again == first
    with psycopg.connect(_psycopg_url(url)) as connection:
        rows = connection.execute(
            "SELECT envelope_json FROM document_policy_ranges WHERE job_id = %s ORDER BY position",
            (job.id,),
        ).fetchall()
        assert len(rows) == 3
        keys = [item for row in rows for item in row[0]["primary_ranges"]]
        assert len(keys) == 71
        assert "Synthetic source block 70" in str(rows[-1])
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute("UPDATE document_policy_ranges SET envelope_json = '{}'")


def test_successful_range_survives_restart_and_does_not_consume_job_retries(
    ranges_database: Any,
) -> None:
    url, job = ranges_database
    repository = PolicyRangeRepository(url)
    seen: list[str] = []
    for _ in range(3):
        work = repository.next(job, WORKER, sensitive_terms=())
        assert work is not None and work.envelope.envelope_id not in seen
        seen.append(work.envelope.envelope_id)
        repository.save(
            job,
            WORKER,
            work,
            _no_facts(work),
            CandidatePipelineResult(
                classification="SUCCESS",
                candidates=(),
            ),
        )
        saved = PolicyStructuringJobQueue(url).get_job(job.id)
        assert saved is not None
        if len(seen) < 3:
            assert saved.state == "queued" and saved.attempts == 0
            job = PolicyStructuringJobQueue(url).claim_next_job(WORKER)
            assert job is not None and job.id == saved.id
        else:
            assert saved.state == "succeeded"
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute("SELECT state FROM document_policy_range_plans").fetchall() == [
            ("COMPLETE",)
        ]


def test_forged_scope_attempt_and_expired_lease_cannot_read_or_save(ranges_database: Any) -> None:
    url, job = ranges_database
    repository = PolicyRangeRepository(url)
    for wrong in (replace(job, family_member_id=uuid4()), replace(job, attempts=job.attempts + 1)):
        with pytest.raises(PolicyRangeConflict):
            repository.next(wrong, WORKER, sensitive_terms=())
    work = repository.next(job, WORKER, sensitive_terms=())
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE policy_structuring_jobs SET lease_expires_at = "
            "clock_timestamp() - interval '1 second' WHERE id = %s",
            (job.id,),
        )
    with pytest.raises(PolicyRangeConflict):
        repository.save(
            job,
            WORKER,
            work,
            _no_facts(work),
            CandidatePipelineResult(
                classification="SUCCESS",
                candidates=(),
            ),
        )
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM document_policy_ranges WHERE state <> 'PENDING'"
        ).fetchone() == (0,)


def test_unresolved_primary_range_prevents_whole_document_success(ranges_database: Any) -> None:
    url, job = ranges_database
    repository = PolicyRangeRepository(url)
    for _ in range(3):
        work = repository.next(job, WORKER, sensitive_terms=())
        repository.save(
            job,
            WORKER,
            work,
            _no_facts(work, unresolved=True),
            CandidatePipelineResult(classification="SUCCESS", candidates=()),
        )
        current = PolicyStructuringJobQueue(url).get_job(job.id)
        assert current is not None
        if current.state == "queued":
            job = PolicyStructuringJobQueue(url).claim_next_job(WORKER)
    assert current.state == "permanently_failed"
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute("SELECT state FROM document_policy_range_plans").fetchone() == (
            "PARTIAL",
        )
        assert connection.execute(
            "SELECT count(*) FROM document_policy_ranges WHERE result_json IS NOT NULL"
        ).fetchone() == (3,)


@pytest.mark.parametrize("invalid_first", [False, True])
def test_worker_uses_complete_ranges_and_pauses_without_repeating_saved_ranges(
    ranges_database: Any,
    invalid_first: bool,
) -> None:
    from familycare_worker.ai.provider import ProviderResponse
    from familycare_worker.policy_request_budget import PolicyRequestBudget
    from familycare_worker.runner import PolicyStructuringJobRunner

    from workers.analyzer.tests.test_policy_structuring_runner import FakeLoader, RecordingPublisher

    url, job = ranges_database
    seen: list[str] = []

    class Provider:
        def complete(self, **kwargs: Any) -> ProviderResponse:
            assert kwargs["schema_name"] == "policy_range_structurer_v3"
            payload = kwargs["input_payload"]
            seen.append(payload["envelope_id"])
            if invalid_first and len(seen) == 1:
                return ProviderResponse(payload={}, request_id="synthetic-invalid-range")
            return ProviderResponse(
                payload={
                    "schema_version": "3",
                    "candidates": [],
                    "ranges": [
                        {
                            "chunk_id": item["chunk_id"],
                            "outcome": "NO_ENROLLMENT_FACTS",
                            "candidate_ids": [],
                        }
                        for item in payload["primary_ranges"]
                    ],
                },
                request_id=f"synthetic-range-{len(seen)}",
            )

    budget = PolicyRequestBudget(url, per_document=2)
    budget.pause(job, WORKER)
    publisher = RecordingPublisher()
    try:
        runner = PolicyStructuringJobRunner(
            queue=PolicyStructuringJobQueue(url),
            evidence_loader=FakeLoader(()),
            provider=Provider(),
            publisher=publisher,
            request_budget=budget,
            range_repository=PolicyRangeRepository(url),
        )
        for _ in range(3):
            with psycopg.connect(_psycopg_url(url)) as connection:
                connection.execute(
                    "UPDATE policy_structuring_jobs SET available_at = "
                    "clock_timestamp() WHERE id = %s",
                    (job.id,),
                )
            assert runner.run_once(WORKER)
        assert len(seen) == len(set(seen)) == 2
        assert publisher.calls == []
        saved = PolicyStructuringJobQueue(url).get_job(job.id)
        assert saved is not None and saved.state == "retryable_failed" and saved.attempts == 0
        with psycopg.connect(_psycopg_url(url)) as connection:
            counts = connection.execute(
                "SELECT state, count(*) FROM document_policy_ranges GROUP BY state ORDER BY state"
            ).fetchall()
            assert counts == (
                [("COMPLETE", 1), ("PENDING", 1), ("REVIEW", 1)]
                if invalid_first
                else [("COMPLETE", 2), ("PENDING", 1)]
            )
    finally:
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute("TRUNCATE policy_provider_requests")


def test_failed_range_is_retained_and_next_range_can_still_run(ranges_database: Any) -> None:
    url, job = ranges_database
    repository = PolicyRangeRepository(url)
    first = repository.next(job, WORKER, sensitive_terms=())
    assert first is not None
    repository.reject(job, WORKER, first)
    current = PolicyStructuringJobQueue(url).claim_next_job(WORKER)
    assert current is not None and current.id == job.id
    second = repository.next(current, WORKER, sensitive_terms=())
    assert second is not None and first.envelope.envelope_id != second.envelope.envelope_id
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT result_json FROM document_policy_ranges WHERE state = 'REVIEW'"
        ).fetchone() == (
            {
                "schema_version": "1",
                "error_code": "POLICY_RANGE_INVALID_RESPONSE",
            },
        )


def test_changed_minimization_context_cannot_replay_an_older_envelope(ranges_database: Any) -> None:
    url, job = ranges_database
    repository = PolicyRangeRepository(url)
    assert repository.next(job, WORKER, sensitive_terms=()) is not None
    with pytest.raises(PolicyRangeConflict):
        repository.next(job, WORKER, sensitive_terms=("Family Member B",))


def test_range_verifier_timeout_reuses_the_structurer_response(ranges_database: Any) -> None:
    from familycare_worker.ai.provider import ProviderResponse, ProviderTimeoutError
    from familycare_worker.policy_request_budget import PolicyRequestBudget
    from familycare_worker.runner import PolicyStructuringJobRunner

    from workers.analyzer.tests.test_policy_structuring_runner import FakeLoader, RecordingPublisher

    url, job = ranges_database
    candidate_id = str(uuid4())
    calls: list[str] = []

    class Provider:
        evidence_id = ""

        def complete(self, **kwargs: Any) -> ProviderResponse:
            calls.append(kwargs["schema_name"])
            if kwargs["schema_name"] == "policy_range_structurer_v3":
                ranges = kwargs["input_payload"]["primary_ranges"]
                self.evidence_id = ranges[0]["evidence_id"]
                payload: Any = {
                    "schema_version": "3",
                    "candidates": [
                        {
                            "schema_version": "1",
                            "candidate_id": candidate_id,
                            "candidate_kind": "rider",
                            "fields": [
                                {
                                    "field_id": "rider_name",
                                    "value": "Late Sample Rider",
                                    "evidence_ids": [self.evidence_id],
                                }
                            ],
                        }
                    ],
                    "ranges": [
                        {
                            "chunk_id": item["chunk_id"],
                            "outcome": "CANDIDATES" if position == 0 else "NO_ENROLLMENT_FACTS",
                            "candidate_ids": [candidate_id] if position == 0 else [],
                        }
                        for position, item in enumerate(ranges)
                    ],
                }
            elif len(calls) == 2:
                raise ProviderTimeoutError
            else:
                payload = {
                    "schema_version": "2",
                    "decisions": [
                        {
                            "schema_version": "1",
                            "candidate_id": candidate_id,
                            "decision": "approved",
                            "evidence_ids": [self.evidence_id],
                            "issue_codes": [],
                        }
                    ],
                }
            return ProviderResponse(payload=payload, request_id=f"synthetic-stage-{len(calls)}")

    budget = PolicyRequestBudget(url)
    budget.pause(job, WORKER)
    try:
        runner = PolicyStructuringJobRunner(
            queue=PolicyStructuringJobQueue(url),
            evidence_loader=FakeLoader(()),
            provider=Provider(),
            publisher=RecordingPublisher(),
            request_budget=budget,
            range_repository=PolicyRangeRepository(url),
        )
        for attempt in range(2):
            with psycopg.connect(_psycopg_url(url)) as connection:
                connection.execute(
                    "UPDATE policy_structuring_jobs SET available_at = "
                    "clock_timestamp() WHERE id = %s",
                    (job.id,),
                )
            assert runner.run_once(WORKER)
            if attempt == 0:
                with psycopg.connect(_psycopg_url(url)) as connection:
                    assert connection.execute(
                        "SELECT count(*) FROM document_policy_ranges WHERE state <> 'PENDING'"
                    ).fetchone() == (0,)
        assert calls == [
            "policy_range_structurer_v3",
            "policy_candidate_batch_verifier_v2",
            "policy_candidate_batch_verifier_v2",
        ]
        with psycopg.connect(_psycopg_url(url)) as connection:
            saved = connection.execute(
                "SELECT result_json FROM document_policy_ranges WHERE state <> 'PENDING'"
            ).fetchone()[0]
            assert saved["result"]["candidates"][0]["candidate_id"] == candidate_id
            # An unknown page role must not be upgraded to enrollment authority by AI approval.
            assert saved["result"]["candidates"][0]["status"] == "NEEDS_REVIEW"
    finally:
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute("TRUNCATE policy_provider_requests")


def test_unknown_primary_role_cannot_create_an_ai_verified_contract(ranges_database: Any) -> None:
    from familycare_worker.ai.schemas import CandidateField, PolicyCandidate, StructurerCandidate

    url, job = ranges_database
    repository = PolicyRangeRepository(url)
    work = repository.next(job, WORKER, sensitive_terms=())
    assert work is not None
    source = StructurerCandidate(
        schema_version="1",
        candidate_id=uuid4(),
        candidate_kind="policy_contract",
        fields=(
            CandidateField(
                field_id="insurer",
                value="Sample Insurer",
                evidence_ids=(work.envelope.primary_evidence_ids[0],),
            ),
            CandidateField(
                field_id="product_name",
                value="Sample Plan",
                evidence_ids=(work.envelope.primary_evidence_ids[0],),
            ),
        ),
    )
    dispositions = _no_facts(work).ranges
    batch = PolicyRangeBatch(
        schema_version="3",
        candidates=(source,),
        ranges=(
            dispositions[0].model_copy(
                update={"outcome": "CANDIDATES", "candidate_ids": (source.candidate_id,)}
            ),
            *dispositions[1:],
        ),
    )
    result = CandidatePipelineResult(
        classification="SUCCESS",
        candidates=(
            PolicyCandidate(
                candidate_id=source.candidate_id,
                candidate_kind=source.candidate_kind,
                fields=source.fields,
                status="AI_VERIFIED",
                issue_codes=(),
                provider_request_ids=("synthetic-structurer", "synthetic-verifier"),
            ),
        ),
    )
    repository.save(job, WORKER, work, batch, result)
    with psycopg.connect(_psycopg_url(url)) as connection:
        saved = connection.execute(
            "SELECT state, result_json FROM document_policy_ranges WHERE result_json IS NOT NULL"
        ).fetchone()
        assert saved[0] == "REVIEW"
        assert saved[1]["result"]["candidates"][0]["status"] == "NEEDS_REVIEW"
        assert "UNSUPPORTED_STRUCTURE" in saved[1]["result"]["candidates"][0]["issue_codes"]
