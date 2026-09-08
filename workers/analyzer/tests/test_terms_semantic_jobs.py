"""Synthetic PostgreSQL ownership and candidate-only completion for terms work."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from familycare_api.terms_knowledge.repository import TermsSemanticRepository
from familycare_api.terms_knowledge.work_repository import TermsSemanticWorkRepository
from familycare_worker.ai.terms_structurer import structure_terms_region
from familycare_worker.generated_terms_semantic import TermsSemanticKnowledge
from familycare_worker.terms_semantic_jobs import TermsSemanticJobQueue, TermsSemanticWorkConflict

from apps.api.tests.test_terms_knowledge_repository import (
    seeded_policy_database,  # noqa: F401
    semantic_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from workers.analyzer.tests.test_document_structure_repository import _psycopg_url
from workers.analyzer.tests.test_terms_structurer import FakeProvider

pytestmark = pytest.mark.integration


@pytest.fixture()
def terms_jobs(request):
    url, scope, edition = request.getfixturevalue("semantic_database")
    plan = TermsSemanticRepository(url).source_plan(scope, edition)
    ids = [r.region_id for r in plan.snapshot.layout.regions if r.kind == "article"]
    producer = TermsSemanticWorkRepository(url)
    requests = [producer.enqueue(scope, edition, (key,)) for key in ids]
    yield url, scope, edition, requests
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute("TRUNCATE terms_semantic_jobs CASCADE")


def candidate(job):
    graph, _ = structure_terms_region(
        envelope=job.envelope, provider=FakeProvider(), model="synthetic-model"
    )
    return graph


def expire(url, job_id):
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE terms_semantic_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' "
            "WHERE id=%s",
            (job_id,),
        )


def count_candidates(url):
    with psycopg.connect(_psycopg_url(url)) as connection:
        return connection.execute("SELECT count(*) FROM terms_semantic_candidates").fetchone()[0]


def test_concurrent_claims_are_distinct_and_old_token_cannot_complete(terms_jobs):
    url, _, _, _ = terms_jobs
    queue = TermsSemanticJobQueue(url)
    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = list(executor.map(queue.claim, ["worker-a", "worker-b"]))
    assert all(jobs) and jobs[0].id != jobs[1].id
    assert all(job.attempts == 1 and job.lease_token for job in jobs)
    first = jobs[0]
    expire(url, first.id)
    second = queue.claim("worker-a")
    assert second.id == first.id and second.attempts == 2
    assert second.lease_token != first.lease_token
    assert not queue.heartbeat(first, "worker-a")
    with pytest.raises(TermsSemanticWorkConflict):
        queue.complete(first, "worker-a", candidate(first))
    assert count_candidates(url) == 0
    assert queue.heartbeat(second, "worker-a")


def test_completion_is_atomic_candidate_only_and_rejects_late_or_foreign_owner(terms_jobs):
    url, _, _, _ = terms_jobs
    queue = TermsSemanticJobQueue(url)
    job = queue.claim("worker-a")
    graph = candidate(job)
    for foreign in [replace(job, household_space_id=uuid4()), replace(job, lease_token=uuid4())]:
        with pytest.raises(TermsSemanticWorkConflict):
            queue.complete(foreign, "worker-a", graph)
    with pytest.raises(TermsSemanticWorkConflict):
        queue.complete(job, "worker-b", graph)
    assert count_candidates(url) == 0
    result = queue.complete(job, "worker-a", graph)
    stored = queue.get_job(job.id)
    assert stored.state == "succeeded" and stored.candidate_id == result
    assert stored.lease_token is None and stored.lease_owner is None
    assert count_candidates(url) == 1
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert (
            connection.execute("SELECT count(*) FROM terms_semantic_publications").fetchone()[0]
            == 0
        )
    with pytest.raises(TermsSemanticWorkConflict):
        queue.complete(job, "worker-a", graph)
    assert count_candidates(url) == 1


@pytest.mark.parametrize(
    "fault", ["source", "citation", "region", "complete_manifest", "extra", "node_scope"]
)
def test_malformed_or_wrong_scope_graph_stores_nothing(terms_jobs, fault):
    url, _, _, _ = terms_jobs
    queue = TermsSemanticJobQueue(url)
    job = queue.claim("worker-a")
    graph = candidate(job).model_dump()
    if fault == "source":
        graph["sources"][0]["generation_id"] = str(uuid4())
    elif fault == "citation":
        graph["citations"][0]["text"] = "Synthetic forged quote"
    elif fault == "region":
        graph["nodes"][0]["region_ids"] = ["synthetic-unsupplied-region"]
    elif fault == "complete_manifest":
        graph["processing"]["consumed_region_ids"] = graph["processing"]["expected_region_ids"]
        graph["processing"]["unresolved_region_ids"] = []
    elif fault == "extra":
        graph["nodes"][0]["payload"]["secret"] = "synthetic-sensitive-extra"
    else:
        graph["nodes"][0]["citation_ids"] = [graph["citations"][-1]["citation_id"]]
    forged = TermsSemanticKnowledge.model_construct(**graph)
    with pytest.raises(TermsSemanticWorkConflict):
        queue.complete(job, "worker-a", forged)
    assert queue.get_job(job.id).state == "running"
    assert count_candidates(url) == 0


@pytest.mark.parametrize("fault", ["source", "privacy"])
def test_stale_source_or_household_privacy_cancels_pending_work(terms_jobs, fault):
    url, scope, edition, requests = terms_jobs
    queue = TermsSemanticJobQueue(url)
    job = queue.claim("worker-a")
    with psycopg.connect(_psycopg_url(url)) as connection:
        if fault == "source":
            connection.execute(
                "UPDATE terms_editions SET version=version+1 WHERE id=%s", (edition,)
            )
        else:
            connection.execute(
                "UPDATE family_members SET display_name='Family Member B',version=version+1 WHERE "
                "household_space_id=%s",
                (scope.household_space_id,),
            )
    assert not queue.heartbeat(job, "worker-a")
    with pytest.raises(TermsSemanticWorkConflict):
        queue.complete(job, "worker-a", candidate(job))
    assert queue.claim("worker-b") is None
    assert all(queue.get_job(request.job_id).state == "cancelled" for request in requests)
    assert count_candidates(url) == 0


def test_completed_history_survives_later_privacy_change(terms_jobs):
    url, scope, _, _ = terms_jobs
    queue = TermsSemanticJobQueue(url)
    job = queue.claim("worker-a")
    saved = queue.complete(job, "worker-a", candidate(job))
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE family_members SET version=version+1 WHERE household_space_id=%s",
            (scope.household_space_id,),
        )
    assert queue.claim("worker-b") is None
    assert queue.get_job(job.id).candidate_id == saved
    assert queue.get_job(job.id).state == "succeeded"


def test_pause_does_not_spend_retry_and_configuration_requires_explicit_resume(terms_jobs):
    url, _, _, _ = terms_jobs
    queue = TermsSemanticJobQueue(url)
    first, second = queue.claim("worker-a"), queue.claim("worker-b")
    queue.pause(first, "worker-a", "TERMS_STRUCTURING_DISABLED")
    queue.pause(second, "worker-b", "TERMS_PROVIDER_DOCUMENT_BUDGET")
    assert queue.get_job(first.id).attempts == queue.get_job(second.id).attempts == 0
    assert queue.claim("worker-a") is None
    resumed = queue.claim("worker-a", include_paused_configuration=True)
    assert resumed.id == first.id and resumed.attempts == 1
    queue.pause(resumed, "worker-a", "TERMS_PROVIDER_DAILY_BUDGET", daily=True)
    paused = queue.get_job(first.id)
    assert paused.attempts == 0 and paused.available_at.date() > datetime.now(UTC).date()
    assert queue.claim("worker-a", include_paused_configuration=True) is None
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE terms_semantic_jobs SET available_at=clock_timestamp() WHERE id=%s", (first.id,)
        )
    assert queue.claim("worker-a").id == first.id


def test_expired_third_attempt_fails_without_fourth_claim(terms_jobs):
    url, _, _, _ = terms_jobs
    queue = TermsSemanticJobQueue(url)
    job = queue.claim("worker-a")
    other = queue.claim("worker-b")
    queue.pause(other, "worker-b", "TERMS_PROVIDER_DOCUMENT_BUDGET")
    for expected in (2, 3):
        expire(url, job.id)
        job = queue.claim("worker-a")
        assert job.attempts == expected
    expire(url, job.id)
    assert queue.claim("worker-a") is None
    stored = queue.get_job(job.id)
    assert stored.state == "failed" and stored.error_code == "TERMS_LEASE_EXHAUSTED"


def test_load_sensitive_terms_requires_current_owner_and_includes_all_active_aliases(terms_jobs):
    url, scope, edition, _ = terms_jobs
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "INSERT INTO family_members(household_space_id,display_name,internal_alias) VALUES(%s,"
            "'Family Member B','Synthetic Relative B')",
            (scope.household_space_id,),
        )
    producer = TermsSemanticWorkRepository(url)
    plan = TermsSemanticRepository(url).source_plan(scope, edition)
    primary = next(r.region_id for r in plan.snapshot.layout.regions if r.label == "Article 1")
    producer.enqueue(scope, edition, (primary,))
    queue = TermsSemanticJobQueue(url)
    job = queue.claim("worker-a")
    terms = queue.load_sensitive_terms(job, "worker-a")
    assert "Family Member B" in terms and "Synthetic Relative B" in terms
    with pytest.raises(TermsSemanticWorkConflict):
        queue.load_sensitive_terms(job, "worker-b")
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute("DELETE FROM family_members WHERE internal_alias='Synthetic Relative B'")


def test_lease_expiry_during_completion_rolls_back_candidate_and_job_change(terms_jobs):
    url, _, _, _ = terms_jobs
    queue = TermsSemanticJobQueue(url)
    job = queue.claim("worker-a")
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute("""
            CREATE FUNCTION synthetic_expire_terms_completion() RETURNS TRIGGER
            LANGUAGE plpgsql AS $$ BEGIN
              UPDATE terms_semantic_jobs SET lease_expires_at=clock_timestamp()-interval '1 second'
                WHERE state='running';
              RETURN NEW;
            END $$;
            CREATE TRIGGER synthetic_expire_terms_completion
              AFTER INSERT ON terms_semantic_candidates
              FOR EACH ROW EXECUTE FUNCTION synthetic_expire_terms_completion();
        """)
    try:
        with pytest.raises(TermsSemanticWorkConflict):
            queue.complete(job, "worker-a", candidate(job))
        assert count_candidates(url) == 0
        assert queue.get_job(job.id).state == "running"
        assert queue.get_job(job.id).lease_expires_at == job.lease_expires_at
    finally:
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute(
                "DROP TRIGGER synthetic_expire_terms_completion ON terms_semantic_candidates"
            )
            connection.execute("DROP FUNCTION synthetic_expire_terms_completion()")


def test_explicit_retryable_failure_waits_and_permanent_failure_is_terminal(terms_jobs):
    url, _, _, _ = terms_jobs
    queue = TermsSemanticJobQueue(url)
    job, other = queue.claim("worker-a"), queue.claim("worker-b")
    queue.pause(other, "worker-b", "TERMS_PROVIDER_DOCUMENT_BUDGET")
    with pytest.raises(TermsSemanticWorkConflict):
        queue.fail(job, "worker-b", "TERMS_PROVIDER_RETRYABLE", retryable=True)
    queue.fail(job, "worker-a", "TERMS_PROVIDER_RETRYABLE", retryable=True)
    waiting = queue.get_job(job.id)
    assert waiting.state == "retryable_failed" and waiting.attempts == 1
    assert waiting.lease_token is None and waiting.available_at > datetime.now(UTC)
    assert queue.claim("worker-a") is None
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE terms_semantic_jobs SET available_at=clock_timestamp() WHERE id=%s", (job.id,)
        )
    retry = queue.claim("worker-a")
    assert retry.attempts == 2 and retry.lease_token != job.lease_token
    queue.fail(retry, "worker-a", "TERMS_PROVIDER_FAILED")
    assert queue.get_job(job.id).state == "failed"
    assert queue.claim("worker-a", include_paused_configuration=True) is None


def test_privacy_set_beyond_sixteen_terms_fails_before_any_claim(terms_jobs):
    url, scope, edition, _ = terms_jobs
    extra_ids = [uuid4() for _ in range(9)]
    try:
        with psycopg.connect(_psycopg_url(url)) as connection:
            for index, member_id in enumerate(extra_ids):
                connection.execute(
                    "INSERT INTO family_members(id,household_space_id,display_name,internal_alias) "
                    "VALUES(%s,%s,%s,%s)",
                    (
                        member_id,
                        scope.household_space_id,
                        f"Synthetic Extra Member {index}",
                        f"synthetic-extra-alias-{index}",
                    ),
                )
        plan = TermsSemanticRepository(url).source_plan(scope, edition)
        primary = next(r.region_id for r in plan.snapshot.layout.regions if r.label == "Article 1")
        request = TermsSemanticWorkRepository(url).enqueue(scope, edition, (primary,))
        queue = TermsSemanticJobQueue(url)
        assert queue.claim("worker-a") is None
        rejected = queue.get_job(request.job_id)
        assert rejected.state == "failed" and rejected.error_code == "TERMS_PRIVACY_UNAVAILABLE"
        assert rejected.attempts == 0 and rejected.lease_token is None
        assert count_candidates(url) == 0
    finally:
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute("DELETE FROM family_members WHERE id=ANY(%s)", (extra_ids,))
