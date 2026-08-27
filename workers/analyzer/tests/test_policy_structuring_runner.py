"""Synthetic orchestration tests for the leased private policy runner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from familycare_worker.ai.provider import (
    EvidenceSlice,
    ProviderResponse,
    RetryableProviderError,
)
from familycare_worker.ai.schemas import CandidatePipelineResult
from familycare_worker.policy_candidates import PolicyCandidateRepositoryUnavailable
from familycare_worker.policy_jobs import (
    PolicyStructuringErrorCode,
    PolicyStructuringJobRecord,
    PolicyStructuringJobState,
)
from familycare_worker.runner import PolicyStructuringJobRunner

JOB_ID = UUID("00000000-0000-4000-8000-000000000701")
HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000000702")
BATCH_ITEM_ID = UUID("00000000-0000-4000-8000-000000000703")
MEMBER_ID = UUID("00000000-0000-4000-8000-000000000704")
VERSION_ID = UUID("00000000-0000-4000-8000-000000000705")
EXTRACTION_ID = UUID("00000000-0000-4000-8000-000000000706")
AGGREGATE_ID = UUID("00000000-0000-4000-8000-000000000707")
EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000708")
CANDIDATE_ID = UUID("00000000-0000-4000-8000-000000000709")


def _job() -> PolicyStructuringJobRecord:
    now = datetime.now(UTC)
    return PolicyStructuringJobRecord(
        id=JOB_ID,
        household_space_id=HOUSEHOLD_ID,
        batch_item_id=BATCH_ITEM_ID,
        family_member_id=MEMBER_ID,
        document_version_id=VERSION_ID,
        extraction_id=EXTRACTION_ID,
        policy_aggregate_id=AGGREGATE_ID,
        state="running",
        pipeline_version="policy-candidate-batch-v2",
        available_at=now,
        lease_owner="worker-a",
        lease_expires_at=now + timedelta(minutes=3),
        heartbeat_at=now,
        attempts=1,
        max_attempts=5,
        error_code=None,
    )


def _evidence() -> tuple[EvidenceSlice, ...]:
    return (
        EvidenceSlice(
            evidence_id=EVIDENCE_ID,
            document_version_id=VERSION_ID,
            page=1,
            text=("Family Member A Policy Number: SAMPLE-PRIVATE-001 Sample Insurer Sample Plan"),
            bbox=None,
            document_kind="policy",
        ),
    )


class FakeQueue:
    def __init__(
        self,
        job: PolicyStructuringJobRecord | None = None,
        *,
        heartbeat_results: Sequence[bool] = (),
    ) -> None:
        self.job = job
        self.heartbeat_results = list(heartbeat_results)
        self.heartbeat_calls: list[tuple[UUID, str]] = []
        self.failures: list[tuple[UUID, str, PolicyStructuringErrorCode]] = []

    def claim_next_job(
        self,
        worker_id: str,
        *,
        lease_seconds: int | None = None,
    ) -> PolicyStructuringJobRecord | None:
        del worker_id, lease_seconds
        claimed, self.job = self.job, None
        return claimed

    def heartbeat(
        self,
        job_id: UUID,
        worker_id: str,
        *,
        lease_seconds: int | None = None,
    ) -> bool:
        del lease_seconds
        self.heartbeat_calls.append((job_id, worker_id))
        return self.heartbeat_results.pop(0) if self.heartbeat_results else True

    def fail_job(
        self,
        job_id: UUID,
        worker_id: str,
        error_code: PolicyStructuringErrorCode,
    ) -> PolicyStructuringJobState:
        self.failures.append((job_id, worker_id, error_code))
        return (
            "permanently_failed"
            if error_code
            in {
                "POLICY_STRUCTURING_AUTHENTICATION_FAILED",
                "POLICY_STRUCTURING_INVALID_RESPONSE",
                "POLICY_STRUCTURING_NO_EVIDENCE",
            }
            else "retryable_failed"
        )


class FakeLoader:
    def __init__(self, evidence: Sequence[EvidenceSlice]) -> None:
        self.evidence = tuple(evidence)

    def load(self, **_: UUID) -> tuple[EvidenceSlice, ...]:
        return self.evidence

    def load_member_terms(self, **_: UUID) -> tuple[str, ...]:
        return ("Family Member A", "family-member-a")


class RecordingProvider:
    def __init__(self, *, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.calls: list[Mapping[str, object]] = []

    def complete(
        self,
        *,
        schema_name: str,
        input_payload: Mapping[str, object],
        **_: object,
    ) -> ProviderResponse:
        self.calls.append(input_payload)
        if self.failure is not None:
            raise self.failure
        if "batch_structurer" in schema_name:
            payload: Mapping[str, object] = {
                "schema_version": "2",
                "policy": {
                    "schema_version": "1",
                    "candidate_id": str(CANDIDATE_ID),
                    "candidate_kind": "policy_contract",
                    "fields": [
                        {
                            "field_id": "insurer",
                            "value": "Sample Insurer",
                            "evidence_ids": [str(EVIDENCE_ID)],
                        },
                        {
                            "field_id": "product_name",
                            "value": "Sample Plan",
                            "evidence_ids": [str(EVIDENCE_ID)],
                        },
                    ],
                },
                "riders": [],
            }
        else:
            payload = {
                "schema_version": "1",
                "candidate_id": str(CANDIDATE_ID),
                "decision": "approved",
                "evidence_ids": [str(EVIDENCE_ID)],
                "issue_codes": [],
            }
        return ProviderResponse(payload=payload, request_id="synthetic-policy-request")


class RecordingPublisher:
    def __init__(self, *, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.calls: list[
            tuple[
                PolicyStructuringJobRecord,
                str,
                CandidatePipelineResult,
                tuple[EvidenceSlice, ...],
            ]
        ] = []

    def publish(
        self,
        *,
        job: PolicyStructuringJobRecord,
        worker_id: str,
        result: CandidatePipelineResult,
        evidence: Sequence[EvidenceSlice],
    ) -> tuple[UUID, ...]:
        self.calls.append((job, worker_id, result, tuple(evidence)))
        if self.failure is not None:
            raise self.failure
        return (UUID("00000000-0000-4000-8000-000000000710"),)


def test_runner_minimizes_member_and_policy_identifiers_before_publishing() -> None:
    queue = FakeQueue(_job())
    provider = RecordingProvider()
    publisher = RecordingPublisher()
    runner = PolicyStructuringJobRunner(
        queue=queue,
        evidence_loader=FakeLoader(_evidence()),
        provider=provider,
        publisher=publisher,
        structurer_model="synthetic-structurer",
        verifier_model="synthetic-verifier",
    )

    assert runner.run_once("worker-a") is True

    assert queue.failures == []
    assert queue.heartbeat_calls == [
        (JOB_ID, "worker-a"),
        (JOB_ID, "worker-a"),
        (JOB_ID, "worker-a"),
    ]
    assert len(provider.calls) == 2
    structurer_evidence = provider.calls[0]["evidence"]
    assert isinstance(structurer_evidence, list)
    minimized_text = structurer_evidence[0]["text"]
    assert minimized_text == "[REDACTED] Policy Number: [REDACTED] Sample Insurer Sample Plan"
    assert len(publisher.calls) == 1
    published_evidence = publisher.calls[0][3]
    assert published_evidence[0].text == minimized_text
    assert "Family Member A" not in repr(published_evidence[0])


def test_runner_marks_empty_evidence_as_a_permanent_safe_failure() -> None:
    queue = FakeQueue(_job())
    runner = PolicyStructuringJobRunner(
        queue=queue,
        evidence_loader=FakeLoader(()),
        provider=RecordingProvider(),
        publisher=RecordingPublisher(),
    )

    assert runner.run_once("worker-a") is True
    assert queue.failures == [(JOB_ID, "worker-a", "POLICY_STRUCTURING_NO_EVIDENCE")]


def test_runner_maps_provider_retry_without_persisting_candidates() -> None:
    queue = FakeQueue(_job())
    publisher = RecordingPublisher()
    runner = PolicyStructuringJobRunner(
        queue=queue,
        evidence_loader=FakeLoader(_evidence()),
        provider=RecordingProvider(failure=RetryableProviderError()),
        publisher=publisher,
    )

    assert runner.run_once("worker-a") is True
    assert publisher.calls == []
    assert queue.failures == [(JOB_ID, "worker-a", "POLICY_STRUCTURING_PROVIDER_TIMEOUT")]


def test_runner_leaves_commit_ambiguity_for_lease_recovery() -> None:
    queue = FakeQueue(_job())
    publisher = RecordingPublisher(failure=PolicyCandidateRepositoryUnavailable())
    runner = PolicyStructuringJobRunner(
        queue=queue,
        evidence_loader=FakeLoader(_evidence()),
        provider=RecordingProvider(),
        publisher=publisher,
    )

    assert runner.run_once("worker-a") is True
    assert len(publisher.calls) == 1
    assert queue.failures == []


def test_runner_stops_provider_calls_immediately_when_lease_is_lost() -> None:
    queue = FakeQueue(_job(), heartbeat_results=(True, False))
    provider = RecordingProvider()
    publisher = RecordingPublisher()
    runner = PolicyStructuringJobRunner(
        queue=queue,
        evidence_loader=FakeLoader(_evidence()),
        provider=provider,
        publisher=publisher,
    )

    assert runner.run_once("worker-a") is True
    assert len(provider.calls) == 1
    assert publisher.calls == []
    assert queue.failures == []


def test_runner_returns_false_when_no_job_is_due() -> None:
    runner = PolicyStructuringJobRunner(
        queue=FakeQueue(),
        evidence_loader=FakeLoader(_evidence()),
        provider=RecordingProvider(),
        publisher=RecordingPublisher(),
    )

    assert runner.run_once("worker-a") is False
