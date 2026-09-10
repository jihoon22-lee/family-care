"""Physical clause flow keeps retained extractor ordinals and original word proofs."""

import json
from copy import deepcopy
from dataclasses import replace

from familycare_api.insurance_documents.terms_body_validation import body_evidence
from familycare_worker.terms_body import observe_terms_body

from workers.analyzer.tests.test_document_structure import _build, _extraction, _page
from workers.analyzer.tests.test_document_text_lines import _words

HEADING = "제7조 (가상 지급 조건)"
BODY = "회사는 보험수익자에게 보험금을 지급합니다."


def reversed_extractor_flow(*, unlocated_barrier=False, overlap=False):
    """Emit the lower sentence first, as an extractor may traverse PDF objects.

    Geometry and original ordinals are established before building any retained
    nodes. Each visible line still has consecutive left-to-right source words.
    """
    sentences = [BODY]
    if unlocated_barrier:
        sentences.append("Synthetic unresolved passage")
    sentences.append(HEADING)
    blocks = _words(sentences)
    body_count = len(BODY.split())
    heading_start = len(blocks) - len(HEADING.split())
    for index, block in enumerate(blocks):
        if body_count <= index < heading_start:
            block["bbox"] = None
            continue
        top = (25.0 if overlap else 35.0) if index < body_count else 20.0
        left, _, right, _ = block["bbox"]
        block["bbox"] = [left, top, right, top + 10]
    return _build(_extraction(_page(1, blocks)))


def retained_nodes(source):
    """Exercise JSON array/dictionary shapes from the retained page projection."""
    return json.loads(json.dumps(source.to_dict()))["nodes"]


def test_current_physical_flow_recovers_original_body_without_renumbering_retained_words():
    source = reversed_extractor_flow()
    original = deepcopy(source.to_dict())
    lines = {node.text: node for node in source.nodes if node.kind == "TEXT_LINE"}
    assert lines[BODY].reading_order < lines[HEADING].reading_order
    assert lines[HEADING].bbox[3] < lines[BODY].bbox[1]

    observed = observe_terms_body(1, source.nodes)
    assert observed.status == "SUPPORTED"
    assert len(observed.provisions) == 1
    assert observed.provisions[0].heading.node_id == lines[HEADING].node_id
    assert observed.provisions[0].body[0].node_id == lines[BODY].node_id

    nodes = retained_nodes(source)
    before_validation = deepcopy(nodes)
    evidence = body_evidence(1, nodes, metadata_revision="document-metadata-v10")
    assert evidence is not None
    assert evidence[0] == (7,)
    evidence_nodes = {span["node_id"] for span in evidence[1]}
    assert lines[HEADING].node_id in evidence_nodes
    assert lines[BODY].node_id in evidence_nodes
    assert nodes == before_validation
    assert source.to_dict() == original


def test_retained_v9_does_not_reinterpret_source_order_using_the_new_physical_flow():
    source = reversed_extractor_flow()
    nodes = retained_nodes(source)
    original = deepcopy(nodes)
    assert body_evidence(1, nodes, metadata_revision="document-metadata-v9") is None
    assert nodes == original


def test_unlocated_passage_between_reversed_source_ordinals_remains_a_flow_barrier():
    source = reversed_extractor_flow(unlocated_barrier=True)
    original = deepcopy(source.to_dict())
    lines = {node.text: node for node in source.nodes if node.kind == "TEXT_LINE"}
    barriers = [node for node in source.nodes if node.bbox is None]
    assert barriers
    assert all(
        lines[BODY].reading_order < node.reading_order < lines[HEADING].reading_order
        for node in barriers
    )
    assert observe_terms_body(1, source.nodes).status != "SUPPORTED"
    assert (
        body_evidence(1, retained_nodes(source), metadata_revision="document-metadata-v10") is None
    )
    assert source.to_dict() == original


def test_actual_overlapping_lines_are_not_repaired_by_physical_flow_ordering():
    source = reversed_extractor_flow(overlap=True)
    original = deepcopy(source.to_dict())
    assert observe_terms_body(1, source.nodes).status != "SUPPORTED"
    assert (
        body_evidence(1, retained_nodes(source), metadata_revision="document-metadata-v10") is None
    )
    assert source.to_dict() == original


def test_physical_flow_still_rejects_forged_word_geometry_inside_a_derived_line():
    source = reversed_extractor_flow()
    line = next(node for node in source.nodes if node.kind == "TEXT_LINE" and node.text == BODY)
    nodes = list(source.nodes)
    by_id = {node.node_id: node for node in nodes}
    first, second = [by_id[span.block_node_id] for span in line.source_spans[:2]]
    nodes[nodes.index(first)] = replace(first, bbox=second.bbox)
    nodes[nodes.index(second)] = replace(second, bbox=first.bbox)
    changed = replace(source, nodes=tuple(nodes))
    original = deepcopy(changed.to_dict())
    assert observe_terms_body(1, changed.nodes).status != "SUPPORTED"
    assert (
        body_evidence(1, retained_nodes(changed), metadata_revision="document-metadata-v10") is None
    )
    assert changed.to_dict() == original


def test_unlocated_last_extracted_passage_cannot_be_ruled_out_of_reversed_physical_flow():
    raw = reversed_extractor_flow().to_dict()["source_extraction"]
    blocks = raw["pages"][0]["blocks"]
    blocks.append(
        {"text": "Synthetic unlocated passage", "reading_order": len(blocks), "bbox": None}
    )
    source = _build(raw)
    original = deepcopy(source.to_dict())
    assert observe_terms_body(1, source.nodes).status != "SUPPORTED"
    assert (
        body_evidence(1, retained_nodes(source), metadata_revision="document-metadata-v10") is None
    )
    assert source.to_dict() == original
