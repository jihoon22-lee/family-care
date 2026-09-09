"""An explicit review binds a saved local answer without changing its history."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.errors import DecisionInvalid, MedicalEventNotFound
from familycare_api.policies.errors import VersionConflict

from apps.api.tests.test_guidance_claim_concurrency_integration import (
    SavedGuidanceSource,
    _psycopg_url,
)
from apps.api.tests.test_guidance_claim_concurrency_integration import (
    saved_guidance_source as saved_guidance_source,
)

pytestmark = pytest.mark.integration


def _repository(source: SavedGuidanceSource):
    from familycare_api.guidance_review.repository import GuidanceReviewRepository

    return GuidanceReviewRepository(source.url)


def _request(source: SavedGuidanceSource):
    return _repository(source).enqueue(
        source.scope,
        source.event_id,
        run_id=source.run_id,
        expected_event_version=1,
    )


def test_only_explicit_request_queues_review_and_duplicate_reuses_it(
    saved_guidance_source: SavedGuidanceSource,
) -> None:
    source = saved_guidance_source
    repository = _repository(source)
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        assert connection.execute("SELECT count(*) FROM guidance_review_jobs").fetchone() == (0,)
    first = _request(source)
    second = _request(source)
    assert first.id == second.id
    assert first.state == "queued" and first.http_attempts == 0
    assert first.decision_run_id == source.run_id
    assert repository.get_job(source.scope, first.id) == first
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        assert connection.execute("SELECT count(*) FROM guidance_review_jobs").fetchone() == (1,)
        assert connection.execute(
            "SELECT local_guidance_json FROM decision_runs WHERE id=%s", (source.run_id,)
        ).fetchone() == (source.snapshot,)
        assert connection.execute("SELECT count(*) FROM policy_provider_requests").fetchone() == (
            0,
        )


def test_other_household_cannot_request_read_or_cancel_review(
    saved_guidance_source: SavedGuidanceSource,
) -> None:
    source = saved_guidance_source
    repository = _repository(source)
    job = _request(source)
    other = HouseholdScope(household_space_id=uuid4())
    with pytest.raises(MedicalEventNotFound):
        repository.enqueue(other, source.event_id, run_id=source.run_id, expected_event_version=1)
    with pytest.raises(MedicalEventNotFound):
        repository.get_job(other, job.id)
    with pytest.raises(MedicalEventNotFound):
        repository.cancel(other, job.id)
    assert repository.get_job(source.scope, job.id).state == "queued"


def test_stale_version_and_unrelated_run_do_not_enqueue(
    saved_guidance_source: SavedGuidanceSource,
) -> None:
    source = saved_guidance_source
    repository = _repository(source)
    with pytest.raises(VersionConflict):
        repository.enqueue(
            source.scope, source.event_id, run_id=source.run_id, expected_event_version=2
        )
    with pytest.raises(MedicalEventNotFound):
        repository.enqueue(source.scope, source.event_id, run_id=uuid4(), expected_event_version=1)
    # Family scope is part of the parser input revision at the same event version.
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        connection.execute(
            "UPDATE family_members SET version=version+1 WHERE household_space_id=%s",
            (source.scope.household_space_id,),
        )
    with pytest.raises(DecisionInvalid):
        _request(source)


def test_cancelled_review_stays_cancelled_on_duplicate_request(
    saved_guidance_source: SavedGuidanceSource,
) -> None:
    source = saved_guidance_source
    job = _request(source)
    cancelled = _repository(source).cancel(source.scope, job.id)
    assert cancelled.state == "cancelled" and cancelled.http_attempts == 0
    assert _request(source).id == job.id
    assert _request(source).state == "cancelled"


def test_concurrent_explicit_requests_share_one_job(
    saved_guidance_source: SavedGuidanceSource,
) -> None:
    source = saved_guidance_source
    ready = Barrier(2)

    def request():
        ready.wait(timeout=10)
        return _request(source)

    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = [
            future.result(timeout=20) for future in [executor.submit(request) for _ in range(2)]
        ]
    assert jobs[0].id == jobs[1].id


def test_review_retains_immutable_source_inventory_and_exact_event_snapshot(
    saved_guidance_source: SavedGuidanceSource,
) -> None:
    from psycopg.rows import dict_row

    source = saved_guidance_source
    job = _request(source)
    with psycopg.connect(_psycopg_url(source.url), row_factory=dict_row) as connection:
        row = connection.execute(
            "SELECT i.sources_json,i.event_json,j.source_digest FROM guidance_review_inputs i "
            "JOIN guidance_review_jobs j ON j.id=i.review_job_id WHERE i.review_job_id=%s",
            (job.id,),
        ).fetchone()
        assert row is not None
        assert row["sources_json"]["digest_sha256"] == row["source_digest"]
        assert row["sources_json"]["family_member_id"] == str(source.snapshot["family_member_id"])
        assert row["event_json"]["id"] == str(source.event_id)
        assert row["sources_json"]["manifest"]["total_coverage_count"] >= 1
        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                connection.execute(
                    "UPDATE guidance_review_inputs SET sources_json='{}' WHERE review_job_id=%s",
                    (job.id,),
                )
