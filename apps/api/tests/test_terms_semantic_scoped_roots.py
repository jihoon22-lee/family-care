"""Original address selection precedes root pagination and never supplies authority."""

from copy import deepcopy
from dataclasses import asdict, replace
from uuid import UUID

import psycopg
import pytest
from familycare_api.clauses.source_regions import ClauseSourceSpan
from familycare_api.common.scope import HouseholdScope
from familycare_api.terms_knowledge.core import COMPILER_REVISION
from familycare_api.terms_knowledge.repository import (
    TermsSemanticRepository,
    read_semantic_root_page,
)
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests.test_terms_knowledge_repository import (
    candidate_from_plan,
)
from apps.api.tests.test_terms_knowledge_repository import (
    semantic_database as semantic_database,
)
from apps.api.tests.test_terms_semantic_core import amount
from workers.analyzer.tests.test_document_structure_repository import (
    _psycopg_url,
)
from workers.analyzer.tests.test_document_structure_repository import (
    seeded_policy_database as seeded_policy_database,
)
from workers.analyzer.tests.test_document_structure_repository import (
    structure_database as structure_database,
)


def _span():
    return ClauseSourceSpan("synthetic-node", 1, 0, 10, "synthetic!", "native", (0, 0, 20, 10))


@pytest.mark.parametrize(
    "spans",
    [
        [],
        (object(),),
        (_span(),) * 4097,
        (replace(_span(), node_id="bad node"),),
        (replace(_span(), page_number=True),),
        (replace(_span(), start=-1),),
        (replace(_span(), end=0),),
        (replace(_span(), source_layer="invented"),),
        (replace(_span(), bbox=(0, 0, float("nan"), 10)),),
    ],
)
def test_invalid_source_scope_is_rejected_before_database_access(spans):
    with pytest.raises(ValueError, match="^TERMS_SEMANTIC_ROOT_REQUEST_INVALID$"):
        read_semantic_root_page(None, HouseholdScope(UUID(int=1)), UUID(int=2), source_spans=spans)


def test_explicit_empty_scope_cannot_expand_to_the_whole_edition():
    assert (
        read_semantic_root_page(None, HouseholdScope(UUID(int=1)), UUID(int=2), source_spans=())
        == ()
    )


def _published(database):
    url, scope, edition = database
    repository = TermsSemanticRepository(url)
    plan = repository.source_plan(scope, edition)
    graph = candidate_from_plan(plan)
    publication = repository.publish_candidate(
        scope, edition, graph, expected_input_digest=plan.input_digest
    )
    body = next(r.body_spans for r in plan.snapshot.layout.regions if r.label == "Article 1")
    return url, scope, edition, repository, plan, publication, body


@pytest.mark.integration
def test_scope_selects_original_primary_addresses_without_changing_edition_api(semantic_database):
    url, scope, edition, repository, _, publication, body = _published(semantic_database)
    assert [view.root_node_id for view in repository.current_root_page(scope, edition)] == [
        "daily",
        "unrelated",
    ]
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        page = read_semantic_root_page(connection, scope, edition, source_spans=body, limit=1)
        assert [view.root_node_id for view in page] == ["daily"]
        assert page[0].last_usable.publication_id == publication.publication_id
        assert amount(page[0].last_usable.root) == 300
        assert (
            read_semantic_root_page(
                connection, scope, edition, source_spans=body, after="daily", limit=1
            )
            == ()
        )
        # Caller text is not an index key or replacement source proof.
        changed_text = tuple(replace(span, text="Synthetic caller label") for span in body)
        assert (
            read_semantic_root_page(connection, scope, edition, source_spans=changed_text) == page
        )
    assert repository.current_root_page(scope, edition, source_spans=body) == page


@pytest.mark.integration
@pytest.mark.parametrize(
    "fault", ["other-node", "other-page", "layer", "bbox", "partial-span", "references-only"]
)
def test_foreign_or_partial_original_address_cannot_select_a_root(semantic_database, fault):
    url, scope, edition, _, _, _, body = _published(semantic_database)
    daily = body[0]
    if fault == "other-node":
        body = tuple(replace(s, node_id="different-node") for s in body)
    elif fault == "other-page":
        body = tuple(replace(s, page_number=s.page_number + 1) for s in body)
    elif fault == "layer":
        body = tuple(replace(s, source_layer="ocr") for s in body)
    elif fault == "bbox":
        body = tuple(
            replace(s, bbox=(s.bbox[0], s.bbox[1], s.bbox[2] - 1, s.bbox[3])) for s in body
        )
    elif fault == "partial-span":
        body = (replace(daily, start=daily.start + 1), *body[1:])
    else:
        body = tuple(s for s in body if s.text.startswith("Apply "))
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert read_semantic_root_page(connection, scope, edition, source_spans=body) == ()


def _audit_roots(connection, scope, edition, plan, roots, *, marker):
    """Create valid immutable audit rows whose index contents are not replay authority."""
    graph = {"synthetic_audit": marker}
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
    publication = connection.execute(
        "INSERT INTO terms_semantic_publications(candidate_id,household_space_id,terms_edition_id,"
        "verifier_revision,compiler_revision,proof_sha256,outcome,processing_complete,result_json) "
        "VALUES(%s,%s,%s,'terms-semantic-source-v1',%s,"
        "%s,'VERIFIED',true,%s) RETURNING id",
        (
            candidate,
            scope.household_space_id,
            edition,
            COMPILER_REVISION,
            "a" * 64,
            Jsonb({"roots": roots}),
        ),
    ).fetchone()["id"]
    with connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO terms_semantic_root_publications(publication_id,root_node_id,"
            "semantic_sha256,manifest_sha256,executable,root_json) VALUES(%s,%s,%s,%s,%s,%s)",
            [
                (
                    publication,
                    r["root_node_id"],
                    r["semantic_sha256"],
                    r["manifest_sha256"],
                    bool(r["rules"] or r["calculation"]),
                    Jsonb(r),
                )
                for r in roots
            ],
        )


def _renamed_root(root, identifier):
    result = deepcopy(root)
    previous = result["root_node_id"]
    result["root_node_id"] = result["manifest"]["root_node_id"] = identifier
    for node in result["manifest"]["nodes"]:
        if node["node_id"] == previous:
            node["node_id"] = identifier
    return result


@pytest.mark.integration
def test_scoped_key_selection_reaches_original_beyond_1024_foreign_audit_keys(semantic_database):
    url, scope, edition, _, plan, publication, body = _published(semantic_database)
    unrelated = asdict(publication.compilation.roots[1])
    roots = [_renamed_root(unrelated, f"aaa-foreign-{index:04d}") for index in range(1024)]
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        _audit_roots(connection, scope, edition, plan, roots, marker="foreign-keys")
        count = connection.execute(
            "SELECT count(DISTINCT root_node_id) AS count FROM terms_semantic_root_publications "
            "WHERE root_node_id<'daily'"
        ).fetchone()["count"]
        assert count == 1024
        page = read_semantic_root_page(connection, scope, edition, source_spans=body, limit=1)
        assert len(page) == 1 and page[0].root_node_id == "daily"
        assert amount(page[0].last_usable.root) == 300


@pytest.mark.integration
def test_missing_primary_citations_are_not_selected_and_forged_indexes_are_not_authority(
    semantic_database,
):
    url, scope, edition, _, plan, publication, body = _published(semantic_database)
    original = asdict(publication.compilation.roots[0])
    missing = _renamed_root(original, "aaa-missing")
    next(n for n in missing["manifest"]["nodes"] if n["node_id"] == "aaa-missing")[
        "citation_ids"
    ] = []
    forged = _renamed_root(original, "bbb-forged")
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        _audit_roots(connection, scope, edition, plan, [missing, forged], marker="forged-address")
        page = read_semantic_root_page(connection, scope, edition, source_spans=body)
        assert [view.root_node_id for view in page] == ["bbb-forged", "daily"]
        assert page[0].latest is None and page[0].last_usable is None
        assert page[0].reason_codes == ("SEMANTIC_PUBLICATION_UNREPLAYABLE",)
        assert amount(page[1].last_usable.root) == 300


@pytest.mark.integration
def test_reused_root_id_selects_same_original_attempt_before_recent_attempt_limit(
    semantic_database,
):
    _, scope, edition, repository, plan, original, body = _published(semantic_database)
    graph = candidate_from_plan(plan)
    identifiers = {"daily": "other-daily", "unrelated": "daily"}
    for node in graph["nodes"]:
        node["node_id"] = identifiers.get(node["node_id"], node["node_id"])
    for edge in graph["edges"]:
        for key in ("from_node_id", "to_node_id"):
            edge[key] = identifiers.get(edge[key], edge[key])
    graph["roots"] = [identifiers.get(key, key) for key in graph["roots"]]
    for attempt in range(9):
        graph["model_revision"] = f"synthetic-reused-root-{attempt}"
        latest = repository.publish_candidate(
            scope, edition, graph, expected_input_digest=plan.input_digest
        )
    assert (
        next(
            view
            for view in repository.current_root_page(scope, edition)
            if view.root_node_id == "daily"
        ).latest.publication_id
        == latest.publication_id
    )
    scoped = repository.current_root_page(scope, edition, source_spans=body)
    selected = next(view for view in scoped if view.root_node_id == "daily")
    assert selected.latest.publication_id == original.publication_id
    assert selected.last_usable.publication_id == original.publication_id
    assert amount(selected.last_usable.root) == 300
