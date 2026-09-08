"""True retained table rows, complete semantic witnesses and source-bound compilation."""

import json
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from uuid import UUID

import pytest
from familycare_api.clauses.dsl import validate_rule_document
from familycare_api.decisions.domain import FactContext, FactValue
from familycare_api.decisions.operators import evaluate_expression
from familycare_api.terms_knowledge.generated_contracts import SemanticSource
from familycare_api.terms_knowledge.local_candidates import propose_local_candidates
from familycare_api.terms_knowledge.source_layout import observe_semantic_regions
from familycare_api.terms_knowledge.source_verification import SourceSnapshot, verify_and_compile
from familycare_worker.document_structure import build_document_structure

from apps.api.tests.test_terms_semantic_core import amount
from apps.api.tests.test_terms_source_verification import DAILY, FIXED, FOOTNOTE, LIMIT
from workers.analyzer.tests.test_document_structure import _block, _extraction, _page, _table

DECLARATION = (
    "Medical event classification uses synthetic-classification version edition-1; "
    "eligible codes are listed in the following table."
)


def table_extraction(
    *, rows=None, declaration=DECLARATION, exclusion=FOOTNOTE, revision=1, share_appendix=False
):
    source = _extraction(
        _page(
            1,
            [
                _block(
                    "\n".join(
                        [
                            "Article 1",
                            DAILY,
                            LIMIT,
                            "Apply Footnote 1.",
                            "Apply Appendix 1.",
                            "Footnote 1",
                            exclusion,
                        ]
                    )
                )
            ],
        ),
        _page(
            2,
            [_block("Appendix 1\n" + declaration)],
            tables=[_table(rows or [["Code"], ["class-a"]], header_rows=[0])],
        ),
        _page(
            3,
            [
                _block(
                    "Article 2\n"
                    + FIXED
                    + ("\nArticle 3\n" + FIXED + "\nApply Appendix 1." if share_appendix else "")
                )
            ],
        ),
    )
    source["document_version_id"] = str(UUID(int=2000 + revision, version=4))
    source["content_sha256"] = sha256(
        json.dumps(source["pages"], sort_keys=True).encode()
    ).hexdigest()
    return source


def snapshot_from_extraction(extraction, *, revision=1):
    projection = build_document_structure(
        extraction,
        extraction_id=UUID(int=1000 + revision, version=4),
        extraction_revision="synthetic-table-source-v1",
    ).to_dict()
    return snapshot_from_projection(projection, revision=revision), projection


def snapshot_from_projection(projection, *, revision=1):
    layout = observe_semantic_regions(projection, component_page_start=1, component_page_end=3)
    source = SemanticSource.model_validate(
        {
            "source_id": "terms",
            "document_version_id": projection["lineage"]["document_version_id"],
            "terms_edition_id": str(UUID(int=3000 + revision, version=4)),
            "generation_id": str(UUID(int=4000 + revision, version=4)),
            "content_sha256": projection["lineage"]["content_sha256"],
            "structure_identity_sha256": sha256(
                json.dumps(projection, sort_keys=True).encode()
            ).hexdigest(),
        }
    )
    return SourceSnapshot(source, layout)


def compile_snapshot(snapshot):
    proposal = propose_local_candidates(snapshot)
    labels = {r.region_id: r.label for r in snapshot.layout.regions}
    result = {}
    for graph in proposal.graphs:
        root = next(n for n in graph["nodes"] if n["node_id"] == graph["roots"][0])
        result[labels[root["region_ids"][0]]] = verify_and_compile(
            graph, sources={"terms": snapshot}
        )
    return proposal, result


def classification_result(root, code):
    rule = next(r for r in root.rules if r["rule_kind"] == "classification")
    validated = validate_rule_document(rule, root.citation_ids)
    assert validated.expression is not None
    facts = FactContext(
        medical_event={
            "classification": FactValue(value=code, confirmation="user", evidence_ids=())
        },
        policy={},
        rider={},
        claim_history={},
    )
    return evaluate_expression(validated.expression, facts).result


def test_original_single_code_column_compiles_through_appendix_to_article() -> None:
    snapshot, projection = snapshot_from_extraction(table_extraction())
    assert snapshot.layout.complete
    assert any(node["kind"] == "TABLE_ROW" for node in projection["nodes"])
    proposal, compiled = compile_snapshot(snapshot)
    root = compiled["Article 1"].compilation.roots[0]
    assert proposal.unresolved_region_ids == ()
    assert amount(root) == 300
    assert classification_result(root, "class-a") == "MATCH"
    assert classification_result(root, "class-b") == "NO_MATCH"
    table_node = next(n for n in root.manifest["nodes"] if n["payload"]["kind"] == "classification")
    assert table_node["statement"] == DECLARATION + "\nCode\nclass-a"
    assert len(table_node["citation_ids"]) == 3
    assert root.classification_scopes[0]["code_version"] == "edition-1"


def test_changed_immutable_table_and_footnote_only_change_dependent_meaning() -> None:
    old, _ = snapshot_from_extraction(table_extraction())
    later, _ = snapshot_from_extraction(
        table_extraction(
            rows=[["Code"], ["class-b"]], exclusion=FOOTNOTE.replace("2", "1"), revision=2
        ),
        revision=2,
    )
    _, before = compile_snapshot(old)
    _, after = compile_snapshot(later)
    prior = before["Article 1"].compilation.roots[0]
    updated = after["Article 1"].compilation.roots[0]
    assert amount(prior) == 300 and amount(updated) == 400
    assert classification_result(prior, "class-a") == "MATCH"
    assert classification_result(updated, "class-a") == "NO_MATCH"
    assert classification_result(updated, "class-b") == "MATCH"
    assert prior.semantic_sha256 != updated.semantic_sha256
    unchanged = before["Article 2"].compilation.roots[0]
    successor = after["Article 2"].compilation.roots[0]
    assert unchanged.semantic_sha256 == successor.semantic_sha256
    assert unchanged.manifest_sha256 != successor.manifest_sha256
    assert amount(prior) == 300


@pytest.mark.parametrize(
    "rows",
    [
        [["Code", "Description"], ["class-a", "Synthetic description"]],
        [["Code", ""], ["class-a", ""]],
        [["Code"], ["class-a"], ["class-a"]],
        [["Code"], ["CLASS-A"]],
        [["Code"], ["c00-c97"]],
        [["Code"], ["class-a..class-c"]],
        [["Code"], ["class-a*"]],
        [["Code"], ["class-a"], ["Except recurring claims."]],
    ],
)
def test_extra_columns_duplicates_ranges_or_exceptions_never_become_partial_code_lists(
    rows,
) -> None:
    snapshot, _ = snapshot_from_extraction(table_extraction(rows=rows))
    assert snapshot.layout.complete
    proposal, compiled = compile_snapshot(snapshot)
    assert proposal.unresolved_region_ids
    root = compiled["Article 1"].compilation.roots[0]
    assert root.calculation is None
    assert amount(compiled["Article 2"].compilation.roots[0]) == 50


def test_plain_prose_code_lines_cannot_claim_original_table_authority() -> None:
    source = table_extraction()
    source["pages"][1]["tables"] = []
    source["pages"][1]["blocks"] = [_block("Appendix 1\n" + DECLARATION + "\nCode\nclass-a")]
    snapshot, _ = snapshot_from_extraction(source)
    assert snapshot.layout.complete
    proposal, compiled = compile_snapshot(snapshot)
    assert proposal.unresolved_region_ids
    assert compiled["Article 1"].compilation.roots[0].calculation is None


@pytest.mark.parametrize(
    "fault",
    [
        "no_metadata",
        "wrong_header",
        "missing_header_citation",
        "missing_row_citation",
        "changed_code",
        "changed_version",
        "reordered_witness",
    ],
)
def test_matching_text_alone_cannot_reuse_a_table_proof(fault) -> None:
    snapshot, _ = snapshot_from_extraction(table_extraction())
    proposal = propose_local_candidates(snapshot)
    graph = deepcopy(
        next(
            g
            for g in proposal.graphs
            if any(n["payload"].get("mode") == "daily" for n in g["nodes"])
        )
    )
    table_node = next(n for n in graph["nodes"] if n["payload"]["kind"] == "classification")
    if fault in {"no_metadata", "wrong_header"}:
        appendix = next(r for r in snapshot.layout.regions if r.kind == "appendix")
        rows = (
            ()
            if fault == "no_metadata"
            else tuple(
                replace(row, header_node_ids=("unrelated-header",))
                if row.row_role == "data"
                else row
                for row in appendix.table_rows
            )
        )
        snapshot = replace(
            snapshot,
            layout=replace(
                snapshot.layout,
                regions=tuple(
                    replace(r, table_rows=rows) if r == appendix else r
                    for r in snapshot.layout.regions
                ),
            ),
        )
    elif fault == "missing_header_citation":
        table_node["citation_ids"].pop(1)
    elif fault == "missing_row_citation":
        table_node["citation_ids"].pop(2)
    elif fault == "reordered_witness":
        table_node["citation_ids"][1:] = reversed(table_node["citation_ids"][1:])
    elif fault == "changed_code":
        table_node["payload"]["codes"] = ["class-b"]
    else:
        table_node["payload"]["code_version"] = "edition-2"
    result = verify_and_compile(graph, sources={"terms": snapshot})
    assert table_node["node_id"] not in result.verified_node_ids
    assert result.compilation.roots[0].calculation is None


def test_invalid_original_header_reference_is_rejected_by_layout_before_meaning() -> None:
    _, projection = snapshot_from_extraction(table_extraction())
    data = next(
        n for n in projection["nodes"] if n["kind"] == "TABLE_ROW" and n["row_role"] == "data"
    )
    data["context_node_ids"] = [projection["nodes"][0]["node_id"]]
    assert not snapshot_from_projection(projection).layout.complete


def test_unrecognized_note_after_table_stays_in_dependency_coverage() -> None:
    source = table_extraction()
    source["pages"][1]["blocks"].append(
        _block("Unless a synthetic special exception applies.", 1, y=150)
    )
    snapshot, _ = snapshot_from_extraction(source)
    assert snapshot.layout.complete
    proposal, compiled = compile_snapshot(snapshot)
    root = compiled["Article 1"].compilation.roots[0]
    assert proposal.unresolved_region_ids and root.calculation is None
    assert any("special exception" in e.statement for e in root.explanations)


@pytest.mark.parametrize("row_count,computable", [(9, True), (10, False)])
def test_existing_dsl_evidence_budget_does_not_drop_rows(row_count, computable) -> None:
    rows = [["Code"], *[[f"class-{i}"] for i in range(row_count)]]
    snapshot, _ = snapshot_from_extraction(table_extraction(rows=rows))
    _, compiled = compile_snapshot(snapshot)
    root = compiled["Article 1"].compilation.roots[0]
    assert (root.calculation is not None) == computable
    node = next(n for n in root.manifest["nodes"] if n["payload"]["kind"] == "classification")
    assert len(node["payload"]["codes"]) == row_count
    if computable:
        assert amount(root) == 300
    else:
        assert root.diagnostics  # The existing planner marks this root unresolved.


def test_whole_64_row_witness_is_unresolved_when_transport_budget_cannot_hold_it() -> None:
    from familycare_api.terms_knowledge.source_tables import observe_classification_table

    source = table_extraction(rows=[["Code"], *[[f"class-{i}"] for i in range(64)]])
    source["pages"][1]["tables"][0]["bbox"][3] = 790
    snapshot, _ = snapshot_from_extraction(source)
    assert snapshot.layout.complete
    appendix = next(r for r in snapshot.layout.regions if r.kind == "appendix")
    witness = observe_classification_table(appendix)
    assert witness is not None and len(witness.payload["codes"]) == 64
    assert len(witness.spans) == 66
    proposal, compiled = compile_snapshot(snapshot)
    assert proposal.unresolved_region_ids
    root = compiled["Article 1"].compilation.roots[0]
    assert root.calculation is None and root.rules == ()
    assert amount(compiled["Article 2"].compilation.roots[0]) == 50


@pytest.mark.parametrize("field,code", [("diagnosis code", "c00"), ("procedure code", "0wqf0zz")])
def test_table_field_and_original_code_system_version_are_preserved(field, code) -> None:
    declaration = DECLARATION.replace("Medical event classification", f"Medical event {field}")
    snapshot, _ = snapshot_from_extraction(
        table_extraction(rows=[["Code"], [code]], declaration=declaration)
    )
    _, compiled = compile_snapshot(snapshot)
    root = compiled["Article 1"].compilation.roots[0]
    rule = next(r for r in root.rules if r["rule_kind"] == "classification")
    assert rule["expression"]["field"] == "MedicalEvent." + field.replace(" ", "_")
    assert rule["expression"]["value"] == [code]
    assert root.classification_scopes[0]["code_system"] == "synthetic-classification"
    assert root.classification_scopes[0]["code_version"] == "edition-1"


def test_empty_code_column_is_not_a_complete_classification() -> None:
    snapshot, _ = snapshot_from_extraction(table_extraction(rows=[["Code"]]))
    proposal, compiled = compile_snapshot(snapshot)
    assert proposal.unresolved_region_ids
    assert compiled["Article 1"].compilation.roots[0].calculation is None


def test_one_original_table_changes_all_dependent_articles_and_only_them() -> None:
    first, _ = snapshot_from_extraction(table_extraction(share_appendix=True))
    second, _ = snapshot_from_extraction(
        table_extraction(rows=[["Code"], ["class-b"]], revision=2, share_appendix=True), revision=2
    )
    before_plan, before = compile_snapshot(first)
    _, after = compile_snapshot(second)
    assert before_plan.unresolved_region_ids == ()
    table_ids = [
        node["node_id"]
        for graph in before_plan.graphs
        for node in graph["nodes"]
        if node["payload"]["kind"] == "classification"
    ]
    assert len(table_ids) == 2 and len(set(table_ids)) == 1
    for label in ("Article 1", "Article 3"):
        old = before[label].compilation.roots[0]
        updated = after[label].compilation.roots[0]
        assert old.semantic_sha256 != updated.semantic_sha256
        assert classification_result(old, "class-a") == "MATCH"
        assert classification_result(updated, "class-a") == "NO_MATCH"
    assert (
        before["Article 2"].compilation.roots[0].semantic_sha256
        == after["Article 2"].compilation.roots[0].semantic_sha256
    )
