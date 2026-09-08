"""Local planning covers every original region without inventing semantic authority."""

from uuid import UUID

import pytest
from familycare_api.clauses.source_regions import ClauseSourceSpan
from familycare_api.terms_knowledge.local_candidates import propose_local_candidates
from familycare_api.terms_knowledge.source_layout import SemanticSourceLayout, SemanticSourceRegion
from familycare_api.terms_knowledge.source_verification import SourceSnapshot, verify_and_compile

from apps.api.tests.test_terms_semantic_core import amount
from apps.api.tests.test_terms_source_verification import (
    CLASS,
    DAILY,
    FIXED,
    FOOTNOTE,
    LIMIT,
    source_fixture,
)


def _snapshot(records, *, revision=1):
    _, sources = source_fixture()
    source = sources["terms"].source.model_copy(
        update={
            "generation_id": str(UUID(int=100 + revision, version=4)),
            "structure_identity_sha256": str(revision) * 64,
        }
    )
    regions = []
    for index, (label, lines) in enumerate(records):
        kind = (
            "article"
            if label.startswith("Article")
            else "appendix"
            if label.startswith("Appendix")
            else "footnote"
        )
        spans = tuple(
            ClauseSourceSpan(
                f"source-{revision}-{index}-{ordinal}",
                1,
                0,
                len(text),
                text,
                "native",
                (0.0, float(ordinal * 20), 500.0, float(ordinal * 20 + 10)),
            )
            for ordinal, text in enumerate([label, *lines])
        )
        regions.append(
            SemanticSourceRegion(f"region-{revision}-{index}", label, kind, spans[0], spans, True)
        )
    layout = SemanticSourceLayout(tuple(regions), tuple(r.region_id for r in regions), True)
    return SourceSnapshot(source, layout)


def _records():
    return [
        ("Article 1", [DAILY, LIMIT, "Apply Footnote 1.", "Apply Appendix 1."]),
        ("Footnote 1", [FOOTNOTE]),
        ("Appendix 1", [CLASS]),
        ("Article 2", [FIXED]),
    ]


def _compiled(plan, snapshot):
    labels = {r.region_id: r.label for r in snapshot.layout.regions}
    result = {}
    for graph in plan.graphs:
        root_node = next(n for n in graph["nodes"] if n["node_id"] == graph["roots"][0])
        label = labels[root_node["region_ids"][0]]
        result[label] = verify_and_compile(graph, sources={"terms": snapshot}).compilation.roots[0]
    return result


def test_supported_originals_produce_separate_roots_and_complete_run_coverage() -> None:
    snapshot = _snapshot(_records())
    plan = propose_local_candidates(snapshot)
    assert len(plan.graphs) == 2
    assert plan.consumed_region_ids == snapshot.layout.expected_region_ids
    assert plan.unresolved_region_ids == ()
    result = _compiled(plan, snapshot)
    assert amount(result["Article 1"]) == 300
    assert result["Article 2"].calculation is not None


def test_shared_semantic_identity_survives_new_source_proof_and_footnote_change() -> None:
    first = _snapshot(_records())
    records = _records()
    records[1] = ("Footnote 1", [FOOTNOTE.replace("2", "1")])
    later = _snapshot(records, revision=2)
    old_plan, new_plan = propose_local_candidates(first), propose_local_candidates(later)
    old, new = _compiled(old_plan, first), _compiled(new_plan, later)
    assert amount(new["Article 1"]) == 400
    assert old["Article 1"].semantic_sha256 != new["Article 1"].semantic_sha256
    assert old["Article 2"].semantic_sha256 == new["Article 2"].semantic_sha256
    assert old["Article 2"].manifest_sha256 != new["Article 2"].manifest_sha256
    assert {n["node_id"] for g in old_plan.graphs for n in g["nodes"]} == {
        n["node_id"] for g in new_plan.graphs for n in g["nodes"]
    }


def test_unknown_body_refuses_affected_root_but_retains_original_explanation() -> None:
    records = _records()
    unknown = "A synthetic unsupported exception also applies."
    records[0][1].append(unknown)
    snapshot = _snapshot(records)
    plan = propose_local_candidates(snapshot)
    result = _compiled(plan, snapshot)
    assert result["Article 1"].calculation is None
    assert result["Article 2"].calculation is not None
    assert snapshot.layout.regions[0].region_id in plan.unresolved_region_ids
    assert any(unknown in e.statement for e in result["Article 1"].explanations)


def test_more_than_thirty_two_articles_are_all_planned_without_truncation() -> None:
    snapshot = _snapshot([(f"Article {number}", [FIXED]) for number in range(1, 41)])
    plan = propose_local_candidates(snapshot)
    assert len(plan.graphs) == 40
    assert plan.consumed_region_ids == snapshot.layout.expected_region_ids
    assert plan.unresolved_region_ids == ()
    assert all(len(graph["roots"]) == 1 for graph in plan.graphs)
    for graph in plan.graphs:
        result = verify_and_compile(graph, sources={"terms": snapshot}).compilation
        assert result.roots[0].calculation is not None
        assert not result.processing_complete


@pytest.mark.parametrize("fault", ["missing", "cycle"])
def test_missing_or_cyclic_original_references_are_unresolved(fault) -> None:
    records = _records()
    if fault == "missing":
        records[0][1][2] = "Apply Footnote 9."
    else:
        records[1][1].append("Apply Article 1.")
    snapshot = _snapshot(records)
    plan = propose_local_candidates(snapshot)
    result = _compiled(plan, snapshot)
    assert result["Article 1"].calculation is None
    assert result["Article 2"].calculation is not None
    assert snapshot.layout.regions[0].region_id in plan.unresolved_region_ids
    assert any(
        d.code == ("DEPENDENCY_MISSING" if fault == "missing" else "DEPENDENCY_CYCLE")
        for d in result["Article 1"].diagnostics
    )


def test_override_requires_the_original_source_and_compatible_target_scope() -> None:
    snapshot = _snapshot(
        [
            ("Article 1", [FIXED]),
            ("Article 2", [FIXED.replace("50", "70"), "Article 2 replaces Article 1."]),
            ("Article 3", [FIXED.replace("50", "90"), "Article 99 replaces Article 1."]),
        ]
    )
    plan = propose_local_candidates(snapshot)
    result = _compiled(plan, snapshot)
    assert amount(result["Article 2"]) == 70
    assert result["Article 3"].calculation is None
    assert snapshot.layout.regions[2].region_id in plan.unresolved_region_ids


def test_oversized_closure_is_explicitly_unresolved_not_a_truncated_executable_graph() -> None:
    snapshot = _snapshot(
        [("Article 1", [FIXED, *["Eligible admission days range from 1 to 10 inclusive."] * 256])]
    )
    plan = propose_local_candidates(snapshot)
    assert plan.unresolved_region_ids == snapshot.layout.expected_region_ids
    assert not plan.consumed_region_ids
    assert all(len(g["nodes"]) <= 256 and len(g["citations"]) <= 1024 for g in plan.graphs)
    assert _compiled(plan, snapshot)["Article 1"].calculation is None


def test_condition_only_article_preserves_every_supported_condition() -> None:
    snapshot = _snapshot(
        [
            (
                "Article 1",
                [
                    "Eligible admission days range from 1 to 10 inclusive.",
                    "Payment requires fewer than 2 prior occurrences.",
                ],
            )
        ]
    )
    plan = propose_local_candidates(snapshot)
    result = _compiled(plan, snapshot)["Article 1"]
    assert result.calculation is None and len(result.rules) == 2
    assert plan.unresolved_region_ids == ()


def test_unreferenced_appendix_is_durably_planned_and_examples_are_excluded() -> None:
    from dataclasses import replace

    snapshot = _snapshot([("Article 1", [FIXED]), ("Appendix 1", [CLASS]), ("Article 2", [FIXED])])
    example = replace(
        snapshot.layout.regions[-1], kind="unresolved", reason_codes=("LOCAL_EXAMPLE_EXCLUDED",)
    )
    snapshot = replace(
        snapshot, layout=replace(snapshot.layout, regions=(*snapshot.layout.regions[:-1], example))
    )
    plan = propose_local_candidates(snapshot)
    assert len(plan.graphs) == 2
    assert plan.consumed_region_ids == snapshot.layout.expected_region_ids
    assert not plan.unresolved_region_ids
    assert all(
        example.region_id not in node["region_ids"]
        for graph in plan.graphs
        for node in graph["nodes"]
    )


def test_supported_daily_without_explicit_exclusion_basis_remains_unresolved() -> None:
    snapshot = _snapshot([("Article 1", [DAILY]), ("Article 2", [FIXED])])
    plan = propose_local_candidates(snapshot)
    assert plan.unresolved_region_ids == (snapshot.layout.regions[0].region_id,)
    assert _compiled(plan, snapshot)["Article 2"].calculation is not None


def test_duplicate_original_region_identity_is_rejected_without_partial_output() -> None:
    from dataclasses import replace

    snapshot = _snapshot([("Article 1", [FIXED])])
    snapshot = replace(
        snapshot, layout=replace(snapshot.layout, regions=snapshot.layout.regions * 2)
    )
    with pytest.raises(ValueError, match="LOCAL_SOURCE_REGION_IDENTITY_INVALID"):
        propose_local_candidates(snapshot)


def test_node_citation_limit_does_not_publish_a_truncated_reference_closure() -> None:
    snapshot = _snapshot(
        [("Article 1", [FIXED, *["Apply Appendix 1."] * 64]), ("Appendix 1", [CLASS])]
    )
    plan = propose_local_candidates(snapshot)
    assert snapshot.layout.regions[0].region_id in plan.unresolved_region_ids
    assert all(len(node["citation_ids"]) <= 64 for graph in plan.graphs for node in graph["nodes"])
    assert _compiled(plan, snapshot)["Article 1"].calculation is None
