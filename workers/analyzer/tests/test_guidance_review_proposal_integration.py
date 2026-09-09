"""Worker proposals are immutable data; they cannot complete local review themselves."""

import psycopg
import pytest
from familycare_worker.guidance_review_jobs import GuidanceReviewQueue

from apps.api.tests.test_guidance_claim_concurrency_integration import _psycopg_url
from apps.api.tests.test_guidance_claim_concurrency_integration import (
    saved_guidance_source as saved_guidance_source,
)
from apps.api.tests.test_guidance_review_request_integration import _repository, _request
from workers.analyzer.tests.test_guidance_review_budget_integration import _reserve, _setup

pytestmark = [
    pytest.mark.integration,
    pytest.mark.parametrize("saved_guidance_source", ["operational"], indirect=True),
]

EMPTY_PROPOSAL = {
    "schema_revision": "guidance-review-proposals-v1",
    "suggestions": [],
    "reviewed_packet_ids": [],
    "unreviewed_packet_ids": [],
    "omitted_packet_ids": [],
    "omitted_coverage_aliases": [],
}


def test_proposal_preserves_original_and_waits_for_local_validation(saved_guidance_source):
    source = saved_guidance_source
    budget, job, documents = _setup(source)
    reservation = _reserve(budget, job, documents)
    budget.finish(reservation, succeeded=True, metadata=None)
    queue = GuidanceReviewQueue(source.url)
    assert queue.record_proposal(job, EMPTY_PROPOSAL)
    assert _repository(source).get_job(source.scope, job.id).state == "running"
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        assert connection.execute(
            "SELECT proposal_json FROM guidance_review_proposals WHERE review_job_id=%s", (job.id,)
        ).fetchone() == (EMPTY_PROPOSAL,)
        assert connection.execute(
            "SELECT local_guidance_json FROM decision_runs WHERE id=%s", (source.run_id,)
        ).fetchone() == (source.snapshot,)
        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            connection.execute(
                "DELETE FROM guidance_review_proposals WHERE review_job_id=%s", (job.id,)
            )


def test_cancel_and_deadline_reject_late_proposal_without_refunding_call(saved_guidance_source):
    source = saved_guidance_source
    budget, job, documents = _setup(source)
    reservation = _reserve(budget, job, documents)
    _repository(source).cancel(source.scope, job.id)
    budget.finish(reservation, succeeded=True, metadata=None)
    assert not GuidanceReviewQueue(source.url).record_proposal(job, EMPTY_PROPOSAL)
    assert _request(source).state == "cancelled"
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        assert connection.execute("SELECT count(*) FROM guidance_review_proposals").fetchone() == (
            0,
        )
        assert connection.execute("SELECT count(*) FROM guidance_review_requests").fetchone() == (
            1,
        )


def test_changed_event_rejects_proposal_and_fails_saved_lease(saved_guidance_source):
    source = saved_guidance_source
    budget, job, documents = _setup(source)
    reservation = _reserve(budget, job, documents)
    budget.finish(reservation, succeeded=True, metadata=None)
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        connection.execute(
            "UPDATE medical_events SET version=version+1 WHERE id=%s", (source.event_id,)
        )
    assert not GuidanceReviewQueue(source.url).record_proposal(job, EMPTY_PROPOSAL)
    assert GuidanceReviewQueue(source.url).claim() is None
    assert _repository(source).get_job(source.scope, job.id).state == "failed"


def test_load_uses_only_saved_inputs_and_rejects_changed_privacy_scope(saved_guidance_source):
    from familycare_worker.guidance_review_jobs import ReviewQueueUnavailable

    source = saved_guidance_source
    _, job, _ = _setup(source)
    queue = GuidanceReviewQueue(source.url)
    work = queue.load_inputs(job)
    assert work.local_guidance == source.snapshot
    assert work.sources["digest_sha256"] == job.source_digest
    assert work.event["id"] == str(source.event_id)
    assert work.sensitive_terms
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        connection.execute(
            "UPDATE family_members SET version=version+1 WHERE household_space_id=%s",
            (source.scope.household_space_id,),
        )
    with pytest.raises(ReviewQueueUnavailable):
        queue.load_inputs(job)


def test_changed_rider_status_invalidates_pending_transmission(saved_guidance_source):
    from familycare_worker.guidance_review_jobs import ReviewQueueUnavailable

    source = saved_guidance_source
    _, job, _ = _setup(source)
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        changed = connection.execute(
            "UPDATE policy_status_snapshots SET status='unknown' WHERE household_space_id=%s "
            "AND rider_id=%s RETURNING id",
            (source.scope.household_space_id, source.ref.coverage_id),
        ).fetchall()
        assert changed
    with pytest.raises(ReviewQueueUnavailable):
        GuidanceReviewQueue(source.url).load_inputs(job)
