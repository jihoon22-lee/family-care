"""Draft replay appends immutable provenance without invalidating v2/v3 history."""

import psycopg
import pytest
from familycare_worker import retained_policy
from familycare_worker.policy_jobs import PolicyStructuringJobQueue
from familycare_worker.policy_range_repository import PolicyRangeRepository
from psycopg.types.json import Jsonb

from apps.api.tests.test_metadata_navigation_publication import _migrate
from workers.analyzer.tests.test_policy_range_repository import WORKER, _no_facts
from workers.analyzer.tests.test_policy_structuring_jobs import _psycopg_url
from workers.analyzer.tests.test_retained_field_proof_revision import _contracts, _history
from workers.analyzer.tests.test_retained_policy_resubmission import (
    _assert_original_preserved,
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from workers.analyzer.tests.test_retained_policy_resubmission import (
    retained_source as retained_source,
)

pytestmark = pytest.mark.integration
V4 = "retained-policy-association-v4"
PREVIOUS = "0068_range_field_proof"
REVISION = "0069_policy_draft_replay"
NORMALIZATION = "policy-draft-normalization-v1"


@pytest.fixture(autouse=True)
def historical_v4_producer(monkeypatch, retained_source):
    monkeypatch.setattr(retained_policy, "RETAINED_POLICY_PIPELINE_REVISION", V4)
    # Test migration 0069 itself, independently of later privacy revisions.
    assert _migrate(retained_source.url, "downgrade", REVISION).returncode == 0
    try:
        yield
    finally:
        assert _migrate(retained_source.url, "upgrade", "head").returncode == 0


def _enqueue(sample, **overrides):
    from workers.analyzer.tests.test_retained_policy_resubmission import _enqueue as enqueue

    return enqueue(sample, **{"pipeline_revision": V4, **overrides})


def _target(sample, job_id):
    return retained_policy.RetainedPolicyJobQueue(
        sample.url,
        household_space_id=sample.original.household_space_id,
        job_id=job_id,
        pipeline_revision=V4,
    )


def _legacy_work(sample, monkeypatch, revision):
    with monkeypatch.context() as legacy:
        legacy.setattr(retained_policy, "RETAINED_POLICY_PIPELINE_REVISION", revision)
        old = _enqueue(sample, pipeline_revision=revision)
        queue = retained_policy.RetainedPolicyJobQueue(
            sample.url,
            household_space_id=sample.original.household_space_id,
            job_id=old.id,
            pipeline_revision=revision,
        )
    running = queue.claim_next_job(WORKER)
    assert running is not None
    ranges = PolicyRangeRepository(sample.url)
    work = ranges.next(running, WORKER, sensitive_terms=())
    assert work is not None
    ranges.reject(running, WORKER, work)
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        request_id = connection.execute(
            "INSERT INTO policy_provider_requests(job_id,document_id,fingerprint,state,"
            "response_json,request_id) SELECT %s,document_id,%s,'SUCCEEDED',%s,%s "
            "FROM document_versions WHERE id=%s RETURNING id",
            (
                old.id,
                "c" * 64,
                Jsonb(_no_facts(work).model_dump(mode="json")),
                "synthetic-draft-replay-request",
                sample.original.document_version_id,
            ),
        ).fetchone()[0]
    return old, work, request_id


@pytest.mark.parametrize(
    "revision", ["retained-policy-association-v2", "retained-policy-association-v3"]
)
def test_replay_upgrade_preserves_prior_review_response_and_appends_v4(
    retained_source, monkeypatch, revision
):
    sample = retained_source
    assert _migrate(sample.url, "downgrade", PREVIOUS).returncode == 0
    old, work, _ = _legacy_work(sample, monkeypatch, revision)
    before = _history(sample.url, old.id)
    contracts = _contracts(sample.url, sample.original.household_space_id)
    assert _migrate(sample.url, "upgrade", REVISION).returncode == 0
    new = _enqueue(sample)
    assert new.pipeline_version == V4 and new.id != old.id
    assert _enqueue(sample).id == new.id
    assert PolicyStructuringJobQueue(sample.url).claim_next_job(WORKER) is None
    running = _target(sample, new.id).claim_next_job(WORKER)
    assert running is not None
    fresh = PolicyRangeRepository(sample.url).next(running, WORKER, sensitive_terms=())
    assert fresh is not None
    assert fresh.envelope.envelope_id == work.envelope.envelope_id
    assert fresh.generation_id == work.generation_id
    assert _history(sample.url, old.id) == before
    assert _history(sample.url, new.id)["requests"] == []
    assert (
        _contracts(sample.url, sample.original.household_space_id)["privacy"]
        == contracts["privacy"]
    )
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        assert connection.execute(
            "SELECT policy_structuring_source_current(%s),policy_structuring_source_current(%s)",
            (old.id, new.id),
        ).fetchone() == (True, True)
    _assert_original_preserved(sample)


def test_replay_downgrade_restores_exact_contract_when_empty(retained_source):
    sample = retained_source
    assert _migrate(sample.url, "downgrade", PREVIOUS).returncode == 0
    before = _contracts(sample.url, sample.original.household_space_id)
    try:
        assert _migrate(sample.url, "upgrade", REVISION).returncode == 0
        after = _contracts(sample.url, sample.original.household_space_id)
        assert V4 in after["source"] and V4 in after["guard"]
        assert before["privacy"] == after["privacy"]
        assert _migrate(sample.url, "downgrade", PREVIOUS).returncode == 0
        assert _contracts(sample.url, sample.original.household_space_id) == before
        with pytest.raises(retained_policy.RetainedPolicyConflict):
            _enqueue(sample, pipeline_revision=V4)
    finally:
        assert _migrate(sample.url, "upgrade", REVISION).returncode == 0
    _assert_original_preserved(sample)


def test_v4_job_alone_refuses_downgrade(retained_source):
    sample = retained_source
    new = _enqueue(sample)
    assert new.pipeline_version == V4
    running = _target(sample, new.id).claim_next_job(WORKER)
    assert running is not None
    assert PolicyRangeRepository(sample.url).next(running, WORKER, sensitive_terms=()) is not None
    before = _history(sample.url, new.id)
    refused = _migrate(sample.url, "downgrade", PREVIOUS)
    assert refused.returncode != 0
    assert "policy draft replay history prevents downgrade" in refused.stderr
    assert _history(sample.url, new.id) == before
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            REVISION,
        )


def test_receipt_is_immutable_and_alone_refuses_downgrade(retained_source, monkeypatch):
    sample = retained_source
    old, work, request_id = _legacy_work(sample, monkeypatch, "retained-policy-association-v3")
    before = _history(sample.url, old.id)
    # DB owns referential integrity; the replay repository owns cross-row source admission.
    statement = (
        "INSERT INTO policy_range_replay_sources(job_id,envelope_id,source_job_id,"
        "source_envelope_id,source_provider_request_id,source_response_hash,normalization_revision,"
        "normalized_batch_json,adjustments_json,partial) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
    )
    values = (
        old.id,
        work.envelope.envelope_id,
        old.id,
        work.envelope.envelope_id,
        request_id,
        "a" * 64,
        NORMALIZATION,
        Jsonb(_no_facts(work).model_dump(mode="json")),
        Jsonb([]),
        False,
    )
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        for index, invalid in (
            (5, "not-a-hash"),
            (6, "unsupported-normalization"),
            (3, "missing-range"),
        ):
            invalid_values = list(values)
            invalid_values[index] = invalid
            with pytest.raises(psycopg.IntegrityError), connection.transaction():
                connection.execute(statement, invalid_values)
        connection.execute(statement, values)
        for mutation in (
            "UPDATE policy_range_replay_sources SET partial=true",
            "DELETE FROM policy_range_replay_sources",
        ):
            with pytest.raises(psycopg.IntegrityError), connection.transaction():
                connection.execute(mutation)
    refused = _migrate(sample.url, "downgrade", PREVIOUS)
    assert refused.returncode != 0
    assert "policy draft replay history prevents downgrade" in refused.stderr
    assert _history(sample.url, old.id) == before
    _assert_original_preserved(sample)
