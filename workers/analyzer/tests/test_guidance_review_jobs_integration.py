"""Review leases fail independently and cannot restart cancelled or expired work."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import psycopg
import pytest

from apps.api.tests.test_guidance_claim_concurrency_integration import (
    SavedGuidanceSource,
    _psycopg_url,
)
from apps.api.tests.test_guidance_claim_concurrency_integration import (
    saved_guidance_source as saved_guidance_source,
)
from apps.api.tests.test_guidance_review_request_integration import _repository, _request

pytestmark = pytest.mark.integration


def _queue(source: SavedGuidanceSource):
    from familycare_worker.guidance_review_jobs import GuidanceReviewQueue

    return GuidanceReviewQueue(source.url)


def test_two_workers_claim_one_review_and_failure_keeps_local_answer(
    saved_guidance_source: SavedGuidanceSource,
) -> None:
    source = saved_guidance_source
    created = _request(source)
    queue = _queue(source)
    ready = Barrier(2)

    def claim():
        ready.wait(timeout=10)
        return queue.claim()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim) for _ in range(2)]
        claims = [future.result(timeout=20) for future in futures]
    jobs = [job for job in claims if job is not None]
    assert len(jobs) == 1 and jobs[0].id == created.id
    assert queue.fail(jobs[0], "REVIEW_PROVIDER_UNAVAILABLE")
    failed = _repository(source).get_job(source.scope, created.id)
    assert failed.state == "failed" and failed.error_code == "REVIEW_PROVIDER_UNAVAILABLE"
    assert queue.claim() is None
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        assert connection.execute(
            "SELECT local_guidance_json FROM decision_runs WHERE id=%s", (source.run_id,)
        ).fetchone() == (source.snapshot,)


def test_cancelled_lease_rejects_late_failure_and_remains_cancelled(
    saved_guidance_source: SavedGuidanceSource,
) -> None:
    source = saved_guidance_source
    created = _request(source)
    queue = _queue(source)
    job = queue.claim()
    assert job is not None
    _repository(source).cancel(source.scope, created.id)
    assert queue.fail(job, "REVIEW_PROVIDER_UNAVAILABLE") is False
    assert _repository(source).get_job(source.scope, created.id).state == "cancelled"
    assert queue.claim() is None


def test_expired_lease_is_terminal_and_cannot_send_a_second_time(
    saved_guidance_source: SavedGuidanceSource,
) -> None:
    source = saved_guidance_source
    created = _request(source)
    queue = _queue(source)
    job = queue.claim()
    assert job is not None
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        connection.execute(
            "UPDATE guidance_review_jobs SET "
            "lease_expires_at=clock_timestamp()-interval '1 second' "
            "WHERE id=%s",
            (created.id,),
        )
    assert queue.claim() is None
    assert queue.fail(job, "REVIEW_PROVIDER_UNAVAILABLE") is False
    expired = _repository(source).get_job(source.scope, created.id)
    assert expired.state == "failed" and expired.error_code == "REVIEW_LEASE_EXPIRED"
    assert _request(source).id == created.id
    assert _request(source).state == "failed"


def test_changed_event_is_not_claimed_and_old_run_stays_readable(
    saved_guidance_source: SavedGuidanceSource,
) -> None:
    source = saved_guidance_source
    created = _request(source)
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        connection.execute(
            "UPDATE medical_events SET version=version+1 WHERE id=%s", (source.event_id,)
        )
    assert _queue(source).claim() is None
    failed = _repository(source).get_job(source.scope, created.id)
    assert failed.state == "failed" and failed.error_code == "REVIEW_INPUT_CHANGED"
    assert failed.stale


def test_changed_event_member_does_not_prevent_preserving_failed_review_history(
    saved_guidance_source: SavedGuidanceSource,
) -> None:
    source = saved_guidance_source
    created = _request(source)
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        connection.execute(
            "UPDATE medical_events SET family_member_id=(SELECT id FROM family_members "
            "WHERE household_space_id=medical_events.household_space_id "
            "AND id<>medical_events.family_member_id ORDER BY id LIMIT 1),version=version+1 "
            "WHERE id=%s",
            (source.event_id,),
        )
    assert _queue(source).claim() is None
    failed = _repository(source).get_job(source.scope, created.id)
    assert failed.state == "failed" and failed.error_code == "REVIEW_INPUT_CHANGED"
    assert failed.stale
