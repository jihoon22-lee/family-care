"""Header proofs fail closed before independent-source scans exceed their budget."""

from dataclasses import asdict, replace

import pytest
from familycare_api.insurance_documents.metadata_header_validation import (
    header_selection as api_header,
)
from familycare_worker.document_structure import StructureCell, StructureNode
from familycare_worker.metadata_header import header_selection as worker_header


def _node(index, text, top):
    return StructureNode(
        node_id=f"synthetic-header-{index}",
        kind="BLOCK",
        page_number=1,
        source_layer="native",
        reading_order=index,
        text=text,
        source_path=f"/synthetic/header/{index}",
        bbox=(10, top, 210, top + 1),
    )


def _source(shape):
    head = _node(0, "보험약관", 10)
    if shape == "long_flow":
        return [head, *(_node(i, "상품명: Sample Policy", 10 + 1.5 * i) for i in range(1, 401))]
    rows = []
    for index in range(1, 513):
        node = _node(index, "합성서식", 20 + 1.5 * index)
        rows.append(
            replace(
                node,
                kind="TABLE_ROW",
                row_index=index,
                row_role="data",
                cells=(StructureCell(index, 0, node.text, node.bbox, node.source_path),),
            )
        )
    assert rows[-1].bbox is not None
    blocks = [
        replace(_node(i, rows[-1].text, rows[-1].bbox[1]), schedulable=False)
        for i in range(513, 641)
    ]
    return [head, *rows, *blocks]


@pytest.mark.parametrize("shape", ["long_flow", "table_representation"])
@pytest.mark.parametrize("consumer", ["worker", "api"])
def test_excessive_header_proof_work_never_returns_a_partial_flow(shape, consumer):
    nodes = _source(shape)
    original = [asdict(node) for node in nodes]
    if consumer == "worker":
        result = worker_header(
            nodes,
            title=lambda node: node.text == "보험약관",
            metadata=lambda node: node.text.startswith("상품명:"),
            reference=lambda node: False,
        )
    else:
        result = api_header(
            original,
            title=lambda node: node["text"] == "보험약관",
            metadata=lambda node: node["text"].startswith("상품명:"),
            reference=lambda node: False,
        )
    assert result is None
    assert [asdict(node) for node in nodes] == original
