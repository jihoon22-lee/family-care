"""Initial normalization appends a version without rewriting earlier processing."""

import psycopg
import pytest
from familycare_worker.policy_draft_replay import PolicyDraftReplayRepository
from familycare_worker.policy_range_repository import PolicyRangeConflict, PolicyRangeRepository
from familycare_worker.retained_policy import RETAINED_POLICY_PIPELINE_REVISION
from familycare_worker.runtime_schema import SUPPORTED_SCHEMA_REVISION

from apps.api.tests.test_metadata_navigation_publication import _migrate
from workers.analyzer.tests.test_retained_field_proof_revision import _contracts
from workers.analyzer.tests.test_retained_policy_resubmission import (
    _assert_original_preserved,
    _enqueue,
    _psycopg_url,
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
PREVIOUS = "0074_policy_label_spacing"


def test_empty_initial_revision_restores_exact_previous_functions(retained_source):
    sample = retained_source
    assert _migrate(sample.url, "downgrade", PREVIOUS).returncode == 0
    before = _contracts(sample.url, sample.original.household_space_id)
    try:
        assert _migrate(sample.url, "upgrade", "head").returncode == 0
        after = _contracts(sample.url, sample.original.household_space_id)
        assert RETAINED_POLICY_PIPELINE_REVISION in after["source"]
        assert RETAINED_POLICY_PIPELINE_REVISION in after["guard"]
        assert after["privacy"] == before["privacy"]
        assert _migrate(sample.url, "downgrade", PREVIOUS).returncode == 0
        assert _contracts(sample.url, sample.original.household_space_id) == before
    finally:
        assert _migrate(sample.url, "upgrade", "head").returncode == 0
    _assert_original_preserved(sample)


@pytest.mark.parametrize("mode", ["automatic", "retained"])
def test_new_pipeline_history_blocks_downgrade_before_any_provider_request(retained_source, mode):
    sample = retained_source
    if mode == "retained":
        job = _enqueue(sample)
        assert job.pipeline_version == RETAINED_POLICY_PIPELINE_REVISION
    else:
        with psycopg.connect(_psycopg_url(sample.url)) as connection:
            connection.execute(
                "UPDATE policy_structuring_jobs SET pipeline_version='policy-range-normalized-v1' "
                "WHERE id=%s",
                (sample.original.id,),
            )
    refused = _migrate(sample.url, "downgrade", PREVIOUS)
    assert refused.returncode != 0
    assert "initial policy draft history prevents downgrade" in refused.stderr
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            SUPPORTED_SCHEMA_REVISION,
        )
        assert connection.execute("SELECT count(*) FROM policy_provider_requests").fetchone() == (
            0,
        )
    if mode == "retained":
        _assert_original_preserved(sample)


@pytest.mark.parametrize(
    "source_revision,privacy",
    [
        ("retained-policy-association-v4", "source-window-minimizer-v4"),
        ("retained-policy-association-v5", "source-window-minimizer-v3"),
    ],
)
def test_v6_replay_rejects_wrong_source_revision_or_privacy(
    retained_source, monkeypatch, source_revision, privacy
):
    from familycare_worker import policy_range_repository

    from workers.analyzer.tests.test_policy_range_repository import WORKER

    sample = retained_source
    with monkeypatch.context() as historical:
        historical.setattr(policy_range_repository, "MINIMIZATION_REVISION", privacy)
        old, _, request = _legacy_work(sample, historical, source_revision)
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        before = connection.execute(
            "SELECT to_jsonb(r) FROM policy_provider_requests r WHERE job_id=%s", (old.id,)
        ).fetchall()
    new = _enqueue(sample)
    running = _target(sample, new.id).claim_next_job(WORKER)
    assert running is not None
    work = PolicyRangeRepository(sample.url).next(running, WORKER, sensitive_terms=())
    assert work is not None
    with pytest.raises(PolicyRangeConflict):
        PolicyDraftReplayRepository(sample.url, source_provider_request_id=request).prepare(
            running,
            WORKER,
            work,
        )
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        assert (
            connection.execute(
                "SELECT to_jsonb(r) FROM policy_provider_requests r WHERE job_id=%s", (old.id,)
            ).fetchall()
            == before
        )
        assert connection.execute(
            "SELECT count(*) FROM policy_provider_requests WHERE job_id=%s", (new.id,)
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM policy_range_replay_sources WHERE job_id=%s", (new.id,)
        ).fetchone() == (0,)
    _assert_original_preserved(sample)
