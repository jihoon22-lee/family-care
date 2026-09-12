"""Non-policy primary ranges stop before replay, normalization, or provider work."""

from dataclasses import replace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from familycare_worker.ai.policy_ranges import PolicyRangeEnvelope
from familycare_worker.ai.range_structurer import PolicyRangeBatch, RangeDisposition
from familycare_worker.policy_range_repository import (
    PolicyRangeConflict,
    PolicyRangeRepository,
    PolicyRangeWork,
)
from familycare_worker.policy_request_budget import PolicyRequestBudget
from familycare_worker.runner import PolicyStructuringJobRunner

from workers.analyzer.tests.test_policy_draft_normalization import _envelope
from workers.analyzer.tests.test_policy_structuring_runner import FakeLoader, FakeQueue, _job


def _work(roles, *, context_policy=False):
    envelope = _envelope(*(f"Synthetic source {i}" for i in range(len(roles))))
    evidence = tuple(
        replace(item, source_role=role, document_kind="policy" if role == "policy" else "terms")
        for item, role in zip(envelope.evidence, roles, strict=True)
    )
    if context_policy:
        evidence += (
            replace(
                evidence[0],
                evidence_id=uuid4(),
                primary=False,
                source_role="policy",
                document_kind="policy",
            ),
        )
    return PolicyRangeWork(uuid4(), replace(envelope, evidence=evidence))


@pytest.mark.parametrize("roles", [("terms",), ("unknown",), ("ambiguous", "amendment")])
@pytest.mark.parametrize("pipeline", ["policy-candidate-batch-v2", "policy-range-normalized-v2"])
def test_nonpolicy_primary_never_reaches_provider_or_paid_replay(roles, pipeline):
    work = _work(roles, context_policy=True)
    ranges = Mock(spec=PolicyRangeRepository)
    ranges.database_url = "postgresql://synthetic.invalid/synthetic"
    ranges.next.return_value = work
    provider, publisher, replay = Mock(), Mock(), Mock()
    budget = Mock(spec=PolicyRequestBudget)
    job = replace(_job(), pipeline_version=pipeline)
    runner = PolicyStructuringJobRunner(
        queue=FakeQueue(),
        evidence_loader=FakeLoader(()),
        provider=provider,
        publisher=publisher,
        request_budget=budget,
        range_repository=ranges,
        replay_repository=replay,
    )
    runner._run_range(job, "worker-a")
    ranges.defer_nonpolicy.assert_called_once_with(
        job, "worker-a", work, sensitive_terms=("Family Member A", "family-member-a")
    )
    assert not provider.mock_calls and not budget.mock_calls and not replay.mock_calls
    assert not publisher.mock_calls
    ranges.save.assert_not_called()


def test_mixed_primary_roles_keep_existing_structuring_path(monkeypatch):
    work = _work(("terms", "policy"))
    ranges = Mock(spec=PolicyRangeRepository)
    ranges.next.return_value = work
    calls = []

    def structure(**kwargs):
        calls.append(kwargs["envelope"])
        return PolicyRangeBatch(
            schema_version="3",
            candidates=(),
            ranges=tuple(
                RangeDisposition(chunk_id=key, outcome="UNRESOLVED", candidate_ids=())
                for key in work.envelope.primary_chunk_ids
            ),
        ), "synthetic-mixed-source-response"

    monkeypatch.setattr("familycare_worker.runner.structure_policy_range", structure)
    runner = PolicyStructuringJobRunner(
        queue=FakeQueue(),
        evidence_loader=FakeLoader(()),
        provider=Mock(),
        publisher=Mock(),
        range_repository=ranges,
    )
    runner._run_range(_job(), "worker-a")
    assert calls == [work.envelope]
    ranges.defer_nonpolicy.assert_not_called()
    ranges.save.assert_called_once()


@pytest.mark.parametrize("fault", ["policy_primary", "empty_primary", "wrong_primary_id"])
def test_deferral_rejects_policy_or_malformed_primary_before_opening_database(fault):
    work = _work(("policy" if fault == "policy_primary" else "terms",))
    if fault != "policy_primary":
        original = work.envelope
        envelope = PolicyRangeEnvelope(
            original.envelope_id,
            original.primary_chunk_ids,
            () if fault == "empty_primary" else (uuid4(),),
            original.evidence,
        )
        work = replace(work, envelope=envelope)
    with pytest.raises(PolicyRangeConflict):
        PolicyRangeRepository("postgresql://synthetic.invalid/synthetic").defer_nonpolicy(
            _job(), "worker-a", work, sensitive_terms=()
        )
