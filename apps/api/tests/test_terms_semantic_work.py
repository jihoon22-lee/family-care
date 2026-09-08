"""Source-scoped semantic work plans are durable and never call a provider."""

import psycopg
import pytest
from familycare_api.terms_knowledge.work_repository import TermsSemanticWorkRepository
from psycopg.rows import dict_row

from apps.api.tests.test_terms_knowledge_repository import (
    seeded_policy_database,  # noqa: F401
    semantic_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from workers.analyzer.tests.test_document_structure_repository import _psycopg_url

pytestmark = pytest.mark.integration


@pytest.fixture()
def semantic_context(request):
    return request.getfixturevalue("semantic_database")


def test_explicit_work_envelope_has_whole_required_regions_and_is_idempotent(semantic_context):
    url, scope, edition = semantic_context
    repository = TermsSemanticWorkRepository(url)
    from familycare_api.terms_knowledge.repository import TermsSemanticRepository

    plan = TermsSemanticRepository(url).source_plan(scope, edition)
    primary = next(r.region_id for r in plan.snapshot.layout.regions if r.label == "Article 1")
    job = repository.enqueue(scope, edition, (primary,))
    assert repository.enqueue(scope, edition, (primary,)).job_id == job.job_id
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        row = connection.execute(
            "SELECT * FROM terms_semantic_jobs WHERE id=%s", (job.job_id,)
        ).fetchone()
    assert row["state"] == "queued"
    assert row["attempts"] == 0
    assert row["envelope_json"]["primary_region_ids"] == [primary]
    assert {r["label"] for r in row["envelope_json"]["regions"]} == {
        "Article 1",
        "Appendix 1",
        "Footnote 1",
    }
    assert row["privacy_digest"]
    assert row["input_digest"] == plan.input_digest


def test_foreign_household_or_unknown_primary_cannot_enqueue(semantic_context):
    from uuid import UUID

    from familycare_api.clauses.errors import TermsEditionNotFound
    from familycare_api.common.scope import HouseholdScope
    from familycare_api.terms_knowledge.work_repository import SemanticWorkUnsupported

    url, scope, edition = semantic_context
    repository = TermsSemanticWorkRepository(url)
    with pytest.raises(TermsEditionNotFound):
        repository.enqueue(HouseholdScope(UUID(int=99, version=4)), edition, ("missing",))
    with pytest.raises(SemanticWorkUnsupported):
        repository.enqueue(scope, edition, ("missing",))
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute("SELECT count(*) FROM terms_semantic_jobs").fetchone()[0] == 0


def test_automatic_work_accounts_for_unsupported_context_without_enqueuing_known_rules(
    semantic_context,
):
    from familycare_api.terms_knowledge.projector import TermsSemanticProjector

    url, _, _ = semantic_context
    repository = TermsSemanticWorkRepository(url)
    assert repository.prepare_pending() == 0
    assert TermsSemanticProjector(url).project_pending() == 1
    assert repository.prepare_pending() == 1
    assert repository.prepare_pending() == 0
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute("SELECT count(*) FROM terms_semantic_jobs").fetchone()[0] == 0
        assert (
            connection.execute("SELECT outcome FROM terms_semantic_work_attempts").fetchone()[0]
            == "UNSUPPORTED"
        )


def test_privacy_revision_creates_new_request_without_rewriting_original(semantic_context):
    from familycare_api.terms_knowledge.repository import TermsSemanticRepository

    url, scope, edition = semantic_context
    repository = TermsSemanticWorkRepository(url)
    plan = TermsSemanticRepository(url).source_plan(scope, edition)
    primary = next(r.region_id for r in plan.snapshot.layout.regions if r.label == "Article 1")
    first = repository.enqueue(scope, edition, (primary,))
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE family_members SET version=version+1 WHERE household_space_id=%s",
            (scope.household_space_id,),
        )
    second = repository.enqueue(scope, edition, (primary,))
    assert second.job_id != first.job_id
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute("SELECT count(*) FROM terms_semantic_jobs").fetchone()[0] == 2


@pytest.mark.parametrize("mutation", ["rename", "restore", "create"])
def test_privacy_lock_serializes_the_whole_member_set(semantic_context, mutation):
    from uuid import UUID

    from psycopg.errors import LockNotAvailable

    url, scope, edition = semantic_context
    other_id, new_id = UUID(int=770, version=4), UUID(int=771, version=4)
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "INSERT INTO family_members("
            "id,household_space_id,display_name,internal_alias,deleted_at) "
            "VALUES(%s,%s,'Family Member B','synthetic-other',"
            "CASE WHEN %s THEN clock_timestamp() ELSE NULL END)",
            (other_id, scope.household_space_id, mutation == "restore"),
        )
    try:
        with psycopg.connect(_psycopg_url(url)) as holder:
            assert holder.execute(
                "SELECT lock_terms_semantic_work_source(%s,%s)", (edition, scope.household_space_id)
            ).fetchone()[0]
            with psycopg.connect(_psycopg_url(url)) as contender:
                contender.execute("SET LOCAL lock_timeout='100ms'")
                with pytest.raises(LockNotAvailable), contender.transaction():
                    if mutation == "rename":
                        contender.execute(
                            "UPDATE family_members SET display_name='Family Member C' WHERE id=%s",
                            (other_id,),
                        )
                    elif mutation == "restore":
                        contender.execute(
                            "UPDATE family_members SET deleted_at=NULL WHERE id=%s", (other_id,)
                        )
                    else:
                        contender.execute(
                            "INSERT INTO family_members("
                            "id,household_space_id,display_name,internal_alias) "
                            "VALUES(%s,%s,'Family Member D','synthetic-new')",
                            (new_id, scope.household_space_id),
                        )
    finally:
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute("DELETE FROM family_members WHERE id=ANY(%s)", ([other_id, new_id],))


def retained_candidate(context, *, outside=False, forged_citation=False):
    from familycare_api.terms_knowledge.local_candidates import propose_local_candidates
    from familycare_api.terms_knowledge.repository import TermsSemanticRepository
    from psycopg.types.json import Jsonb

    url, scope, edition = context
    repository = TermsSemanticWorkRepository(url)
    plan = TermsSemanticRepository(url).source_plan(scope, edition)
    primary = next(r.region_id for r in plan.snapshot.layout.regions if r.label == "Article 1")
    job = repository.enqueue(scope, edition, (primary,))
    graph = propose_local_candidates(plan.snapshot).graphs[1 if outside else 0]
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        original = connection.execute(
            "SELECT * FROM terms_semantic_jobs WHERE id=%s", (job.job_id,)
        ).fetchone()
        supplied = {r["region_id"] for r in original["envelope_json"]["regions"]}
        graph["processing"]["consumed_region_ids"] = sorted(supplied)
        graph["processing"]["unresolved_region_ids"] = [
            key for key in graph["processing"]["expected_region_ids"] if key not in supplied
        ]
        if forged_citation:
            graph["citations"][0]["text"] = "X" * len(graph["citations"][0]["text"])
        candidate = connection.execute(
            "INSERT INTO terms_semantic_candidates(household_space_id,terms_edition_id,"
            "input_context,input_digest,graph_json,graph_sha256) "
            "VALUES(%s,%s,%s,%s,%s,encode(sha256(convert_to(%s::jsonb::text,'UTF8')),'hex')) "
            "RETURNING id",
            (
                scope.household_space_id,
                edition,
                Jsonb(plan.input_context),
                plan.input_digest,
                Jsonb(graph),
                Jsonb(graph),
            ),
        ).fetchone()["id"]
        # This API-only fixture emulates a retained Worker result, not a lease execution.
        connection.execute(
            "UPDATE terms_semantic_jobs SET state='succeeded',candidate_id=%s WHERE id=%s",
            (candidate, job.job_id),
        )
    return job.job_id


def test_retained_worker_candidate_is_replayed_without_provider_or_http(semantic_context):
    from familycare_api.terms_knowledge.repository import TermsSemanticRepository

    from apps.api.tests.test_terms_semantic_core import amount

    url, scope, edition = semantic_context
    retained_candidate(semantic_context)
    repository = TermsSemanticWorkRepository(url)
    assert repository.project_pending() == 1
    assert repository.project_pending() == 0
    page = TermsSemanticRepository(url).current_root_page(scope, edition)
    assert len(page) == 1 and amount(page[0].latest.root) == 300
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert (
            connection.execute("SELECT count(*) FROM policy_provider_requests").fetchone()[0] == 0
        )
        assert (
            connection.execute("SELECT outcome FROM terms_semantic_job_publications").fetchone()[0]
            == "PUBLISHED"
        )


def test_inbox_and_direct_publication_use_compatible_lock_order(semantic_context):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from familycare_api.terms_knowledge.local_candidates import propose_local_candidates
    from familycare_api.terms_knowledge.repository import TermsSemanticRepository

    url, scope, edition = semantic_context
    retained_candidate(semantic_context)
    direct = TermsSemanticRepository(url)
    plan = direct.source_plan(scope, edition)
    payload = propose_local_candidates(plan.snapshot).graphs[0]
    barrier = Barrier(4)

    def publish(index):
        barrier.wait(timeout=10)
        if index % 2:
            return TermsSemanticWorkRepository(url).project_pending()
        return direct.publish_candidate(
            scope, edition, payload, expected_input_digest=plan.input_digest
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(publish, index) for index in range(4)]
        results = [future.result(timeout=20) for future in futures]
    assert sum(result for result in results if isinstance(result, int)) == 1
    assert len(direct.current_root_page(scope, edition)) == 1


@pytest.mark.parametrize(
    "fault", ["outside", "forged_citation", "source_changed", "privacy_changed"]
)
def test_inbox_rejects_wrong_work_scope_and_stale_candidates(semantic_context, fault):
    from familycare_api.terms_knowledge.repository import TermsSemanticRepository

    url, scope, edition = semantic_context
    retained_candidate(
        semantic_context, outside=fault == "outside", forged_citation=fault == "forged_citation"
    )
    with psycopg.connect(_psycopg_url(url)) as connection:
        if fault == "source_changed":
            connection.execute(
                "UPDATE terms_editions SET version=version+1 WHERE id=%s", (edition,)
            )
        elif fault == "privacy_changed":
            connection.execute(
                "UPDATE family_members SET version=version+1 WHERE household_space_id=%s",
                (scope.household_space_id,),
            )
    assert TermsSemanticWorkRepository(url).project_pending() == 1
    assert TermsSemanticRepository(url).current(scope, edition) == ()
    with psycopg.connect(_psycopg_url(url)) as connection:
        outcome = connection.execute(
            "SELECT outcome FROM terms_semantic_job_publications"
        ).fetchone()[0]
    assert outcome == ("STALE" if fault.endswith("changed") else "REJECTED")


def test_new_meaning_proof_reuses_retained_candidate_without_a_worker_request(
    semantic_context, monkeypatch
):
    from familycare_api.terms_knowledge import source_meaning, source_verification
    from familycare_api.terms_knowledge.repository import TermsSemanticRepository

    url, scope, edition = semantic_context
    retained_candidate(semantic_context)
    repository = TermsSemanticWorkRepository(url)
    assert repository.project_pending() == 1
    before = TermsSemanticRepository(url).current(scope, edition)[0]
    monkeypatch.setattr(source_meaning, "MEANING_REVISION", "synthetic-work-meaning-v3")
    monkeypatch.setattr(source_verification, "MEANING_REVISION", "synthetic-work-meaning-v3")
    assert repository.project_pending() == 1
    after = TermsSemanticRepository(url).current(scope, edition)[0]
    assert before.candidate_id == after.candidate_id
    assert before.publication_id != after.publication_id
