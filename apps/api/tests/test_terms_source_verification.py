"""Original spans, field meanings and relation scope are separate authorities."""

from copy import deepcopy
from uuid import UUID

import pytest
from familycare_api.clauses.source_regions import ClauseSourceSpan
from familycare_api.terms_knowledge.core import parse_knowledge
from familycare_api.terms_knowledge.source_layout import SemanticSourceLayout, SemanticSourceRegion
from familycare_api.terms_knowledge.source_meaning import observe_statement
from familycare_api.terms_knowledge.source_verification import SourceSnapshot, verify_and_compile

from apps.api.tests.test_terms_semantic_core import amount

DAILY = (
    "For each payable admission day, pay the insured amount in KRW; "
    "multiply first, then round the total half up to whole currency units."
)
FOOTNOTE = "Exclude the first 2 admission days."
LIMIT = "The maximum is 10 payable days."
CLASS = "Medical event classification uses synthetic-classification version edition-1: class-a."
FIXED = "The benefit is KRW 50, rounded half up to whole currency units."


def source_fixture():
    source = {
        "source_id": "terms",
        "document_version_id": str(UUID(int=1, version=4)),
        "terms_edition_id": str(UUID(int=2, version=4)),
        "generation_id": str(UUID(int=3, version=4)),
        "content_sha256": "a" * 64,
        "structure_identity_sha256": "b" * 64,
    }
    regions = []
    citations = []
    nodes = []
    texts = [
        ("daily", "Article 1", [DAILY, LIMIT, "Apply Footnote 1.", "Apply Appendix 1."]),
        ("exclusion", "Footnote 1", [FOOTNOTE]),
        ("class", "Appendix 1", [CLASS]),
        ("unrelated", "Article 2", [FIXED]),
    ]
    for page, (key, label, lines) in enumerate(texts, 1):
        heading = ClauseSourceSpan(
            f"heading-{key}", page, 0, len(label), label, "native", (0.0, 0.0, 100.0, 10.0)
        )
        spans = [heading]
        for index, line in enumerate(lines):
            span = ClauseSourceSpan(
                f"source-{key}-{index}",
                page,
                0,
                len(line),
                line,
                "native",
                (0.0, float(20 + index * 20), 500.0, float(30 + index * 20)),
            )
            spans.append(span)
            payload = observe_statement(line)
            node_key = "limit" if line == LIMIT else key
            citation_id = str(UUID(int=20 + len(citations), version=4))
            citations.append(
                {
                    "citation_id": citation_id,
                    "source_id": "terms",
                    "node_id": span.node_id,
                    "page_number": page,
                    "start": 0,
                    "end": len(line),
                    "text": line,
                    "source_layer": "native",
                    "bbox": list(span.bbox),
                }
            )
            if payload is None:
                nodes[-2 if line == "Apply Footnote 1." else -2]["citation_ids"].append(citation_id)
                continue
            nodes.append(
                {
                    "node_id": node_key,
                    "source_id": "terms",
                    "statement": line,
                    "region_ids": [f"region-{key}"],
                    "citation_ids": [citation_id],
                    "payload": payload,
                }
            )
        kind = (
            "article"
            if label.startswith("Article")
            else "footnote"
            if label.startswith("Footnote")
            else "appendix"
        )
        regions.append(
            SemanticSourceRegion(f"region-{key}", label, kind, heading, tuple(spans), True, ())
        )
    payload = {
        "schema_version": "1",
        "schema_revision": "terms-semantic-v1",
        "prompt_revision": "synthetic-v1",
        "model_revision": "synthetic-v1",
        "sources": [source],
        "citations": citations,
        "nodes": nodes,
        "edges": [
            {"from_node_id": "daily", "to_node_id": key, "relation": "DEPENDS_ON"}
            for key in ("limit", "exclusion", "class")
        ],
        "roots": ["daily", "unrelated"],
        "processing": {
            "expected_region_ids": [r.region_id for r in regions],
            "consumed_region_ids": [r.region_id for r in regions],
            "unresolved_region_ids": [],
        },
    }
    graph = parse_knowledge(payload)
    layout = SemanticSourceLayout(tuple(regions), tuple(r.region_id for r in regions), True, ())
    return payload, {"terms": SourceSnapshot(graph.sources[0], layout)}


def test_original_graph_compiles_with_all_independent_proofs():
    graph, sources = source_fixture()
    verified = verify_and_compile(graph, sources=sources)
    assert verified.compilation.processing_complete
    assert amount(verified.compilation.roots[0]) == 300
    assert verified.compilation.roots[1].calculation is not None
    assert len(verified.verified_node_ids) == 5
    assert len(verified.verified_edges) == 3


@pytest.mark.parametrize(
    "fault",
    [
        "amount",
        "unit",
        "span",
        "bbox",
        "layer",
        "edition",
        "generation",
        "structure",
        "scope",
        "missing_footnote",
        "omitted_exception",
    ],
)
def test_changed_claim_cannot_reuse_matching_quote_or_old_proof(fault):
    graph, sources = source_fixture()
    if fault == "amount":
        graph["nodes"][1]["payload"]["value"] = "11"
    elif fault == "unit":
        graph["nodes"][1]["payload"]["unit"] = "amount"
    elif fault == "span":
        graph["citations"][0]["text"] = "X" + graph["citations"][0]["text"][1:]
    elif fault == "bbox":
        graph["citations"][0]["bbox"][0] = 1.0
    elif fault == "layer":
        graph["citations"][0]["source_layer"] = "ocr"
    elif fault in {"edition", "generation", "structure"}:
        field = {
            "edition": "terms_edition_id",
            "generation": "generation_id",
            "structure": "structure_identity_sha256",
        }[fault]
        graph["sources"][0][field] = (
            "c" * 64 if fault == "structure" else str(UUID(int=99, version=4))
        )
    elif fault == "scope":
        graph["nodes"][2]["region_ids"] = ["region-unrelated"]
    elif fault == "missing_footnote":
        graph["edges"] = [e for e in graph["edges"] if e["to_node_id"] != "exclusion"]
    else:
        graph["nodes"] = [n for n in graph["nodes"] if n["node_id"] != "limit"]
        graph["edges"] = [e for e in graph["edges"] if e["to_node_id"] != "limit"]
    result = verify_and_compile(graph, sources=sources).compilation
    assert result.roots[0].calculation is None
    assert result.roots[0].diagnostics
    if fault not in {"edition", "generation", "structure"}:
        assert result.roots[0].explanations
        assert result.roots[1].calculation is not None
    else:
        assert not result.roots[0].explanations


def test_model_completion_cannot_replace_original_region_plan():
    graph, sources = source_fixture()
    graph["nodes"] = [n for n in graph["nodes"] if n["node_id"] != "unrelated"]
    graph["roots"].remove("unrelated")
    graph["processing"]["expected_region_ids"].remove("region-unrelated")
    graph["processing"]["consumed_region_ids"].remove("region-unrelated")
    result = verify_and_compile(graph, sources=sources).compilation
    assert not result.processing_complete
    assert amount(result.roots[0]) == 300


def test_proof_is_bound_to_canonical_graph_and_does_not_mutate_the_input():
    graph, sources = source_fixture()
    original = deepcopy(graph)
    first = verify_and_compile(graph, sources=sources)
    graph["nodes"][1]["payload"]["value"] = "100"
    assert (
        first.canonical_graph_json
        != verify_and_compile(graph, sources=sources).canonical_graph_json
    )
    assert amount(first.compilation.roots[0]) == 300
    assert original["nodes"][1]["payload"]["value"] == "10"


def test_full_processing_ids_cannot_hide_an_unmodeled_original_region():
    graph, sources = source_fixture()
    graph["nodes"] = [n for n in graph["nodes"] if n["node_id"] != "unrelated"]
    graph["roots"].remove("unrelated")
    result = verify_and_compile(graph, sources=sources).compilation
    assert not result.processing_complete
    assert amount(result.roots[0]) == 300


def test_closed_example_cannot_authorize_its_matching_formula():
    from dataclasses import replace

    graph, sources = source_fixture()
    source = sources["terms"]
    example = replace(
        source.layout.regions[0], kind="unresolved", reason_codes=("LOCAL_EXAMPLE_EXCLUDED",)
    )
    sources["terms"] = replace(
        source, layout=replace(source.layout, regions=(example, *source.layout.regions[1:]))
    )
    result = verify_and_compile(graph, sources=sources).compilation
    assert result.roots[0].calculation is None
    assert result.roots[1].calculation is not None


def test_relation_requires_its_original_reference_witness_in_root_evidence():
    graph, sources = source_fixture()
    graph["nodes"][0]["citation_ids"] = graph["nodes"][0]["citation_ids"][:1]
    result = verify_and_compile(graph, sources=sources)
    assert ("daily", "exclusion", "DEPENDS_ON") not in result.verified_edges
    assert result.compilation.roots[0].calculation is None


def test_unverified_candidate_prose_is_not_returned_as_an_original_explanation():
    graph, sources = source_fixture()
    graph["nodes"][0]["statement"] = "Synthetic fabricated definitive assurance"
    result = verify_and_compile(graph, sources=sources).compilation
    assert result.roots[0].calculation is None
    assert all("fabricated" not in e.statement for e in result.roots[0].explanations)


def test_same_reference_label_in_an_unrelated_edition_does_not_block_local_rules():
    from dataclasses import replace

    graph, sources = source_fixture()
    original = sources["terms"]
    other_source = original.source.model_copy(
        update={
            "source_id": "other",
            "terms_edition_id": str(UUID(int=77, version=4)),
            "document_version_id": str(UUID(int=78, version=4)),
        }
    )
    other_region = replace(original.layout.regions[1], region_id="region-other-footnote")
    sources["other"] = SourceSnapshot(
        other_source, SemanticSourceLayout((other_region,), (other_region.region_id,), True, ())
    )
    graph["sources"].append(other_source.model_dump(mode="json"))
    graph["processing"]["expected_region_ids"].append(other_region.region_id)
    graph["processing"]["consumed_region_ids"].append(other_region.region_id)
    result = verify_and_compile(graph, sources=sources).compilation
    assert amount(result.roots[0]) == 300
    assert not result.processing_complete


def test_duplicate_original_region_identity_never_overwrites_a_valid_source():
    from dataclasses import replace

    graph, sources = source_fixture()
    source = sources["terms"]
    sources["terms"] = replace(
        source,
        layout=replace(source.layout, regions=(*source.layout.regions, source.layout.regions[0])),
    )
    result = verify_and_compile(graph, sources=sources).compilation
    assert not result.processing_complete
    assert all(root.calculation is None for root in result.roots)


def test_original_explanations_do_not_retain_unverified_reference_ids():
    graph, sources = source_fixture()
    bad = deepcopy(graph["citations"][0])
    bad["citation_id"] = str(UUID(int=999, version=4))
    bad["text"] = "X" + bad["text"][1:]
    graph["citations"].append(bad)
    graph["nodes"][0]["citation_ids"].append(bad["citation_id"])
    result = verify_and_compile(graph, sources=sources).compilation
    assert all(
        bad["citation_id"] not in explanation.citation_ids
        for explanation in result.roots[0].explanations
    )
