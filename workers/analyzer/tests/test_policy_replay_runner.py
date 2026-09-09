"""A derived draft still spends one normal verifier reservation and grants no authority."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from familycare_worker.policy_draft_replay import ReplayedPolicyDraft
from familycare_worker.policy_range_repository import PolicyRangeConflict, PolicyRangeWork
from familycare_worker.policy_request_budget import PolicyBudgetExhausted
from familycare_worker.runner import PolicyStructuringJobRunner

from workers.analyzer.tests.test_policy_draft_normalization import _batch, _candidate, _envelope
from workers.analyzer.tests.test_policy_structuring_runner import (
    FakeLoader,
    FakeQueue,
    RecordingPublisher,
    _job,
)


@pytest.mark.parametrize("failure", [None, "stale", "budget", "verifier"])
def test_replay_runs_only_fresh_budgeted_verifier_and_preserves_failures(failure):
    from familycare_worker.ai.provider import ProviderResponse, ProviderTimeoutError

    envelope = _envelope("보험증권 Sample Insurer Sample Plan")
    candidate = _candidate(
        envelope.evidence[0], insurer="Sample Insurer", product_name="Sample Plan"
    )
    batch = _batch(envelope, candidate)
    job = replace(
        _job(), processing_mode="retained", pipeline_version="retained-policy-association-v4"
    )
    work = PolicyRangeWork(uuid4(), envelope)
    calls, reservations, saved, paused = [], [], [], []

    def current(*args):
        if failure == "stale":
            raise PolicyRangeConflict

    def reserve(*args):
        reservations.append(args)
        if failure == "budget":
            raise PolicyBudgetExhausted
        return uuid4()

    def complete(**kwargs):
        calls.append(kwargs["schema_name"])
        if failure == "verifier":
            raise ProviderTimeoutError
        return ProviderResponse(
            request_id="synthetic-fresh-verifier",
            payload={
                "schema_version": "2",
                "decisions": [
                    {
                        "schema_version": "1",
                        "candidate_id": str(candidate.candidate_id),
                        "decision": "approved",
                        "evidence_ids": [str(envelope.evidence[0].evidence_id)],
                        "issue_codes": [],
                    }
                ],
            },
        )

    queue = FakeQueue(job)
    runner = PolicyStructuringJobRunner(
        queue=queue,
        evidence_loader=FakeLoader(envelope.evidence),
        publisher=RecordingPublisher(),
        provider=SimpleNamespace(complete=complete),
        range_repository=SimpleNamespace(
            next=lambda *a, **k: work, save=lambda *a: saved.append(a)
        ),
        request_budget=SimpleNamespace(
            reserve=reserve, finish=lambda *a: None, pause=lambda *a: paused.append(a)
        ),
        replay_repository=SimpleNamespace(
            prepare=lambda *a: ReplayedPolicyDraft(batch, "synthetic-original-structurer"),
            assert_current=current,
        ),
    )
    assert runner.run_once("worker-a")
    assert calls == (
        [] if failure in {"stale", "budget"} else ["policy_candidate_batch_verifier_v2"]
    )
    assert len(reservations) == (0 if failure == "stale" else 1)
    assert bool(paused) == (failure == "budget")
    if failure is None:
        result = saved[0][-1]
        assert result.classification == "SUCCESS"
        assert result.candidates[0].provider_request_ids == (
            "synthetic-original-structurer",
            "synthetic-fresh-verifier",
        )
    else:
        assert not saved
