"""New certificate-title receipts preserve old proofs and reject cross-revision reuse."""

import psycopg
import pytest
from familycare_worker.policy_draft_replay import PolicyDraftReplayRepository
from familycare_worker.policy_range_repository import PolicyRangeConflict, PolicyRangeRepository
from familycare_worker.retained_policy import RETAINED_POLICY_PIPELINE_REVISION
from familycare_worker.runtime_schema import SUPPORTED_SCHEMA_REVISION
from psycopg.rows import dict_row

from apps.api.tests.test_metadata_navigation_publication import _migrate
from workers.analyzer.tests.test_policy_range_repository import WORKER
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
PREVIOUS = "0075_initial_policy_drafts"


def test_empty_certificate_revision_restores_previous_admission(retained_source):
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
def test_certificate_pipeline_history_prevents_downgrade(retained_source, mode):
    sample = retained_source
    if mode == "retained":
        assert _enqueue(sample).pipeline_version == RETAINED_POLICY_PIPELINE_REVISION
    else:
        with psycopg.connect(_psycopg_url(sample.url)) as connection:
            connection.execute(
                "UPDATE policy_structuring_jobs SET pipeline_version='policy-range-normalized-v2' "
                "WHERE id=%s",
                (sample.original.id,),
            )
    refused = _migrate(sample.url, "downgrade", PREVIOUS)
    assert refused.returncode != 0
    assert "certificate title history prevents downgrade" in refused.stderr
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            SUPPORTED_SCHEMA_REVISION,
        )
        assert connection.execute("SELECT count(*) FROM policy_provider_requests").fetchone() == (
            0,
        )


def test_v7_refuses_a_legacy_receipt_even_for_the_same_exact_raw_source(
    retained_source, monkeypatch
):
    sample = retained_source
    old, old_work, request = _legacy_work(sample, monkeypatch, "retained-policy-association-v6")
    new = _enqueue(sample)
    running = _target(sample, new.id).claim_next_job(WORKER)
    assert running is not None
    work = PolicyRangeRepository(sample.url).next(running, WORKER, sensitive_terms=())
    assert work is not None and work.envelope == old_work.envelope
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        before = connection.execute(
            "SELECT to_jsonb(r) AS row FROM policy_provider_requests r WHERE job_id=%s", (old.id,)
        ).fetchall()
        connection.execute(
            "INSERT INTO policy_range_replay_sources(job_id,envelope_id,source_job_id,"
            "source_envelope_id,source_provider_request_id,source_response_hash,"
            "normalization_revision,normalized_batch_json,adjustments_json,partial,origin) "
            "VALUES(%s,%s,%s,%s,%s,%s,'policy-draft-normalization-v1','{}','[]',false,'replay')",
            (
                running.id,
                work.envelope.envelope_id,
                old.id,
                old_work.envelope.envelope_id,
                request,
                "a" * 64,
            ),
        )
    with pytest.raises(PolicyRangeConflict):
        PolicyDraftReplayRepository(sample.url, source_provider_request_id=request).prepare(
            running, WORKER, work
        )
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        assert (
            connection.execute(
                "SELECT to_jsonb(r) AS row FROM policy_provider_requests r WHERE job_id=%s",
                (old.id,),
            ).fetchall()
            == before
        )
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM policy_provider_requests WHERE job_id=%s", (running.id,)
            ).fetchone()["n"]
            == 0
        )
    _assert_original_preserved(sample)
