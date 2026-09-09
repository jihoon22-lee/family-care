"""Field-proof reprocessing appends work without invalidating private v2 evidence."""

from types import SimpleNamespace

import psycopg
import pytest
from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
from familycare_worker import retained_policy
from familycare_worker.policy_jobs import PolicyStructuringJobQueue
from familycare_worker.policy_range_repository import PolicyRangeRepository
from familycare_worker.runtime_schema import SUPPORTED_SCHEMA_REVISION
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests.test_metadata_navigation_publication import _migrate
from apps.api.tests.test_native_range_enrollment_integration import (
    _retain_native,
    enrollment_database,  # noqa: F401
)
from apps.api.tests.test_native_range_enrollment_integration import (
    native_database as native_database,
)
from apps.api.tests.test_retained_policy_publication_integration import _initial
from workers.analyzer.tests.test_policy_range_repository import WORKER, _no_facts
from workers.analyzer.tests.test_policy_structuring_jobs import _psycopg_url
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

V2 = "retained-policy-association-v2"
V3 = "retained-policy-association-v3"


@pytest.fixture(autouse=True)
def historical_v3_producer(monkeypatch):
    monkeypatch.setattr(retained_policy, "RETAINED_POLICY_PIPELINE_REVISION", V3)


def _enqueue(sample, **overrides):
    from workers.analyzer.tests.test_retained_policy_resubmission import _enqueue as enqueue

    return enqueue(sample, **{"pipeline_revision": V3, **overrides})


def _target(sample, job_id):
    return retained_policy.RetainedPolicyJobQueue(
        sample.url,
        household_space_id=sample.original.household_space_id,
        job_id=job_id,
        pipeline_revision=V3,
    )


def _v2_job(sample, monkeypatch):
    # Preserve an old producer explicitly; the current API only creates v3 work.
    with monkeypatch.context() as legacy:
        legacy.setattr(retained_policy, "RETAINED_POLICY_PIPELINE_REVISION", V2)
        old = _enqueue(sample, pipeline_revision=V2)
        queue = retained_policy.RetainedPolicyJobQueue(
            sample.url,
            household_space_id=sample.original.household_space_id,
            job_id=old.id,
            pipeline_revision=V2,
        )
    return old, queue


def _history(url, job_id):
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        return {
            "job": connection.execute(
                "SELECT to_jsonb(j) AS value FROM policy_structuring_jobs j WHERE id=%s",
                (job_id,),
            ).fetchone()["value"],
            "plan": connection.execute(
                "SELECT to_jsonb(p) AS value FROM document_policy_range_plans p WHERE job_id=%s",
                (job_id,),
            ).fetchone()["value"],
            "ranges": connection.execute(
                "SELECT to_jsonb(r) AS value FROM document_policy_ranges r "
                "WHERE job_id=%s ORDER BY position",
                (job_id,),
            ).fetchall(),
            "requests": connection.execute(
                "SELECT to_jsonb(r) AS value FROM policy_provider_requests r "
                "WHERE job_id=%s ORDER BY id",
                (job_id,),
            ).fetchall(),
        }


def _contracts(url, household_id):
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        return connection.execute(
            "SELECT pg_get_functiondef('policy_structuring_source_current(uuid)'::regprocedure) "
            "AS source,pg_get_functiondef('protect_retained_policy_job()'::regprocedure) AS guard,"
            "terms_semantic_privacy_digest(%s) AS privacy",
            (household_id,),
        ).fetchone()


def test_field_proof_revision_appends_one_targeted_job(retained_source):
    sample = retained_source
    new = _enqueue(sample, pipeline_revision=V3)
    assert new.pipeline_version == V3 and new.id != sample.original.id
    assert new.source_generation_id == sample.generation
    assert _enqueue(sample).id == new.id
    assert PolicyStructuringJobQueue(sample.url).claim_next_job(WORKER) is None
    running = _target(sample, new.id).claim_next_job(WORKER)
    assert running is not None and running.id == new.id
    _assert_original_preserved(sample)


def test_upgrade_preserves_v2_review_plan_and_appends_fresh_v3_work(retained_source, monkeypatch):
    sample = retained_source
    assert _migrate(sample.url, "downgrade", "0067_metadata_lineage").returncode == 0
    old, queue = _v2_job(sample, monkeypatch)
    running = queue.claim_next_job(WORKER)
    assert running is not None
    ranges = PolicyRangeRepository(sample.url)
    old_work = ranges.next(running, WORKER, sensitive_terms=())
    assert old_work is not None
    ranges.reject(running, WORKER, old_work)
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        connection.execute(
            "INSERT INTO policy_provider_requests(job_id,document_id,fingerprint,state,"
            "response_json,request_id) SELECT %s,document_id,%s,'SUCCEEDED',%s,%s "
            "FROM document_versions WHERE id=%s",
            (
                old.id,
                "b" * 64,
                Jsonb(_no_facts(old_work).model_dump(mode="json")),
                "synthetic-old-field-request",
                sample.original.document_version_id,
            ),
        )
    before = _history(sample.url, old.id)
    assert before["ranges"][0]["value"]["state"] == "REVIEW"
    assert _migrate(sample.url, "upgrade", "head").returncode == 0
    new = _enqueue(sample)
    assert new.pipeline_version == V3 and new.id not in {old.id, sample.original.id}
    assert new.source_generation_id == old.source_generation_id == sample.generation
    assert _enqueue(sample).id == new.id
    assert PolicyStructuringJobQueue(sample.url).claim_next_job(WORKER) is None
    assert _target(sample, old.id).claim_next_job(WORKER) is None
    current = _target(sample, new.id).claim_next_job(WORKER)
    assert current is not None
    new_work = ranges.next(current, WORKER, sensitive_terms=())
    assert new_work is not None and new_work.generation_id == old_work.generation_id
    assert new_work.envelope.envelope_id == old_work.envelope.envelope_id
    assert _history(sample.url, old.id) == before
    after = _history(sample.url, new.id)
    assert after["plan"]["privacy_fingerprint"] == before["plan"]["privacy_fingerprint"]
    assert all(row["value"]["state"] == "PENDING" for row in after["ranges"])
    assert all(row["value"]["result_json"] is None for row in after["ranges"])
    assert after["requests"] == []
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        assert connection.execute(
            "SELECT policy_structuring_source_current(%s),policy_structuring_source_current(%s)",
            (old.id, new.id),
        ).fetchone() == (True, True)
        for job_id in (old.id, new.id):
            with pytest.raises(psycopg.IntegrityError), connection.transaction():
                connection.execute(
                    "UPDATE policy_structuring_jobs SET pipeline_version=%s WHERE id=%s",
                    (V3 if job_id == old.id else V2, job_id),
                )
    assert _history(sample.url, old.id) == before
    _assert_original_preserved(sample)


def test_current_python_producer_does_not_create_legacy_v2_work(retained_source):
    sample = retained_source
    with pytest.raises(retained_policy.RetainedPolicyConflict):
        _enqueue(sample, pipeline_revision=V2)
    with pytest.raises(retained_policy.RetainedPolicyConflict):
        retained_policy.RetainedPolicyJobQueue(
            sample.url,
            household_space_id=sample.original.household_space_id,
            job_id=sample.original.id,
            pipeline_revision=V2,
        )


def test_field_revision_downgrade_restores_exact_v2_only_contract_without_history(retained_source):
    sample = retained_source
    assert _migrate(sample.url, "downgrade", "0067_metadata_lineage").returncode == 0
    before = _contracts(sample.url, sample.original.household_space_id)
    with pytest.raises(retained_policy.RetainedPolicyConflict):
        _enqueue(sample, pipeline_revision=V3)
    try:
        assert _migrate(sample.url, "upgrade", "head").returncode == 0
        after = _contracts(sample.url, sample.original.household_space_id)
        assert after["privacy"] == before["privacy"]
        assert "retained-policy-association-v3" in after["source"]
        assert "retained-policy-association-v3" in after["guard"]
        assert _migrate(sample.url, "downgrade", "0067_metadata_lineage").returncode == 0
        assert _contracts(sample.url, sample.original.household_space_id) == before
        with pytest.raises(retained_policy.RetainedPolicyConflict):
            _enqueue(sample, pipeline_revision=V3)
    finally:
        assert _migrate(sample.url, "upgrade", "head").returncode == 0
    _assert_original_preserved(sample)


def test_v3_history_refuses_downgrade_without_changing_job_or_contracts(retained_source):
    sample = retained_source
    new = _enqueue(sample)
    before = _contracts(sample.url, sample.original.household_space_id)
    refused = _migrate(sample.url, "downgrade", "0067_metadata_lineage")
    assert refused.returncode != 0
    assert "range field proof history prevents downgrade" in refused.stderr
    assert _contracts(sample.url, sample.original.household_space_id) == before
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            SUPPORTED_SCHEMA_REVISION,
        )
        assert connection.execute(
            "SELECT pipeline_version,state FROM policy_structuring_jobs WHERE id=%s", (new.id,)
        ).fetchone() == (V3, "queued")
    _assert_original_preserved(sample)


def test_preexisting_v2_candidates_keep_api_publication_authority(native_database, monkeypatch):
    url, original = native_database
    assert _migrate(url, "downgrade", "0067_metadata_lineage").returncode == 0
    first = _initial(native_database)
    sample = SimpleNamespace(url=url, original=original, generation=first.generation_id)
    old, queue = _v2_job(sample, monkeypatch)
    running = queue.claim_next_job(WORKER)
    assert running is not None
    _retain_native(url, running)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        ledger = connection.execute(
            "SELECT to_jsonb(p) AS value FROM policy_contracts p WHERE household_space_id=%s",
            (original.household_space_id,),
        ).fetchall()
        assert connection.execute(
            "SELECT count(*) AS count FROM policy_range_candidate_sources WHERE job_id=%s",
            (old.id,),
        ).fetchone() == {"count": 2}
    before = _history(url, old.id)
    assert _migrate(url, "upgrade", "head").returncode == 0
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT policy_structuring_source_current(%s)", (old.id,)
        ).fetchone() == (True,)
    assert RangeEnrollmentProjector(url).project_pending() == 2
    assert _history(url, old.id) == before
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute(
                "SELECT to_jsonb(p) AS value FROM policy_contracts p WHERE household_space_id=%s",
                (original.household_space_id,),
            ).fetchall()
            == ledger
        )
        assert connection.execute(
            "SELECT count(*) AS count FROM range_enrollment_publications p "
            "JOIN policy_range_candidate_sources s "
            "ON s.candidate_version_id=p.source_candidate_version_id "
            "WHERE s.job_id=%s",
            (old.id,),
        ).fetchone() == {"count": 2}
