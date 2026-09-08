"""Synthetic original document to immutable knowledge and local calculation."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
import pytest
from familycare_api.clauses.component_editions import ComponentTermsProjector
from familycare_api.clauses.errors import TermsEditionNotFound
from familycare_api.common.scope import HouseholdScope
from familycare_api.insurance_documents.metadata_publication import DocumentMetadataProjector
from familycare_api.terms_knowledge.repository import SemanticSourceChanged, TermsSemanticRepository
from familycare_api.terms_knowledge.source_meaning import observe_statement
from familycare_worker.document_metadata_repository import DocumentMetadataRunner
from psycopg.rows import dict_row

from apps.api.tests.test_terms_semantic_core import amount
from apps.api.tests.test_terms_source_verification import CLASS, DAILY, FIXED, FOOTNOTE, LIMIT
from workers.analyzer.tests.test_document_metadata_repository import _seed
from workers.analyzer.tests.test_document_structure_repository import (
    _psycopg_url,
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def semantic_database(request: pytest.FixtureRequest) -> Any:
    database = request.getfixturevalue("structure_database")
    text = "\n".join(
        [
            "보험약관",
            "보험사: Sample Assurance",
            "상품코드: SAMPLE-SEMANTIC",
            "Article 1",
            DAILY,
            LIMIT,
            "Apply Footnote 1.",
            "Apply Appendix 1.",
            "Footnote 1",
            FOOTNOTE,
            "Appendix 1",
            CLASS,
            "Article 2",
            FIXED,
        ]
    )
    return seed_semantic_source(database, text)


def seed_semantic_source(database, text, *, content_sha256="a" * 64):
    url, job, _ = _seed(database, text=text, content_sha256=content_sha256)
    assert DocumentMetadataRunner(url).run_once("synthetic-worker")
    assert DocumentMetadataProjector(url).project_pending() == 1
    assert ComponentTermsProjector(url).project_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        edition = connection.execute(
            "SELECT id FROM terms_editions WHERE household_space_id=%s AND document_version_id=%s",
            (job.household_space_id, job.document_version_id),
        ).fetchone()["id"]
    return url, HouseholdScope(job.household_space_id), edition


def candidate_from_plan(plan):
    citations = []
    nodes = []
    for region in plan.snapshot.layout.regions:
        pending_refs = []
        region_nodes = []
        for span in region.body_spans:
            payload = observe_statement(span.text)
            if payload is None and not span.text.startswith("Apply "):
                continue
            citation_id = str(
                uuid5(
                    NAMESPACE_URL,
                    str(plan.snapshot.source.generation_id) + span.node_id + str(span.start),
                )
            )
            citations.append({"citation_id": citation_id, "source_id": "terms", **asdict(span)})
            if payload is None:
                pending_refs.append(citation_id)
                continue
            key = {
                "calculation": "daily" if payload.get("mode") == "daily" else "unrelated",
                "footnote": "exclusion",
                "limit": "limit",
                "classification": "class",
            }[payload["kind"]]
            node = {
                "node_id": key,
                "source_id": "terms",
                "statement": span.text,
                "region_ids": [region.region_id],
                "citation_ids": [citation_id],
                "payload": payload,
            }
            nodes.append(node)
            region_nodes.append(node)
        if region_nodes:
            region_nodes[0]["citation_ids"].extend(pending_refs)
    return {
        "schema_version": "1",
        "schema_revision": "terms-semantic-v1",
        "prompt_revision": "synthetic-worker-v1",
        "model_revision": "synthetic-worker-v1",
        "sources": [plan.snapshot.source.model_dump(mode="json")],
        "citations": citations,
        "nodes": nodes,
        "edges": [
            {"from_node_id": "daily", "to_node_id": key, "relation": "DEPENDS_ON"}
            for key in ("exclusion", "limit", "class")
        ],
        "roots": ["daily", "unrelated"],
        "processing": {
            "expected_region_ids": list(plan.snapshot.layout.expected_region_ids),
            "consumed_region_ids": list(plan.snapshot.layout.expected_region_ids),
            "unresolved_region_ids": [],
        },
    }


def test_original_source_publication_replays_and_computes_without_provider(semantic_database):
    url, scope, edition = semantic_database
    repository = TermsSemanticRepository(url)
    plan = repository.source_plan(scope, edition)
    graph = candidate_from_plan(plan)
    assert len(graph["nodes"]) == 5
    publication = repository.publish_candidate(
        scope, edition, graph, expected_input_digest=plan.input_digest
    )
    assert amount(publication.compilation.roots[0]) == 300
    assert repository.current(scope, edition)[0] == publication
    assert (
        repository.publish_candidate(scope, edition, graph, expected_input_digest=plan.input_digest)
        == publication
    )


def test_partial_failure_preserves_last_good_and_unrelated_rule(semantic_database):
    url, scope, edition = semantic_database
    repository = TermsSemanticRepository(url)
    plan = repository.source_plan(scope, edition)
    graph = candidate_from_plan(plan)
    first = repository.publish_candidate(
        scope, edition, graph, expected_input_digest=plan.input_digest
    )
    malformed = deepcopy(graph)
    next(n for n in malformed["nodes"] if n["node_id"] == "exclusion")["payload"]["days"] = 1
    later = repository.publish_candidate(
        scope, edition, malformed, expected_input_digest=plan.input_digest
    )
    assert later.compilation.roots[0].calculation is None
    assert later.compilation.roots[1].calculation is not None
    history = repository.current(scope, edition)
    assert [r.publication_id for r in history] == [later.publication_id, first.publication_id]
    assert amount(history[1].compilation.roots[0]) == 300


def test_current_root_page_retains_partial_explanation_and_last_good(semantic_database):
    url, scope, edition = semantic_database
    repository = TermsSemanticRepository(url)
    plan = repository.source_plan(scope, edition)
    graph = candidate_from_plan(plan)
    first = repository.publish_candidate(
        scope, edition, graph, expected_input_digest=plan.input_digest
    )
    broken = deepcopy(graph)
    next(n for n in broken["nodes"] if n["node_id"] == "exclusion")["payload"]["days"] = 1
    last = repository.publish_candidate(
        scope, edition, broken, expected_input_digest=plan.input_digest
    )
    page = repository.current_root_page(scope, edition, limit=1)
    assert len(page) == 1 and page[0].root_node_id == "daily"
    assert page[0].latest.publication_id == last.publication_id
    assert page[0].latest.root.calculation is None
    assert page[0].latest.root.explanations
    assert page[0].last_usable.publication_id == first.publication_id
    assert amount(page[0].last_usable.root) == 300
    next_page = repository.current_root_page(scope, edition, after=page[-1].root_node_id, limit=1)
    assert [item.root_node_id for item in next_page] == ["unrelated"]
    assert next_page[0].latest == next_page[0].last_usable
    assert repository.current_root_page(scope, edition, after="unrelated") == ()


def test_root_page_is_bound_to_callers_repeatable_read_snapshot(semantic_database):
    from familycare_api.terms_knowledge.repository import read_semantic_root_page

    url, scope, edition = semantic_database
    repository = TermsSemanticRepository(url)
    plan = repository.source_plan(scope, edition)
    graph = candidate_from_plan(plan)
    first = repository.publish_candidate(
        scope, edition, graph, expected_input_digest=plan.input_digest
    )
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        assert (
            read_semantic_root_page(connection, scope, edition)[0].latest.publication_id
            == first.publication_id
        )
        broken = deepcopy(graph)
        next(n for n in broken["nodes"] if n["node_id"] == "exclusion")["payload"]["days"] = 1
        last = repository.publish_candidate(
            scope, edition, broken, expected_input_digest=plan.input_digest
        )
        assert (
            read_semantic_root_page(connection, scope, edition)[0].latest.publication_id
            == first.publication_id
        )
    assert (
        repository.current_root_page(scope, edition)[0].latest.publication_id == last.publication_id
    )


def test_root_page_reports_aggregate_replay_budget_without_losing_its_cursor(
    semantic_database, monkeypatch
):
    from familycare_api.terms_knowledge import repository as repository_module

    url, scope, edition = semantic_database
    repository = TermsSemanticRepository(url)
    plan = repository.source_plan(scope, edition)
    repository.publish_candidate(
        scope, edition, candidate_from_plan(plan), expected_input_digest=plan.input_digest
    )
    monkeypatch.setattr(repository_module, "MAX_ROOT_PAGE_REPLAY_BYTES", 1, raising=False)
    page = repository.current_root_page(scope, edition, limit=1)
    assert page[0].latest is None and page[0].last_usable is None
    assert page[0].reason_codes == ("SEMANTIC_READ_BUDGET_EXCEEDED",)
    monkeypatch.setattr(repository_module, "MAX_ROOT_PAGE_REPLAY_BYTES", 32 * 1024 * 1024)
    assert (
        repository.current_root_page(scope, edition, after=page[0].root_node_id)[0].latest
        is not None
    )


def test_parallel_identical_candidates_publish_once(semantic_database):
    url, scope, edition = semantic_database
    repository = TermsSemanticRepository(url)
    plan = repository.source_plan(scope, edition)
    graph = candidate_from_plan(plan)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: repository.publish_candidate(
                    scope, edition, graph, expected_input_digest=plan.input_digest
                ),
                range(2),
            )
        )
    assert results[0].publication_id == results[1].publication_id
    assert len(repository.current(scope, edition)) == 1


def test_other_household_and_changed_inputs_do_not_publish(semantic_database):
    url, scope, edition = semantic_database
    repository = TermsSemanticRepository(url)
    plan = repository.source_plan(scope, edition)
    graph = candidate_from_plan(plan)
    with pytest.raises(TermsEditionNotFound):
        repository.publish_candidate(
            HouseholdScope(UUID(int=99, version=4)),
            edition,
            graph,
            expected_input_digest=plan.input_digest,
        )
    with pytest.raises(SemanticSourceChanged):
        repository.publish_candidate(scope, edition, graph, expected_input_digest="c" * 64)
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute("UPDATE terms_editions SET version=version+1 WHERE id=%s", (edition,))
    with pytest.raises(SemanticSourceChanged):
        repository.publish_candidate(scope, edition, graph, expected_input_digest=plan.input_digest)
    assert repository.current(scope, edition) == ()


@pytest.mark.parametrize(
    "table",
    [
        "terms_semantic_candidates",
        "terms_semantic_publications",
        "terms_semantic_root_publications",
    ],
)
def test_semantic_history_is_immutable(semantic_database, table):
    from psycopg import sql

    url, scope, edition = semantic_database
    repository = TermsSemanticRepository(url)
    plan = repository.source_plan(scope, edition)
    repository.publish_candidate(
        scope, edition, candidate_from_plan(plan), expected_input_digest=plan.input_digest
    )
    with (
        psycopg.connect(_psycopg_url(url)) as connection,
        pytest.raises(psycopg.IntegrityError),
        connection.transaction(),
    ):
        connection.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(table)))


def test_cancelled_original_generation_cannot_publish_or_supply_current_rules(semantic_database):
    url, scope, edition = semantic_database
    repository = TermsSemanticRepository(url)
    plan = repository.source_plan(scope, edition)
    graph = candidate_from_plan(plan)
    repository.publish_candidate(scope, edition, graph, expected_input_digest=plan.input_digest)
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE document_structure_generations SET cancelled=true WHERE id=%s",
            (UUID(plan.snapshot.source.generation_id),),
        )
    with pytest.raises(TermsEditionNotFound):
        repository.current(scope, edition)
    with pytest.raises(TermsEditionNotFound):
        repository.publish_candidate(scope, edition, graph, expected_input_digest=plan.input_digest)


def test_last_usable_root_survives_more_than_the_recent_history_window(semantic_database):
    url, scope, edition = semantic_database
    repository = TermsSemanticRepository(url)
    plan = repository.source_plan(scope, edition)
    graph = candidate_from_plan(plan)
    first = repository.publish_candidate(
        scope, edition, graph, expected_input_digest=plan.input_digest
    )
    invalid = deepcopy(graph)
    next(n for n in invalid["nodes"] if n["node_id"] == "exclusion")["payload"]["days"] = 1
    for index in range(65):
        invalid["model_revision"] = f"synthetic-failed-attempt-{index}"
        repository.publish_candidate(
            scope, edition, invalid, expected_input_digest=plan.input_digest
        )
    retained = repository.last_usable_roots(scope, edition, ("daily", "unrelated"))
    assert len(retained) == 2
    assert retained[0].publication_id == first.publication_id
    assert amount(retained[0].root) == 300


@pytest.mark.parametrize("malformed", [False, True])
def test_audit_rows_cannot_override_replayed_outcomes_or_block_good_history(
    semantic_database, malformed
):
    from familycare_api.terms_knowledge.source_verification import verify_and_compile
    from psycopg.types.json import Jsonb

    url, scope, edition = semantic_database
    repository = TermsSemanticRepository(url)
    plan = repository.source_plan(scope, edition)
    graph = candidate_from_plan(plan)
    first = repository.publish_candidate(
        scope, edition, graph, expected_input_digest=plan.input_digest
    )
    graph["model_revision"] = "synthetic-forged-audit"
    next(n for n in graph["nodes"] if n["node_id"] == "exclusion")["payload"]["days"] = 1
    verified = verify_and_compile(graph, sources={"terms": plan.snapshot})
    stored_graph = {} if malformed else graph
    stored_result = asdict(verified.compilation)
    if malformed:
        stored_result["roots"][0]["root_node_id"] = "aaa-unreplayable"
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
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
                Jsonb(stored_graph),
                Jsonb(stored_graph),
            ),
        ).fetchone()["id"]
        audit_publication = connection.execute(
            "INSERT INTO terms_semantic_publications(candidate_id,household_space_id,"
            "terms_edition_id,"
            "verifier_revision,compiler_revision,proof_sha256,outcome,"
            "processing_complete,result_json) "
            "VALUES(%s,%s,%s,%s,%s,%s,'VERIFIED',true,%s) RETURNING id",
            (
                candidate,
                scope.household_space_id,
                edition,
                verified.verifier_revision,
                verified.compilation.compiler_revision,
                verified.proof_sha256,
                Jsonb(stored_result),
            ),
        ).fetchone()["id"]
        if malformed:
            root = stored_result["roots"][0]
            connection.execute(
                "INSERT INTO terms_semantic_root_publications(publication_id,root_node_id,"
                "semantic_sha256,manifest_sha256,executable,root_json) VALUES(%s,%s,%s,%s,%s,%s)",
                (
                    audit_publication,
                    root["root_node_id"],
                    root["semantic_sha256"],
                    root["manifest_sha256"],
                    bool(root["rules"] or root["calculation"]),
                    Jsonb(root),
                ),
            )
    current = repository.current(scope, edition)
    if malformed:
        assert current == (first,)
        page = repository.current_root_page(scope, edition, limit=1)
        assert len(page) == 1 and page[0].root_node_id == "aaa-unreplayable"
        assert page[0].latest is None and page[0].last_usable is None
        assert repository.current_root_page(scope, edition, after=page[-1].root_node_id)
    else:
        assert current[0].outcome == "PARTIAL"
        assert current[0].compilation.roots[0].calculation is None
        assert current[-1] == first
