"""Durable local source processing resumes without provider calls or lost roots."""

from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from familycare_api.terms_knowledge.projector import TermsSemanticProjector
from familycare_api.terms_knowledge.repository import TermsSemanticRepository
from psycopg.rows import dict_row

from apps.api.tests.test_terms_knowledge_repository import (
    seed_semantic_source,
    seeded_policy_database,  # noqa: F401
    semantic_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_terms_semantic_core import amount
from apps.api.tests.test_terms_source_verification import FIXED
from workers.analyzer.tests.test_document_structure_repository import _psycopg_url

pytestmark = pytest.mark.integration


@pytest.fixture()
def semantic_context(request):
    return request.getfixturevalue("semantic_database")


def counts(url):
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        return connection.execute(
            "SELECT (SELECT count(*) FROM terms_semantic_processing_runs) AS runs,"
            "(SELECT count(*) FROM terms_semantic_processing_outputs) AS outputs,"
            "(SELECT count(*) FROM terms_semantic_processing_completions) AS completions"
        ).fetchone()


def test_background_projection_accounts_for_original_and_does_not_repeat(semantic_context):
    url, scope, edition = semantic_context
    projector = TermsSemanticProjector(url)
    assert projector.project_pending() == 1
    assert projector.project_pending() == 0
    status = projector.current_status(scope, edition)
    # The synthetic insurer/product header is retained as unresolved source context.
    assert status is not None and status.outcome == "PARTIAL"
    assert set(status.expected_regions) == set(status.consumed_regions) | set(
        status.unresolved_regions
    )
    assert len(status.unresolved_regions) == 1
    roots = [
        root
        for item in TermsSemanticRepository(url).current(scope, edition)
        for root in item.compilation.roots
    ]
    assert sorted(amount(root) for root in roots if root.calculation is not None) == [50, 300]
    assert any(root.explanations and not root.executable for root in roots)
    assert counts(url) == {"runs": 1, "outputs": 3, "completions": 1}


def test_interrupted_projection_reuses_first_publication(semantic_context, monkeypatch):
    from familycare_api.terms_knowledge import projector as projector_module

    monkeypatch.setattr(projector_module, "RETRY_SECONDS", 0, raising=False)
    url, scope, edition = semantic_context
    original = TermsSemanticRepository.publish_candidate
    calls = []

    def interrupt(self, *args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("synthetic source contents must not be logged")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(TermsSemanticRepository, "publish_candidate", interrupt)
    projector = TermsSemanticProjector(url)
    assert projector.project_pending() == 0
    assert counts(url) == {"runs": 1, "outputs": 1, "completions": 0}
    first = TermsSemanticRepository(url).current(scope, edition)[0].publication_id
    assert projector.project_pending() == 1
    assert first in {
        item.publication_id for item in TermsSemanticRepository(url).current(scope, edition)
    }
    assert len(calls) == 4
    assert projector.project_pending() == 0


def test_concurrent_projection_has_one_complete_run(semantic_context):
    url, _, _ = semantic_context
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(lambda _: TermsSemanticProjector(url).project_pending(), range(2))
        )
    assert sum(results) == 1
    assert counts(url) == {"runs": 1, "outputs": 3, "completions": 1}


def test_current_source_revision_reprocesses_preserving_old_history(semantic_context):
    url, scope, edition = semantic_context
    projector = TermsSemanticProjector(url)
    assert projector.project_pending() == 1
    old = projector.current_status(scope, edition)
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute("UPDATE terms_editions SET version=version+1 WHERE id=%s", (edition,))
    assert projector.current_status(scope, edition) is None
    assert projector.project_pending() == 1
    assert projector.current_status(scope, edition).run_id != old.run_id
    assert counts(url) == {"runs": 2, "outputs": 6, "completions": 2}


def test_meaning_revision_reprocesses_without_rewriting_old_publication(
    semantic_context, monkeypatch
):
    from familycare_api.terms_knowledge import source_meaning, source_verification

    url, scope, edition = semantic_context
    projector = TermsSemanticProjector(url)
    assert projector.project_pending() == 1
    before = next(
        p
        for p in TermsSemanticRepository(url).current(scope, edition)
        if p.compilation.roots[0].calculation is not None and amount(p.compilation.roots[0]) == 300
    )
    monkeypatch.setattr(source_meaning, "MEANING_REVISION", "synthetic-meaning-v2")
    monkeypatch.setattr(source_verification, "MEANING_REVISION", "synthetic-meaning-v2")
    assert projector.current_status(scope, edition) is None
    assert projector.project_pending() == 1
    after = next(
        p
        for p in TermsSemanticRepository(url).current(scope, edition)
        if p.compilation.roots[0].root_node_id == before.compilation.roots[0].root_node_id
    )
    assert after.candidate_id == before.candidate_id
    assert after.publication_id != before.publication_id
    assert amount(after.compilation.roots[0]) == amount(before.compilation.roots[0]) == 300


def test_cancelled_source_never_resumes_or_reports_current_completion(semantic_context):
    url, scope, edition = semantic_context
    repository = TermsSemanticRepository(url)
    plan = repository.source_plan(scope, edition)
    projector = TermsSemanticProjector(url)
    assert projector.project_pending(stop_requested=lambda: True) == 0
    assert counts(url) == {"runs": 0, "outputs": 0, "completions": 0}
    assert projector.project_pending() == 1
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE document_structure_generations SET cancelled=true WHERE id=%s",
            (plan.snapshot.source.generation_id,),
        )
    assert projector.project_pending() == 0
    from familycare_api.clauses.errors import TermsEditionNotFound

    with pytest.raises(TermsEditionNotFound):
        projector.current_status(scope, edition)


def test_processing_history_cannot_be_rewritten(semantic_context):
    url, _, _ = semantic_context
    assert TermsSemanticProjector(url).project_pending() == 1
    with (
        psycopg.connect(_psycopg_url(url)) as connection,
        pytest.raises(psycopg.IntegrityError),
        connection.transaction(),
    ):
        connection.execute("UPDATE terms_semantic_processing_completions SET outcome='PARTIAL'")


def test_a_partial_run_cannot_be_claimed_complete_by_a_receipt(semantic_context):
    url, scope, edition = semantic_context
    projector = TermsSemanticProjector(url)
    assert projector.project_pending(stop_requested=lambda: counts(url)["outputs"] >= 1) == 0
    status = projector.current_status(scope, edition)
    assert status is not None and status.outcome == "IN_PROGRESS"
    assert status.published_graphs == 1
    with (
        psycopg.connect(_psycopg_url(url)) as connection,
        pytest.raises(psycopg.IntegrityError),
        connection.transaction(),
    ):
        connection.execute(
            "INSERT INTO terms_semantic_processing_completions(run_id,outcome) "
            "VALUES(%s,'COMPLETE')",
            (status.run_id,),
        )
    assert projector.project_pending() == 1


def test_receipt_cannot_substitute_a_different_graph(semantic_context):
    url, scope, edition = semantic_context
    projector = TermsSemanticProjector(url)
    assert projector.project_pending(stop_requested=lambda: counts(url)["outputs"] >= 1) == 0
    status = projector.current_status(scope, edition)
    publication = TermsSemanticRepository(url).current(scope, edition)[0]
    with (
        psycopg.connect(_psycopg_url(url)) as connection,
        pytest.raises(psycopg.IntegrityError),
        connection.transaction(),
    ):
        connection.execute(
            "INSERT INTO terms_semantic_processing_outputs(run_id,graph_ordinal,publication_id) "
            "VALUES(%s,1,%s)",
            (status.run_id, publication.publication_id),
        )


def test_unreplayable_output_receipt_is_never_reported_as_success(semantic_context):
    from dataclasses import asdict

    from familycare_api.terms_knowledge.local_candidates import propose_local_candidates
    from psycopg.types.json import Jsonb

    url, scope, edition = semantic_context
    projector = TermsSemanticProjector(url)
    assert projector.project_pending(stop_requested=lambda: counts(url)["runs"] == 1) == 0
    status = projector.current_status(scope, edition)
    assert status is not None and status.published_graphs == 0
    repository = TermsSemanticRepository(url)
    plan = repository.source_plan(scope, edition)
    graph = propose_local_candidates(plan.snapshot).graphs[0]
    valid = repository.publish_candidate(
        scope, edition, graph, expected_input_digest=plan.input_digest
    )
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        forged = connection.execute(
            "INSERT INTO terms_semantic_publications(candidate_id,household_space_id,"
            "terms_edition_id,verifier_revision,compiler_revision,proof_sha256,outcome,"
            "processing_complete,result_json) SELECT candidate_id,household_space_id,"
            "terms_edition_id,verifier_revision,compiler_revision,%s,'VERIFIED',true,%s "
            "FROM terms_semantic_publications WHERE id=%s RETURNING id",
            ("0" * 64, Jsonb(asdict(valid.compilation)), valid.publication_id),
        ).fetchone()["id"]
        connection.execute(
            "INSERT INTO terms_semantic_processing_outputs(run_id,graph_ordinal,publication_id) "
            "VALUES(%s,0,%s)",
            (status.run_id, forged),
        )
    assert projector.project_pending() == 1
    status = projector.current_status(scope, edition)
    assert status.outcome == "UNRESOLVED"
    assert status.reason_codes == ("SEMANTIC_PROCESSING_OUTPUT_UNVERIFIED",)
    assert projector.project_pending() == 0
    assert valid.publication_id in {p.publication_id for p in repository.current(scope, edition)}


def test_original_with_65_articles_processes_and_pages_every_rule(request):
    database = request.getfixturevalue("structure_database")
    text = "보험약관\n보험사: Sample Assurance\n상품코드: SAMPLE-MANY\n" + "\n".join(
        f"Article {number}\n{FIXED}" for number in range(1, 66)
    )
    url, scope, edition = seed_semantic_source(database, text)
    projector = TermsSemanticProjector(url)
    assert projector.project_pending() == 1
    status = projector.current_status(scope, edition)
    assert status.planned_graphs == status.published_graphs == 66
    assert status.outcome == "PARTIAL"  # Unresolved metadata header stays visible.
    assert len(status.expected_regions) == len(status.consumed_regions) + len(
        status.unresolved_regions
    )
    repository = TermsSemanticRepository(url)
    amounts = []
    after = None
    while page := repository.current_root_page(scope, edition, after=after, limit=32):
        amounts.extend(
            amount(item.latest.root)
            for item in page
            if item.latest and item.latest.root.calculation is not None
        )
        after = page[-1].root_node_id
    assert amounts == [50] * 65
    assert len(repository.current(scope, edition)) == 64
    assert projector.project_pending() == 0


def test_a_failed_source_backs_off_across_processes_and_does_not_starve_others(
    request, monkeypatch, caplog
):
    from uuid import UUID

    from familycare_worker.policy_jobs import PolicyStructuringJobQueue

    url, first_job = request.getfixturevalue("structure_database")
    second_job = PolicyStructuringJobQueue(url).get_job(
        UUID("00000000-0000-4000-8000-000000000653")
    )
    text = "보험약관\n보험사: Sample Assurance\n상품코드: SAMPLE-RETRY\nArticle 1\n" + FIXED
    _, scope, first = seed_semantic_source((url, first_job), text)
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE document_versions SET content_sha256=%s WHERE id=%s",
            ("c" * 64, second_job.document_version_id),
        )
    _, _, second = seed_semantic_source((url, second_job), text, content_sha256="c" * 64)
    failing = min(first, second)
    original = TermsSemanticProjector._project
    attempted = []

    def fail_one(self, current_scope, edition, stop_requested):
        attempted.append(edition)
        if edition == failing:
            raise RuntimeError("synthetic source contents must not be logged")
        return original(self, current_scope, edition, stop_requested)

    monkeypatch.setattr(TermsSemanticProjector, "_project", fail_one)
    assert TermsSemanticProjector(url).project_pending(limit=1) == 0
    assert TermsSemanticProjector(url).project_pending(limit=1) == 1
    assert TermsSemanticProjector(url).project_pending(limit=1) == 0
    assert attempted.count(failing) == 1
    assert "synthetic source contents" not in caplog.text
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute("UPDATE terms_editions SET version=version+1 WHERE id=%s", (failing,))
    monkeypatch.setattr(TermsSemanticProjector, "_project", original)
    assert TermsSemanticProjector(url).project_pending(limit=1) == 1
    assert TermsSemanticProjector(url).current_status(scope, failing) is not None
