"""Reopening equivalent saved guidance never creates a new paid review."""

import psycopg
import pytest
from familycare_api.decisions.errors import DecisionInvalid
from familycare_api.decisions.repository import DecisionRepository
from familycare_api.guidance_review.repository import GuidanceReviewRepository

from apps.api.tests.test_guidance_claim_concurrency_integration import (
    SavedGuidanceSource,
    _psycopg_url,
)
from apps.api.tests.test_guidance_claim_concurrency_integration import (
    saved_guidance_source as saved_guidance_source,
)

pytestmark = pytest.mark.integration


def test_read_lookup_and_equivalent_new_run_reuse_one_original_job(
    saved_guidance_source: SavedGuidanceSource,
):
    source = saved_guidance_source
    repository = GuidanceReviewRepository(source.url)
    assert (
        repository.find_for_run(
            source.scope, source.event_id, run_id=source.run_id, expected_event_version=1
        )
        is None
    )
    first = repository.enqueue(
        source.scope, source.event_id, run_id=source.run_id, expected_event_version=1
    )
    new_run = DecisionRepository(source.url).analyze_medical_event(source.scope, source.event_id)
    assert new_run.run_id != source.run_id
    assert new_run.local_guidance.model_dump(mode="json") == source.snapshot
    reused = repository.enqueue(
        source.scope, source.event_id, run_id=new_run.run_id, expected_event_version=1
    )
    assert reused.id == first.id
    assert reused.decision_run_id == source.run_id
    assert reused.matched_decision_run_id == new_run.run_id
    assert (
        repository.find_for_run(
            source.scope, source.event_id, run_id=new_run.run_id, expected_event_version=1
        )
        == reused
    )
    assert repository.get_job(source.scope, first.id, run_id=new_run.run_id) == reused
    cancelled = repository.cancel(source.scope, first.id, run_id=new_run.run_id)
    assert cancelled.state == "cancelled"
    assert cancelled.matched_decision_run_id == new_run.run_id
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        assert connection.execute("SELECT count(*) FROM guidance_review_jobs").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM guidance_review_requests").fetchone() == (
            0,
        )
        assert connection.execute(
            "SELECT local_guidance_json FROM decision_runs WHERE id=%s", (source.run_id,)
        ).fetchone() == (source.snapshot,)


def test_alias_binding_rejects_changed_context_at_the_same_event_version(
    saved_guidance_source: SavedGuidanceSource,
):
    source = saved_guidance_source
    repository = GuidanceReviewRepository(source.url)
    job = repository.enqueue(
        source.scope, source.event_id, run_id=source.run_id, expected_event_version=1
    )
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        connection.execute(
            "UPDATE family_members SET version=version+1 WHERE household_space_id=%s",
            (source.scope.household_space_id,),
        )
    assert repository.get_job(source.scope, job.id).stale
    with pytest.raises(DecisionInvalid):
        repository.get_job(source.scope, job.id, run_id=source.run_id)
    with pytest.raises(DecisionInvalid):
        repository.find_for_run(
            source.scope, source.event_id, run_id=source.run_id, expected_event_version=1
        )


def test_saved_guidance_resolver_separates_current_claim_and_historical_evidence(
    saved_guidance_source: SavedGuidanceSource,
):
    from familycare_api.guidance_review.binding import resolve_saved_guidance
    from psycopg.rows import dict_row

    source = saved_guidance_source
    with psycopg.connect(_psycopg_url(source.url), row_factory=dict_row) as connection:
        options = dict(decision_run_id=source.run_id, expected_event_version=1)
        current = resolve_saved_guidance(connection, source.scope, source.event_id, **options)
        assert current.model_dump(mode="json") == source.snapshot
        connection.execute(
            "UPDATE medical_events SET version=version+1 WHERE id=%s", (source.event_id,)
        )
        with pytest.raises(DecisionInvalid):
            resolve_saved_guidance(connection, source.scope, source.event_id, **options)
        historic = resolve_saved_guidance(
            connection, source.scope, source.event_id, **options, require_current=False
        )
        assert historic == current
