"""Explicit review reservations survive uncertainty and share document/global quotas."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from familycare_worker.ai.provider import ProviderCallMetadata, ProviderUsage
from familycare_worker.guidance_review_jobs import GuidanceReviewQueue

from apps.api.tests.test_guidance_claim_concurrency_integration import (
    SavedGuidanceSource,
    _psycopg_url,
)
from apps.api.tests.test_guidance_claim_concurrency_integration import (
    saved_guidance_source as saved_guidance_source,
)
from apps.api.tests.test_guidance_review_request_integration import _repository, _request

pytestmark = pytest.mark.integration


def _setup(source):
    from familycare_worker.guidance_review_budget import GuidanceReviewBudget

    _request(source)
    job = GuidanceReviewQueue(source.url).claim()
    assert job is not None
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        documents = tuple(
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT d.id FROM documents d JOIN document_versions v ON v.document_id=d.id "
                "JOIN evidence e ON e.document_version_id=v.id "
                "WHERE e.household_space_id=%s AND d.deleted_at IS NULL "
                "ORDER BY d.id LIMIT 2",
                (source.scope.household_space_id,),
            )
        )
    assert len(documents) == 2
    return GuidanceReviewBudget(source.url), job, documents


def _reserve(budget, job, documents, phase="discover"):
    return budget.reserve(
        job,
        phase=phase,
        document_ids=documents,
        fingerprint="a" * 64,
        input_tokens=1200,
        output_tokens=4000,
    )


def test_one_multidocument_http_counts_once_globally_and_for_each_document(
    saved_guidance_source: SavedGuidanceSource,
):
    from familycare_worker.provider_quota import request_counts

    source = saved_guidance_source
    budget, job, documents = _setup(source)
    reservation = _reserve(budget, job, documents)
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        for document in documents:
            assert request_counts(connection, document) == (1, 1)
    budget.finish(reservation, succeeded=False, metadata=None)
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        assert connection.execute(
            "SELECT state,usage_json FROM guidance_review_requests WHERE id=%s", (reservation,)
        ).fetchone() == ("FAILED", None)
        assert request_counts(connection, documents[0]) == (1, 1)


def test_same_phase_cannot_repeat_after_response_loss_or_failure(saved_guidance_source):
    from familycare_worker.guidance_review_budget import ReviewBudgetRejected

    budget, job, documents = _setup(saved_guidance_source)
    reservation = _reserve(budget, job, documents)
    with pytest.raises(ReviewBudgetRejected):
        _reserve(budget, job, documents)
    budget.finish(reservation, succeeded=False, metadata=None)
    with pytest.raises(ReviewBudgetRejected):
        _reserve(budget, job, documents)
    assert (
        _repository(saved_guidance_source)
        .get_job(saved_guidance_source.scope, job.id)
        .http_attempts
        == 1
    )


def test_cancel_stops_reservation_but_retains_late_usage(saved_guidance_source):
    from familycare_worker.guidance_review_budget import ReviewBudgetRejected

    source = saved_guidance_source
    budget, job, documents = _setup(source)
    reservation = _reserve(budget, job, documents)
    _repository(source).cancel(source.scope, job.id)
    with pytest.raises(ReviewBudgetRejected):
        _reserve(budget, job, documents, "compare")
    metadata = ProviderCallMetadata(usage=ProviderUsage(100, 20, 120), model="synthetic-model")
    budget.finish(reservation, succeeded=True, metadata=metadata)
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        assert connection.execute(
            "SELECT usage_json->>'total_tokens' FROM guidance_review_requests WHERE id=%s",
            (reservation,),
        ).fetchone() == ("120",)
    assert _repository(source).get_job(source.scope, job.id).state == "cancelled"


def test_concurrent_phase_reservations_obey_last_shared_daily_slot(saved_guidance_source):
    from familycare_worker.guidance_review_budget import GuidanceReviewBudget, ReviewBudgetRejected

    source = saved_guidance_source
    _, job, documents = _setup(source)
    budget = GuidanceReviewBudget(source.url, daily=1)
    ready = Barrier(2)

    def reserve(phase):
        ready.wait(timeout=10)
        try:
            return _reserve(budget, job, documents, phase)
        except ReviewBudgetRejected:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, ("discover", "compare")))
    assert sum(value is not None for value in results) == 1


def test_unknown_or_other_household_document_and_expired_deadline_rejected(saved_guidance_source):
    from familycare_worker.guidance_review_budget import ReviewBudgetRejected

    source = saved_guidance_source
    budget, job, documents = _setup(source)
    with pytest.raises(ReviewBudgetRejected):
        _reserve(budget, job, (uuid4(),))
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        foreign = connection.execute(
            "SELECT v.document_id FROM evidence e JOIN document_versions v "
            "ON v.id=e.document_version_id WHERE e.household_space_id<>%s LIMIT 1",
            (source.scope.household_space_id,),
        ).fetchone()
        if foreign:
            with pytest.raises(ReviewBudgetRejected):
                _reserve(budget, job, (foreign[0],))
        connection.execute(
            "UPDATE guidance_review_jobs SET deadline_at=clock_timestamp()-interval '1 second' "
            "WHERE id=%s",
            (job.id,),
        )
    with pytest.raises(ReviewBudgetRejected):
        _reserve(budget, job, documents)


def test_job_token_and_document_caps_apply_even_to_failed_calls(saved_guidance_source):
    from familycare_worker.guidance_review_budget import GuidanceReviewBudget, ReviewBudgetRejected

    source = saved_guidance_source
    budget, job, documents = _setup(source)
    with pytest.raises(ReviewBudgetRejected):
        budget.reserve(
            job,
            phase="discover",
            document_ids=documents,
            fingerprint="b" * 64,
            input_tokens=32769,
            output_tokens=4000,
        )
    first = _reserve(budget, job, documents)
    budget.finish(first, succeeded=False, metadata=None)
    with pytest.raises(ReviewBudgetRejected):
        _reserve(GuidanceReviewBudget(source.url, per_document=1), job, documents, "compare")
