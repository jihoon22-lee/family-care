"""V9 uses constructor geometry while immutable older metadata replays its own rules."""

from copy import deepcopy
from uuid import UUID

import pytest
from familycare_api.clauses.source_regions import observe_clause_source_regions
from familycare_api.insurance_documents.metadata_validation import validate_component_metadata
from familycare_api.insurance_documents.terms_body_validation import body_evidence
from familycare_api.terms_knowledge.source_layout import observe_semantic_regions
from familycare_worker.document_metadata import metadata_proposal
from familycare_worker.terms_body import observe_terms_body

from apps.api.tests.test_document_metadata_validation import _legacy_identity
from workers.analyzer.tests.test_document_structure import _build, _extraction, _page
from workers.analyzer.tests.test_document_text_lines import _words

V9 = "document-metadata-v9"


def _source(change="alternating-baselines", *, formal=False):
    lines = ["제7조 (가상 지급 조건)", "회사는 보험수익자에게 보험금을 지급합니다."]
    if formal:
        lines.insert(0, "보험약관")
    blocks = _words(lines)
    body_top = 20 + 15 * (len(lines) - 1)
    body = [block for block in blocks if block["bbox"][1] == body_top]
    if change == "alternating-baselines":
        body[1]["bbox"][1] -= 2
        body[1]["bbox"][3] -= 2
        body[2]["bbox"][1] += 2
        body[2]["bbox"][3] += 2
    else:
        body[1]["bbox"][1] += 1
        body[1]["bbox"][3] -= 1
        for block in body[2:]:
            block["bbox"][0] += 11
            block["bbox"][2] += 11
    return _build(_extraction(_page(1, blocks)))


def _component(structure, revision=V9):
    component = deepcopy(metadata_proposal(structure, UUID(int=205), "c" * 64)["components"][0])
    _legacy_identity(component, structure.to_dict(), revision=revision)
    return component


@pytest.mark.parametrize("change", ["alternating-baselines", "short-middle-word"])
def test_worker_supported_body_reaches_api_metadata_clause_and_semantic_observation(change):
    structure = _source(change)
    source = structure.to_dict()
    original = deepcopy(source)
    worker = observe_terms_body(1, structure.nodes)
    assert worker.status == "SUPPORTED"
    component = _component(structure)
    assert validate_component_metadata(component, source, revision=V9) is not None
    assert body_evidence(1, source["nodes"], metadata_revision=V9) is not None
    clauses = observe_clause_source_regions(source, 1, component_page_end=1, metadata_revision=V9)
    assert len(clauses.regions) == 1 and clauses.regions[0].complete
    semantic = observe_semantic_regions(
        source, component_page_start=1, component_page_end=1, metadata_revision=V9
    )
    assert semantic.complete and len(semantic.regions) == 1
    assert semantic.regions[0].body_spans[0].text == worker.provisions[0].body[0].text
    assert source == original


@pytest.mark.parametrize("revision", [f"document-metadata-v{number}" for number in range(1, 9)])
def test_old_formal_metadata_keeps_its_original_unsupported_body_result(revision):
    structure = _source(formal=True)
    source = structure.to_dict()
    component = _component(structure, revision)
    if "range_evidence" in component:
        # The old formal-title proof did not recognize the native body. This is
        # retained historical evidence, not a fresh proposal relabelled as old.
        for evidence in component["range_evidence"]:
            evidence["article_numbers"] = []
            evidence["article_sequence_verified"] = False
    original = deepcopy(component)
    assert validate_component_metadata(component, source, revision=revision) is not None
    assert component == original
    assert body_evidence(1, source["nodes"]) is None
    assert body_evidence(1, source["nodes"], metadata_revision=revision) is None
    assert not observe_semantic_regions(
        source, component_page_start=1, component_page_end=1
    ).complete


@pytest.mark.parametrize(
    "fault", ["baseline", "gap", "span", "word-order", "source-layer", "union"]
)
def test_v9_rejects_forged_native_lineage_without_changing_the_source_claim(fault):
    structure = _source()
    source = structure.to_dict()
    component = _component(structure)
    line = next(
        node
        for node in source["nodes"]
        if node["kind"] == "TEXT_LINE" and node["text"].startswith("회사")
    )
    by_id = {node["node_id"]: node for node in source["nodes"]}
    blocks = [by_id[span["block_node_id"]] for span in line["source_spans"]]
    if fault == "baseline":
        for index, block in enumerate(blocks):
            block["bbox"][1] = blocks[0]["bbox"][1] + 2 * index
            block["bbox"][3] = block["bbox"][1] + 10
    elif fault == "gap":
        for block in blocks[2:]:
            block["bbox"][0] += 30
            block["bbox"][2] += 30
    elif fault == "span":
        line["source_spans"][1]["block_start"] = 1
    elif fault == "word-order":
        blocks[1]["reading_order"] += 2
    elif fault == "source-layer":
        blocks[1]["source_layer"] = "ocr"
    if fault in {"baseline", "gap"}:
        line["bbox"] = [
            min(block["bbox"][0] for block in blocks),
            min(block["bbox"][1] for block in blocks),
            max(block["bbox"][2] for block in blocks),
            max(block["bbox"][3] for block in blocks),
        ]
    elif fault == "union":
        line["bbox"][2] += 1
    assert validate_component_metadata(component, source, revision=V9) is None
    assert body_evidence(1, source["nodes"], metadata_revision=V9) is None
    semantic = observe_semantic_regions(
        source, component_page_start=1, component_page_end=1, metadata_revision=V9
    )
    assert not semantic.complete


def test_navigation_lineage_uses_its_metadata_revision():
    from familycare_api.insurance_documents.navigation_page_validation import is_navigation_page
    from familycare_worker.navigation_page import is_navigation_page as worker_navigation

    blocks = _words(["목차", "Sample Terms Guide .... 1"])
    entry = [block for block in blocks if block["bbox"][1] == 35]
    for index, delta in [(1, -2), (2, 2)]:
        entry[index]["bbox"][1] += delta
        entry[index]["bbox"][3] += delta
    structure = _build(_extraction(_page(1, blocks)))
    assert worker_navigation(structure.nodes)
    nodes = structure.to_dict()["nodes"]
    assert not is_navigation_page(nodes)
    assert is_navigation_page(nodes, metadata_revision=V9)
