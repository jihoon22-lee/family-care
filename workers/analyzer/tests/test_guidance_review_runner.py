"""Optional review failures and late responses do not replace the local answer."""

from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from familycare_worker.ai.provider import ProviderRateLimitError
from familycare_worker.guidance_review_jobs import (
    ReviewLease,
    ReviewQueueUnavailable,
    ReviewWorkInput,
)

from workers.analyzer.tests.test_guidance_reviewer import FakeProvider, sources


class Queue:
    def __init__(self):
        source = sources()
        self.job = ReviewLease(
            id=uuid4(),
            household_space_id=UUID(source["household_space_id"]),
            family_member_id=UUID(source["family_member_id"]),
            medical_event_id=UUID(source["medical_event_id"]),
            decision_run_id=uuid4(),
            event_version=1,
            source_digest=source["digest_sha256"],
            input_digest="a" * 64,
            model="synthetic-review-model",
            prompt_revision="guidance-review-proposals-v1",
            lease_token=uuid4(),
            deadline_at=datetime.now(UTC) + timedelta(seconds=100),
        )
        document = uuid4()
        self.work = ReviewWorkInput(
            sources=source,
            event={
                "id": source["medical_event_id"],
                "household_space_id": source["household_space_id"],
                "family_member_id": source["family_member_id"],
                "version": 1,
                "situation": "Synthetic admission for five days.",
                "facts": {"MedicalEvent.admission_days": {"value": 5, "confirmation": "user"}},
            },
            local_guidance={
                "medical_event_id": source["medical_event_id"],
                "family_member_id": source["family_member_id"],
                "event_version": 1,
                "candidates": [],
            },
            sensitive_terms=(),
            document_versions={
                UUID(packet["envelope"]["source"]["document_version_id"]): document
                for packet in source["packets"]
            }
            | {
                UUID(value): document
                for entry in source["index"]
                for value in entry.get("source_document_version_ids", [])
            },
        )
        self.claimed = False
        self.state = "queued"
        self.error = None
        self.proposals = []
        self.stale = False

    def claim(self):
        if self.claimed:
            return None
        self.claimed = True
        self.state = "running"
        return self.job

    def load_inputs(self, job):
        if self.stale or self.state == "cancelled":
            raise ReviewQueueUnavailable
        return self.work

    def record_proposal(self, job, value):
        if self.state != "running":
            return False
        self.proposals.append(value)
        return True

    def fail(self, job, code):
        if self.state == "running":
            self.state, self.error = "failed", code
            return True
        return False


class Budget:
    def __init__(self):
        self.reservations = []
        self.settlements = []
        self.settled = Event()

    def reserve(self, job, **kwargs):
        self.reservations.append(kwargs)
        return uuid4()

    def finish(self, reservation, **kwargs):
        self.settlements.append(kwargs)
        self.settled.set()


def runner(provider=None, **kwargs):
    from familycare_worker.guidance_review_runner import GuidanceReviewRunner

    queue, budget = Queue(), Budget()
    provider = provider or FakeProvider()
    worker = GuidanceReviewRunner(
        queue=queue, provider=provider, request_budget=budget, configured=lambda: True, **kwargs
    )
    return SimpleNamespace(queue=queue, budget=budget, provider=provider, worker=worker)


def test_only_one_explicit_job_call_reserves_before_wire_and_saves_proposal():
    sample = runner()
    original = sample.provider.complete

    def complete(**kwargs):
        assert len(sample.budget.reservations) == 1
        return original(**kwargs)

    sample.provider.complete = complete
    assert sample.worker.run_once("worker-a")
    assert not sample.worker.run_once("worker-b")
    assert len(sample.provider.calls) == 1
    assert sample.queue.proposals[0]["schema_revision"] == "guidance-review-proposals-v1"
    assert sample.queue.state == "running"
    assert sample.budget.settlements[0]["metadata"].usage.total_tokens == 150


def test_unconfigured_review_spends_nothing_and_keeps_original():
    sample = runner()
    sample.worker.configured = lambda: False
    assert sample.worker.run_once("worker-a")
    assert not sample.provider.calls and not sample.budget.reservations
    assert sample.queue.error == "REVIEW_PROVIDER_CONFIGURATION"
    assert sample.queue.work.local_guidance["candidates"] == []


def test_429_spends_one_reservation_without_retrying():
    class Limited(FakeProvider):
        def complete(self, **kwargs):
            self.calls.append(kwargs)
            raise ProviderRateLimitError

    sample = runner(Limited())
    sample.worker.run_once("worker-a")
    assert not sample.worker.run_once("worker-a")
    assert len(sample.provider.calls) == len(sample.budget.reservations) == 1
    assert sample.queue.error == "REVIEW_RATE_LIMITED"
    assert sample.budget.settlements == [{"succeeded": False, "metadata": None}]


def test_call_deadline_returns_before_late_response_and_retains_late_usage():
    release = Event()

    class Delayed(FakeProvider):
        def complete(self, **kwargs):
            assert release.wait(timeout=3)
            return super().complete(**kwargs)

    sample = runner(Delayed(), call_timeout_seconds=0.02)
    try:
        assert sample.worker.run_once("worker-a")
        assert sample.queue.error == "REVIEW_PROVIDER_TIMEOUT"
        assert not sample.queue.proposals and not sample.budget.settlements
        assert len(sample.budget.reservations) == 1
    finally:
        release.set()
    assert sample.budget.settled.wait(timeout=2)
    assert sample.budget.settlements[0]["metadata"].usage.total_tokens == 150
    assert sample.queue.state == "failed" and not sample.queue.proposals


@pytest.mark.parametrize("fault", ["cancel", "stale"])
def test_response_cannot_replace_cancelled_or_changed_input(fault):
    sample = runner()
    original = sample.provider.complete

    def complete(**kwargs):
        if fault == "cancel":
            sample.queue.state = "cancelled"
        else:
            sample.queue.stale = True
        return original(**kwargs)

    sample.provider.complete = complete
    sample.worker.run_once("worker-a")
    assert not sample.queue.proposals
    assert sample.budget.settlements[0]["succeeded"]
    assert sample.queue.state == ("cancelled" if fault == "cancel" else "failed")


def test_no_original_packets_never_transmits_even_when_index_exists():
    sample = runner()
    sample.queue.work.sources["packets"] = []
    sample.worker.run_once("worker-a")
    assert not sample.provider.calls and not sample.budget.reservations
    assert sample.queue.error == "REVIEW_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("fault", ["cancel", "stale", "stop", "deadline"])
def test_reservation_delay_cannot_start_a_cancelled_or_expired_call(fault, monkeypatch):
    from familycare_worker import guidance_review_runner

    sample = runner()
    original = sample.budget.reserve

    def reserve(job, **kwargs):
        reservation = original(job, **kwargs)
        if fault == "cancel":
            sample.queue.state = "cancelled"
        elif fault == "stale":
            sample.queue.stale = True
        elif fault == "stop":
            sample.worker.stop_requested = lambda: True
        else:

            class ExpiredClock:
                @staticmethod
                def now(zone):
                    return job.deadline_at + timedelta(seconds=1)

            monkeypatch.setattr(guidance_review_runner, "datetime", ExpiredClock)
        return reservation

    sample.budget.reserve = reserve
    assert sample.worker.run_once("worker-a")
    assert not sample.provider.calls
    assert not sample.queue.proposals
    assert len(sample.budget.reservations) == 1
