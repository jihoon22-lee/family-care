"""Durable cost accounting and retained stages against synthetic PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from familycare_worker.ai.provider import (
    ProviderResponse,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from familycare_worker.policy_jobs import PolicyStructuringJobQueue
from familycare_worker.policy_request_budget import (
    BudgetedPolicyProvider,
    PolicyBudgetExhausted,
    PolicyRequestBudget,
)

from workers.analyzer.tests.test_policy_structuring_jobs import (
    _psycopg_url,
    seeded_policy_database,  # noqa: F401
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def budget_database(request: pytest.FixtureRequest) -> Any:
    database_url, _, job_ids, _ = request.getfixturevalue("seeded_policy_database")
    queue = PolicyStructuringJobQueue(database_url)
    job = queue.claim_next_job("synthetic-budget-worker")
    assert job is not None and job.id in job_ids
    try:
        yield database_url, job
    finally:
        with psycopg.connect(_psycopg_url(database_url)) as connection:
            connection.execute("TRUNCATE policy_provider_requests")


class Provider:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def complete(self, **kwargs: object) -> ProviderResponse:
        self.calls += 1
        if self.fail:
            raise ProviderTimeoutError
        return ProviderResponse(payload={"synthetic_result": True}, request_id="synthetic-request")


def _provider(database: Any, provider: Any, *, per_document: int = 4, daily: int = 8) -> Any:
    url, job = database
    return BudgetedPolicyProvider(
        provider=provider,
        budget=PolicyRequestBudget(url, per_document=per_document, daily=daily),
        job=job,
        worker_id="synthetic-budget-worker",
    )


def _call(provider: Any, *, value: str = "synthetic evidence") -> ProviderResponse:
    return provider.complete(
        model="synthetic-model",
        schema_name="policy_candidate_batch_structurer_v2",
        system_instruction="Synthetic instruction",
        input_payload={"evidence": value},
    )


def test_success_is_reused_across_provider_instances_and_budget_exhaustion(
    budget_database: Any,
) -> None:
    raw = Provider()
    first = _call(_provider(budget_database, raw, per_document=1))
    assert _call(_provider(budget_database, raw, per_document=1)) == first
    assert raw.calls == 1
    with pytest.raises(PolicyBudgetExhausted, match="POLICY_PROVIDER_BUDGET_EXHAUSTED"):
        _call(_provider(budget_database, raw, per_document=1), value="different synthetic input")
    assert raw.calls == 1


def test_timeout_consumes_budget_and_does_not_cache_a_success(budget_database: Any) -> None:
    raw = Provider(fail=True)
    with pytest.raises(ProviderTimeoutError):
        _call(_provider(budget_database, raw, per_document=1))
    with pytest.raises(PolicyBudgetExhausted, match="POLICY_PROVIDER_BUDGET_EXHAUSTED"):
        _call(_provider(budget_database, raw, per_document=1))
    assert raw.calls == 1


def test_parallel_requests_share_the_last_daily_slot(budget_database: Any) -> None:
    raw = Provider()

    def attempt(value: str) -> bool:
        try:
            _call(_provider(budget_database, raw, daily=1), value=value)
            return True
        except PolicyBudgetExhausted:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, ("synthetic first", "synthetic second")))
    assert sorted(results) == [False, True]
    assert raw.calls == 1


def test_reservation_is_committed_before_the_network_call(budget_database: Any) -> None:
    url, _ = budget_database

    class InspectingProvider(Provider):
        def complete(self, **kwargs: object) -> ProviderResponse:
            with psycopg.connect(_psycopg_url(url)) as connection:
                assert connection.execute(
                    "SELECT count(*) FROM policy_provider_requests WHERE state = 'RESERVED'"
                ).fetchone() == (1,)
            return super().complete(**kwargs)

    _call(_provider(budget_database, InspectingProvider()))


def test_wrong_scope_and_duplicate_inflight_request_cannot_spend(budget_database: Any) -> None:
    url, job = budget_database
    budget = PolicyRequestBudget(url)
    with pytest.raises(ProviderUnavailableError):
        budget.reserve(replace(job, family_member_id=uuid4()), "synthetic-budget-worker", "a" * 64)
    with pytest.raises(ProviderUnavailableError):
        budget.reserve(replace(job, attempts=job.attempts + 1), "synthetic-budget-worker", "a" * 64)
    reservation = budget.reserve(job, "synthetic-budget-worker", "a" * 64)
    with pytest.raises(ProviderUnavailableError):
        budget.reserve(job, "synthetic-budget-worker", "a" * 64)
    assert not isinstance(reservation, ProviderResponse)
    budget.finish(reservation, None)


def test_daily_budget_is_shared_by_different_documents(budget_database: Any) -> None:
    url, job = budget_database
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE policy_structuring_jobs SET available_at = clock_timestamp() "
            "WHERE state = 'queued' AND id <> %s",
            (job.id,),
        )
    other = PolicyStructuringJobQueue(url).claim_next_job("synthetic-budget-worker")
    assert other is not None and other.document_version_id != job.document_version_id
    raw = Provider()
    _call(_provider(budget_database, raw, daily=1))
    with pytest.raises(PolicyBudgetExhausted):
        _call(_provider((url, other), raw, daily=1))
    assert raw.calls == 1


def test_budget_pause_preserves_retry_attempt_and_waits_until_next_utc_day(
    budget_database: Any,
) -> None:
    url, job = budget_database
    PolicyRequestBudget(url).pause(job, "synthetic-budget-worker")
    saved = PolicyStructuringJobQueue(url).get_job(job.id)
    assert saved is not None
    assert saved.state == "retryable_failed"
    assert saved.attempts == job.attempts - 1
    assert saved.available_at > job.available_at
    assert saved.available_at.hour == saved.available_at.minute == 0
    assert saved.error_code == "POLICY_STRUCTURING_RATE_LIMITED"


def test_worker_retry_reuses_structurer_and_only_repeats_failed_verifier(
    budget_database: Any,
) -> None:
    from familycare_worker.ai.provider import EvidenceSlice
    from familycare_worker.runner import PolicyStructuringJobRunner

    from workers.analyzer.tests.test_policy_structuring_runner import FakeLoader, RecordingPublisher

    url, job = budget_database
    evidence_id = uuid4()
    candidate_id = uuid4()

    class Stages(Provider):
        def complete(self, **kwargs: Any) -> ProviderResponse:
            self.calls += 1
            if kwargs["schema_name"] == "policy_candidate_batch_structurer_v2":
                payload: Any = {
                    "schema_version": "2",
                    "riders": [],
                    "policy": {
                        "schema_version": "1",
                        "candidate_id": str(candidate_id),
                        "candidate_kind": "policy_contract",
                        "fields": [
                            {
                                "field_id": "insurer",
                                "value": "Sample Insurer",
                                "evidence_ids": [str(evidence_id)],
                            },
                            {
                                "field_id": "product_name",
                                "value": "Sample Plan",
                                "evidence_ids": [str(evidence_id)],
                            },
                        ],
                    },
                }
            elif self.calls == 2:
                raise ProviderTimeoutError
            else:
                payload = {
                    "schema_version": "2",
                    "decisions": [
                        {
                            "schema_version": "1",
                            "candidate_id": str(candidate_id),
                            "decision": "approved",
                            "evidence_ids": [str(evidence_id)],
                            "issue_codes": [],
                        }
                    ],
                }
            return ProviderResponse(payload=payload, request_id=f"synthetic-stage-{self.calls}")

    # Return the fixture's initially claimed job to the queue without a provider attempt.
    queue = PolicyStructuringJobQueue(url)
    PolicyRequestBudget(url).pause(job, "synthetic-budget-worker")
    raw = Stages()
    publisher = RecordingPublisher()
    runner = PolicyStructuringJobRunner(
        queue=queue,
        request_budget=PolicyRequestBudget(url),
        provider=raw,
        publisher=publisher,
        evidence_loader=FakeLoader(
            (
                EvidenceSlice(
                    evidence_id=evidence_id,
                    document_version_id=job.document_version_id,
                    page=1,
                    text="Sample Insurer Sample Plan",
                    bbox=None,
                    document_kind="policy",
                ),
            )
        ),
    )
    for attempt in range(2):
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute(
                "UPDATE policy_structuring_jobs SET available_at = clock_timestamp() WHERE id = %s",
                (job.id,),
            )
        assert runner.run_once("synthetic-budget-worker")
        if attempt == 0:
            assert raw.calls == 2 and publisher.calls == []
    assert raw.calls == 3
    assert len(publisher.calls) == 1
    assert publisher.calls[0][2].classification == "SUCCESS"
