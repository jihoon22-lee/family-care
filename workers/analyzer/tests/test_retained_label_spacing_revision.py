"""A new local label reader appends work without resetting earlier provider history."""

import psycopg
import pytest
from familycare_worker import retained_policy
from familycare_worker.policy_range_repository import PolicyRangeRepository
from familycare_worker.runtime_schema import SUPPORTED_SCHEMA_REVISION

from apps.api.tests.test_metadata_navigation_publication import _migrate
from workers.analyzer.tests.test_policy_range_repository import WORKER
from workers.analyzer.tests.test_policy_structuring_jobs import _psycopg_url
from workers.analyzer.tests.test_retained_field_proof_revision import _contracts, _history
from workers.analyzer.tests.test_retained_policy_resubmission import (
    _assert_original_preserved,
    _enqueue,
    _target,
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from workers.analyzer.tests.test_retained_policy_resubmission import (
    retained_source as retained_source,
)
from workers.analyzer.tests.test_retained_replay_revision import _legacy_work

pytestmark = pytest.mark.integration
V4 = "retained-policy-association-v4"
V5 = "retained-policy-association-v5"
PREVIOUS = "0073_metadata_header_regions"


@pytest.fixture(autouse=True)
def historical_v5_producer(monkeypatch):
    monkeypatch.setattr(retained_policy, "RETAINED_POLICY_PIPELINE_REVISION", V5)


def test_label_reader_revision_preserves_v4_history_and_creates_one_new_job(
    retained_source, monkeypatch
):
    sample = retained_source
    assert _migrate(sample.url, "downgrade", PREVIOUS).returncode == 0
    from familycare_worker import policy_range_repository
    from familycare_worker.policy_draft_replay import PolicyDraftReplayRepository
    from familycare_worker.policy_range_repository import PolicyRangeConflict

    with monkeypatch.context() as legacy:
        legacy.setattr(
            policy_range_repository, "MINIMIZATION_REVISION", "source-window-minimizer-v3"
        )
        old, work, request = _legacy_work(sample, legacy, V4)
    before = _history(sample.url, old.id)
    assert _migrate(sample.url, "upgrade", "head").returncode == 0
    new = _enqueue(sample)
    assert new.pipeline_version == V5 and new.id != old.id
    assert _enqueue(sample).id == new.id
    running = _target(sample, new.id).claim_next_job(WORKER)
    assert running is not None
    fresh = PolicyRangeRepository(sample.url).next(running, WORKER, sensitive_terms=())
    assert fresh is not None and fresh.generation_id == work.generation_id
    assert fresh.envelope == work.envelope
    assert _history(sample.url, old.id) == before
    current = _history(sample.url, new.id)
    assert current["requests"] == []
    assert current["plan"]["privacy_fingerprint"] != before["plan"]["privacy_fingerprint"]
    with pytest.raises(PolicyRangeConflict):
        PolicyDraftReplayRepository(sample.url, source_provider_request_id=request).prepare(
            running, WORKER, fresh
        )
    _assert_original_preserved(sample)


def test_empty_label_revision_downgrade_restores_the_exact_previous_functions(retained_source):
    sample = retained_source
    assert _migrate(sample.url, "downgrade", PREVIOUS).returncode == 0
    before = _contracts(sample.url, sample.original.household_space_id)
    try:
        assert _migrate(sample.url, "upgrade", "head").returncode == 0
        after = _contracts(sample.url, sample.original.household_space_id)
        assert V5 in after["source"] and V5 in after["guard"]
        assert before["privacy"] != after["privacy"]
        assert _migrate(sample.url, "downgrade", PREVIOUS).returncode == 0
        assert _contracts(sample.url, sample.original.household_space_id) == before
        with pytest.raises(retained_policy.RetainedPolicyConflict):
            _enqueue(sample, pipeline_revision=V5)
    finally:
        assert _migrate(sample.url, "upgrade", "head").returncode == 0
    _assert_original_preserved(sample)


def test_new_label_processing_history_prevents_downgrade(retained_source):
    sample = retained_source
    new = _enqueue(sample)
    running = _target(sample, new.id).claim_next_job(WORKER)
    assert running is not None
    assert PolicyRangeRepository(sample.url).next(running, WORKER, sensitive_terms=()) is not None
    before = _history(sample.url, new.id)
    refused = _migrate(sample.url, "downgrade", PREVIOUS)
    assert refused.returncode != 0
    assert "policy label spacing history prevents downgrade" in refused.stderr
    assert _history(sample.url, new.id) == before
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            SUPPORTED_SCHEMA_REVISION,
        )
    _assert_original_preserved(sample)
