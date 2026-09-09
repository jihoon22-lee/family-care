"""A new privacy revision never reuses or rewrites a retained legacy packet."""

import psycopg
import pytest
from psycopg.rows import dict_row

from apps.api.tests.test_metadata_navigation_publication import _migrate
from workers.analyzer.tests.test_policy_range_repository import WORKER
from workers.analyzer.tests.test_policy_structuring_jobs import _psycopg_url
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

pytestmark = pytest.mark.integration


def test_new_privacy_processing_revision_is_available(retained_source):
    new = _enqueue(retained_source, pipeline_revision="retained-policy-association-v2")
    assert new.pipeline_version == "retained-policy-association-v2"
    _assert_original_preserved(retained_source)


def test_privacy_upgrade_preserves_old_job_and_packet_but_requires_fresh_processing(
    retained_source,
    monkeypatch,
):
    from familycare_worker import policy_range_repository, retained_policy

    sample = retained_source
    assert _migrate(sample.url, "downgrade", "0065_retained_policy_jobs").returncode == 0
    with monkeypatch.context() as legacy:
        legacy.setattr(
            retained_policy, "RETAINED_POLICY_PIPELINE_REVISION", "retained-policy-association-v1"
        )
        legacy.setattr(
            policy_range_repository, "MINIMIZATION_REVISION", "source-window-minimizer-v2"
        )
        old = _enqueue(sample, pipeline_revision="retained-policy-association-v1")
        queue = retained_policy.RetainedPolicyJobQueue(
            sample.url,
            household_space_id=sample.original.household_space_id,
            job_id=old.id,
            pipeline_revision="retained-policy-association-v1",
        )
        running = queue.claim_next_job(WORKER)
        assert running is not None
        assert (
            policy_range_repository.PolicyRangeRepository(sample.url).next(
                running, WORKER, sensitive_terms=()
            )
            is not None
        )
        queue.fail_job(old.id, WORKER, "POLICY_STRUCTURING_UNAVAILABLE")
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        old_job = connection.execute(
            "SELECT to_jsonb(j) AS value FROM policy_structuring_jobs j WHERE id=%s",
            (old.id,),
        ).fetchone()["value"]
        old_plan = connection.execute(
            "SELECT to_jsonb(p) AS value FROM document_policy_range_plans p WHERE job_id=%s",
            (old.id,),
        ).fetchone()["value"]
        old_privacy = connection.execute(
            "SELECT terms_semantic_privacy_digest(%s) AS value",
            (sample.original.household_space_id,),
        ).fetchone()["value"]
    assert _migrate(sample.url, "upgrade", "head").returncode == 0
    new = _enqueue(sample)
    assert new.id != old.id
    assert _target(sample, old.id).claim_next_job(WORKER) is None
    running = _target(sample, new.id).claim_next_job(WORKER)
    assert running is not None
    work = policy_range_repository.PolicyRangeRepository(sample.url).next(
        running, WORKER, sensitive_terms=()
    )
    assert work is not None and work.generation_id == sample.generation
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        assert (
            connection.execute(
                "SELECT to_jsonb(j) AS value FROM policy_structuring_jobs j WHERE id=%s",
                (old.id,),
            ).fetchone()["value"]
            == old_job
        )
        assert (
            connection.execute(
                "SELECT to_jsonb(p) AS value FROM document_policy_range_plans p WHERE job_id=%s",
                (old.id,),
            ).fetchone()["value"]
            == old_plan
        )
        assert (
            connection.execute(
                "SELECT privacy_fingerprint FROM document_policy_range_plans WHERE job_id=%s",
                (new.id,),
            ).fetchone()["privacy_fingerprint"]
            != old_plan["privacy_fingerprint"]
        )
        assert (
            connection.execute(
                "SELECT terms_semantic_privacy_digest(%s) AS value",
                (sample.original.household_space_id,),
            ).fetchone()["value"]
            != old_privacy
        )
    _assert_original_preserved(sample)
    refused = _migrate(sample.url, "downgrade", "0065_retained_policy_jobs")
    assert refused.returncode != 0
    assert "privacy revision history prevents downgrade" in refused.stderr
