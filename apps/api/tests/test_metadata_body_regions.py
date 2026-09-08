"""Independent API validation consumes region-scoped Worker witnesses."""

from dataclasses import replace
from uuid import UUID

import pytest
from familycare_api.insurance_documents.metadata_validation import validate_component_metadata
from familycare_api.insurance_documents.terms_body_validation import body_evidence
from familycare_worker.document_metadata import metadata_proposal

from workers.analyzer.tests.test_document_metadata import _structure
from workers.analyzer.tests.test_terms_body import _columns, _table_nodes

FIRST = "제7조 (가상 지급 조건)\n회사는 보험수익자에게 보험금을 지급합니다."
SECOND = "제8조 (가상 제외 조건)\n회사는 보험금을 지급하지 않습니다."


@pytest.mark.parametrize(
    "mode", ["two_columns", "reference_column", "forged_column", "external_table"]
)
def test_independent_regions_reach_api_without_inventing_order(mode: str) -> None:
    nodes = list(_columns(FIRST, SECOND))
    if mode == "reference_column":
        nodes = list(_columns("다음은 약관을 설명하기 위한 예시입니다.\n" + FIRST, SECOND))
    elif mode == "forged_column":
        line = next(node for node in nodes if node.kind == "TEXT_LINE" and node.bbox[0] < 100)
        nodes[nodes.index(line)] = replace(line, text=line.text + " forged")
    elif mode == "external_table":
        nodes = list(_columns(FIRST))
        _, table = _table_nodes()
        table = replace(
            table,
            bbox=(320, 10, 590, 100),
            cells=(replace(table.cells[0], bbox=(320, 10, 590, 100)),),
            context_node_ids=("synthetic-prior-header",),
            continuation_of="synthetic-prior-table",
        )
        nodes.append(table)
    source = replace(_structure(FIRST), nodes=tuple(nodes))
    payload = metadata_proposal(source, UUID(int=905), "c" * 64)
    assert len(payload["components"]) == 1
    component = payload["components"][0]
    assert validate_component_metadata(component, source.to_dict())
    assert component["range_evidence"][0]["article_sequence_verified"] is (mode != "two_columns")


@pytest.mark.parametrize("title", ["상품설명서(요약)", "(상품설명서)", "【목차】"])
def test_document_reference_title_punctuation_cannot_grant_terms_authority(title: str) -> None:
    source = _structure(title + "\n" + FIRST)
    payload = metadata_proposal(source, UUID(int=905), "c" * 64)
    assert not any(component["role"] == "terms" for component in payload["components"])
    assert body_evidence(1, source.to_dict()["nodes"]) is None


def test_empty_retained_blocks_do_not_erase_supported_body() -> None:
    source = _structure(FIRST)
    empty = replace(source.nodes[0], node_id="synthetic-empty-node", text="", reading_order=99)
    source = replace(source, nodes=(*source.nodes, empty))
    assert body_evidence(1, source.to_dict()["nodes"])
