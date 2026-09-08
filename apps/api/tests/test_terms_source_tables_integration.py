"""Retained PostgreSQL TABLE_ROW sources reach immutable semantic publications."""

from copy import deepcopy
from uuid import UUID

import psycopg
import pytest
from familycare_api.clauses.component_editions import ComponentTermsProjector
from familycare_api.common.scope import HouseholdScope
from familycare_api.insurance_documents.metadata_publication import DocumentMetadataProjector
from familycare_api.terms_knowledge.local_candidates import propose_local_candidates
from familycare_api.terms_knowledge.repository import TermsSemanticRepository
from familycare_worker.document_metadata_repository import DocumentMetadataRunner
from familycare_worker.document_structure import build_document_structure, plan_structure_chunks
from familycare_worker.document_structure_repository import DocumentStructureRepository
from familycare_worker.policy_jobs import PolicyStructuringJobQueue
from psycopg.rows import dict_row

from apps.api.tests.test_terms_semantic_core import amount
from apps.api.tests.test_terms_source_tables import DECLARATION, classification_result
from apps.api.tests.test_terms_source_verification import DAILY, FIXED, LIMIT
from workers.analyzer.tests.test_document_structure import _block, _extraction, _page, _table
from workers.analyzer.tests.test_document_structure_repository import (
    _prepare,
    _psycopg_url,
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)

pytestmark = pytest.mark.integration


def seed_table_source(database, *, code="class-a", excluded_days=2, content_sha256="a" * 64):
    url, job = database
    intro = "\n".join(
        [
            "보험약관",
            "보험사: Sample Assurance",
            "상품코드: SAMPLE-TABLE",
            "Article 1",
            DAILY,
            LIMIT,
            "Apply Footnote 1.",
            "Apply Appendix 1.",
            "Footnote 1",
            f"Exclude the first {excluded_days} admission days.",
        ]
    )
    source = _extraction(
        _page(
            1,
            [
                _block(intro),
                _block("Appendix 1\n" + DECLARATION, 1, y=40),
                _block("Article 2\n" + FIXED, 2, y=150),
            ],
            tables=[_table([["Code"], [code]], header_rows=[0])],
        )
    )
    source["document_version_id"] = str(job.document_version_id)
    source["content_sha256"] = content_sha256
    structure = build_document_structure(
        source, extraction_id=job.extraction_id, extraction_revision="synthetic-table-v1"
    )
    _prepare(
        DocumentStructureRepository(url),
        job,
        structure,
        plan_structure_chunks(
            structure, max_content_chars=4096, max_context_chars=4096, max_chunks=100
        ),
    )
    assert DocumentMetadataRunner(url).run_once("synthetic-table-worker")
    assert DocumentMetadataProjector(url).project_pending() == 1
    assert ComponentTermsProjector(url).project_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        edition = connection.execute(
            "SELECT id FROM terms_editions WHERE household_space_id=%s AND document_version_id=%s",
            (job.household_space_id, job.document_version_id),
        ).fetchone()["id"]
    repository = TermsSemanticRepository(url)
    scope = HouseholdScope(job.household_space_id)
    plan = repository.source_plan(scope, edition)
    appendix = next(r for r in plan.snapshot.layout.regions if r.kind == "appendix")
    assert [row.row_role for row in appendix.table_rows] == ["header", "data"]
    assert appendix.table_rows[1].header_node_ids == (appendix.table_rows[0].node_id,)
    return repository, scope, edition, plan


def publish_table_graphs(repository, scope, edition, plan):
    labels = {r.region_id: r.label for r in plan.snapshot.layout.regions}
    result = {}
    for graph in propose_local_candidates(plan.snapshot).graphs:
        root = next(n for n in graph["nodes"] if n["node_id"] == graph["roots"][0])
        label = labels[root["region_ids"][0]]
        publication = repository.publish_candidate(
            scope, edition, graph, expected_input_digest=plan.input_digest
        )
        result[label] = (publication, graph)
    return result


def test_original_table_publications_replay_and_new_document_preserves_prior_result(request):
    database = request.getfixturevalue("structure_database")
    repository, scope, edition, plan = seed_table_source(database)
    before = publish_table_graphs(repository, scope, edition, plan)
    old = before["Article 1"][0].compilation.roots[0]
    assert amount(old) == 300 and classification_result(old, "class-a") == "MATCH"
    page = repository.current_root_page(scope, edition)
    replayed = next(v.latest for v in page if v.root_node_id == old.root_node_id)
    assert amount(replayed.root) == 300
    url, _ = database
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        snapshot = connection.execute(
            "SELECT result_json FROM terms_semantic_publications WHERE id=%s",
            (before["Article 1"][0].publication_id,),
        ).fetchone()["result_json"]
    next_job = PolicyStructuringJobQueue(url).get_job(UUID("00000000-0000-4000-8000-000000000653"))
    assert next_job is not None
    # Initialize the second fixture document identity before any source generation exists.
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE document_versions SET content_sha256=%s WHERE id=%s",
            ("c" * 64, next_job.document_version_id),
        )
    later_repository, later_scope, later_edition, later_plan = seed_table_source(
        (url, next_job), code="class-b", excluded_days=1, content_sha256="c" * 64
    )
    after = publish_table_graphs(later_repository, later_scope, later_edition, later_plan)
    updated = after["Article 1"][0].compilation.roots[0]
    assert edition != later_edition
    assert plan.snapshot.source.generation_id != later_plan.snapshot.source.generation_id
    assert amount(updated) == 400
    assert classification_result(updated, "class-a") == "NO_MATCH"
    assert classification_result(updated, "class-b") == "MATCH"
    assert old.semantic_sha256 != updated.semantic_sha256
    unchanged = before["Article 2"][0].compilation.roots[0]
    successor = after["Article 2"][0].compilation.roots[0]
    assert unchanged.semantic_sha256 == successor.semantic_sha256
    assert unchanged.manifest_sha256 != successor.manifest_sha256
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute(
                "SELECT result_json FROM terms_semantic_publications WHERE id=%s",
                (before["Article 1"][0].publication_id,),
            ).fetchone()["result_json"]
            == snapshot
        )
    assert (
        amount(
            next(
                v.latest.root
                for v in repository.current_root_page(scope, edition)
                if v.root_node_id == old.root_node_id
            )
        )
        == 300
    )


def test_missing_original_table_header_proof_does_not_replace_last_good_root(request):
    repository, scope, edition, plan = seed_table_source(
        request.getfixturevalue("structure_database")
    )
    before = publish_table_graphs(repository, scope, edition, plan)
    successful, graph = before["Article 1"]
    broken = deepcopy(graph)
    classification = next(n for n in broken["nodes"] if n["payload"]["kind"] == "classification")
    classification["citation_ids"].pop(1)
    rejected = repository.publish_candidate(
        scope, edition, broken, expected_input_digest=plan.input_digest
    )
    assert rejected.compilation.roots[0].calculation is None
    view = next(
        v
        for v in repository.current_root_page(scope, edition)
        if v.root_node_id == successful.compilation.roots[0].root_node_id
    )
    assert view.latest.publication_id == rejected.publication_id
    assert view.latest.root.calculation is None
    assert view.last_usable.publication_id == successful.publication_id
    assert amount(view.last_usable.root) == 300
