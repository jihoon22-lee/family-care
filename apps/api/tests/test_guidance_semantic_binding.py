"""A verified semantic root must belong to the actual bound original Clause."""

from copy import deepcopy
from dataclasses import replace
from uuid import UUID

import pytest
from familycare_api.clauses.source_regions import ClauseSourceRegion
from familycare_api.guidance.semantic_binding import bind_semantic_root
from familycare_api.terms_knowledge.repository import CurrentSemanticRoot
from familycare_api.terms_knowledge.source_verification import verify_and_compile

from apps.api.tests.test_terms_source_verification import source_fixture


def source_and_root():
    graph, sources = source_fixture()
    snapshot = sources["terms"]
    compiled = verify_and_compile(graph, sources={"terms": snapshot}).compilation.roots[0]
    original = snapshot.layout.regions[0]
    clause = ClauseSourceRegion(
        original.label,
        original.heading,
        original.body_spans,
        "\n".join(s.text for s in original.body_spans),
        snapshot.source.content_sha256,
        "c" * 64,
        True,
        "NEXT_HEADING",
        snapshot.layout.regions[1].heading,
        (),
    )
    return snapshot, clause, CurrentSemanticRoot(UUID(int=100, version=4), compiled)


@pytest.mark.parametrize("mismatch", [None, "clause", "document", "generation", "edition", "sha"])
def test_semantic_binding_requires_original_address_and_retains_real_citation_kind(mismatch):
    snapshot, clause, root = source_and_root()
    source = snapshot.source.model_dump()
    if mismatch == "clause":
        other = snapshot.layout.regions[-1]
        clause = replace(clause, heading=other.heading, body=other.body_spans)
    elif mismatch is not None:
        key = {
            "document": "document_version_id",
            "generation": "generation_id",
            "edition": "terms_edition_id",
            "sha": "content_sha256",
        }[mismatch]
        source[key] = "e" * 64 if mismatch == "sha" else str(UUID(int=999, version=4))
    result = bind_semantic_root(root, clause, source)
    if mismatch is not None:
        assert result is None
        return
    assert result is not None and result.calculation is not None
    assert result.calculation.source_kind == "SEMANTIC_NODE"
    assert result.calculation.publication_id == root.publication_id
    assert result.calculation.semantic_node_id == root.root.root_node_id
    for citation in result.calculation.citations:
        assert citation.evidence.kind == "SEMANTIC_CITATION"
        assert citation.evidence.publication_id == root.publication_id
        assert citation.evidence.document_version_id == UUID(snapshot.source.document_version_id)
        assert citation.evidence.manifest_sha256 == root.root.manifest_sha256
        assert not hasattr(citation.evidence, "evidence_id")


def test_dependency_placement_does_not_change_the_original_calculation_identity():
    snapshot, clause, current = source_and_root()
    graph, sources = source_fixture()
    variant = deepcopy(graph)
    reference = next(
        c["citation_id"] for c in variant["citations"] if c["text"] == "Apply Footnote 1."
    )
    next(n for n in variant["nodes"] if n["node_id"] == "daily")["citation_ids"].remove(reference)
    next(n for n in variant["nodes"] if n["node_id"] == "limit")["citation_ids"].append(reference)
    next(e for e in variant["edges"] if e["to_node_id"] == "exclusion")["from_node_id"] = "limit"
    compiled = verify_and_compile(variant, sources=sources).compilation.roots[0]
    assert not compiled.diagnostics and compiled.calculation is not None
    original = bind_semantic_root(current, clause, snapshot.source.model_dump())
    alternative = bind_semantic_root(
        CurrentSemanticRoot(UUID(int=101, version=4), compiled),
        clause,
        snapshot.source.model_dump(),
    )
    assert original.original_anchor == alternative.original_anchor
