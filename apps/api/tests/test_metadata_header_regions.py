"""Only an independently proved first physical header can recover metadata."""

from copy import deepcopy
from dataclasses import replace
from uuid import UUID

import pytest
from familycare_api.insurance_documents.metadata_validation import validate_component_metadata
from familycare_worker import document_metadata

from workers.analyzer.tests.test_document_structure import _build, _extraction, _page
from workers.analyzer.tests.test_document_text_lines import _words


def _source(*, title="보험 약관", fields=True, fault=None):
    lines = [title]
    if fields:
        lines.extend(["보험사: Sample Assurance", "상품코드: SYNTHETIC-HEADER"])
    lines.append("Synthetic ordinary body text")
    blocks = _words(lines)
    blocks.append(
        {"text": "Synthetic-sidebar", "reading_order": len(blocks), "bbox": [400, 20, 550, 30]}
    )
    if fault in {"preceding_body", "reference", "guide", "unlocated"}:
        text = {
            "preceding_body": "Synthetic preceding body",
            "reference": "청구 제출서류",
            "guide": "약관 읽기 안내",
            "unlocated": "Synthetic unlocated context",
        }[fault]
        blocks.append(
            {
                "text": text,
                "reading_order": len(blocks),
                "bbox": None if fault == "unlocated" else [10, 1, 300, 11],
            }
        )
    if fault == "overlap":
        blocks[-1]["bbox"] = [15, 20, 100, 30]
    if fault == "other_column_fields":
        for block in blocks:
            if block["bbox"][1] in {35, 50}:
                block["bbox"][0] += 300
                block["bbox"][2] += 300
    if fault == "field_barrier":
        blocks.append(
            {
                "text": "Synthetic intervening text",
                "reading_order": len(blocks),
                "bbox": [10, 31, 200, 34],
            }
        )
    source = _build(_extraction(_page(1, blocks)))
    if fault == "forged_lineage":
        line = next(node for node in source.nodes if node.kind == "TEXT_LINE")
        source = replace(
            source,
            nodes=tuple(
                replace(node, text=node.text + " forged") if node == line else node
                for node in source.nodes
            ),
        )
    return source


def _proposal(source, monkeypatch, revision="document-metadata-v11"):
    monkeypatch.setattr(document_metadata, "REVISION", revision)
    return document_metadata.metadata_proposal(source, UUID(int=205), "c" * 64)


@pytest.mark.parametrize("title,role", [("보험 약관", "terms"), ("보험 증권", "policy")])
def test_separate_sidebar_does_not_hide_the_first_proved_header(title, role, monkeypatch):
    source = _source(title=title)
    original = deepcopy(source.to_dict())
    assert any("LINE_COLUMN_CONTEXT_UNRESOLVED" in node.issue_codes for node in source.nodes)
    proposal = _proposal(source, monkeypatch)
    assert len(proposal["components"]) == 1
    component = proposal["components"][0]
    assert component["role"] == role
    assert component["unresolved_fields"] == []
    assert {(fact["field"], fact["value"]) for fact in component["facts"]} == {
        ("insurer", "Sample Assurance"),
        ("product_code", "SYNTHETIC-HEADER"),
    }
    assert validate_component_metadata(component, original, revision="document-metadata-v11")
    assert source.to_dict() == original


def test_v10_keeps_its_existing_unresolved_column_boundary(monkeypatch):
    assert _proposal(_source(), monkeypatch, "document-metadata-v10")["components"] == []


@pytest.mark.parametrize("reference", ["Product brochure (summary)", "［Ｒｅｆｅｒｅｎｃｅ］"])
def test_a_same_height_reference_context_prevents_a_formal_header(reference, monkeypatch):
    source = _source()
    source = replace(
        source,
        nodes=tuple(
            replace(node, text=reference) if node.text == "Synthetic-sidebar" else node
            for node in source.nodes
        ),
    )
    assert _proposal(source, monkeypatch)["components"] == []


def test_role_without_printed_identity_does_not_invent_metadata(monkeypatch):
    source = _source(fields=False)
    component = _proposal(source, monkeypatch)["components"][0]
    assert component["role"] == "terms"
    assert component["facts"] == []
    assert component["authority"] == "CONTENT_CLASSIFICATION_ONLY"
    assert validate_component_metadata(
        component, source.to_dict(), revision="document-metadata-v11"
    )


@pytest.mark.parametrize(
    "fault", ["preceding_body", "reference", "guide", "unlocated", "overlap", "forged_lineage"]
)
def test_unproved_header_does_not_restart_the_page(fault, monkeypatch):
    assert _proposal(_source(fault=fault), monkeypatch)["components"] == []


@pytest.mark.parametrize("fault", ["other_column_fields", "field_barrier"])
def test_fields_outside_the_header_flow_remain_unresolved(fault, monkeypatch):
    source = _source(fault=fault)
    component = _proposal(source, monkeypatch)["components"][0]
    assert component["role"] == "terms"
    assert component["facts"] == []
    assert set(component["unresolved_fields"]) == {"insurer", "product_code"}
    assert validate_component_metadata(
        component, source.to_dict(), revision="document-metadata-v11"
    )


@pytest.mark.parametrize(
    "fault", ["preceding_body", "reference", "guide", "unlocated", "overlap", "forged_lineage"]
)
def test_api_rechecks_the_entire_page_when_rejecting_a_header(fault, monkeypatch):
    source = _source()
    component = _proposal(source, monkeypatch)["components"][0]
    retained = deepcopy(source.to_dict())
    if fault == "overlap":
        sidebar = next(node for node in retained["nodes"] if node["text"] == "Synthetic-sidebar")
        sidebar["bbox"] = [15, 20, 100, 30]
    elif fault == "forged_lineage":
        line = next(node for node in retained["nodes"] if node["kind"] == "TEXT_LINE")
        block = next(
            node
            for node in retained["nodes"]
            if node["node_id"] == line["source_spans"][0]["block_node_id"]
        )
        block["bbox"][0] += 1
    else:
        block = deepcopy(
            next(node for node in retained["nodes"] if node["text"] == "Synthetic-sidebar")
        )
        block.update(
            node_id="synthetic-untrusted-context",
            text={
                "preceding_body": "Synthetic preceding body",
                "reference": "청구 제출서류",
                "guide": "약관 읽기 안내",
                "unlocated": "Synthetic unlocated context",
            }[fault],
            bbox=None if fault == "unlocated" else [10, 1, 300, 11],
        )
        retained["nodes"].append(block)
    assert (
        validate_component_metadata(component, retained, revision="document-metadata-v11") is None
    )


def _table_source(*, fault=None):
    raw = _extraction(
        _page(
            1,
            [
                {"text": "Synthetic-sidebar", "reading_order": 0, "bbox": [400, 20, 550, 30]},
            ],
        )
    )
    raw["pages"][0]["tables"] = [
        {
            "bbox": [10, 20, 250, 75],
            "metadata_json": {"header_rows": []},
            "cells": [
                {"row_index": 0, "column_index": 0, "text": "보험 약관", "bbox": [10, 20, 250, 30]},
                {"row_index": 1, "column_index": 0, "text": "보험사", "bbox": [10, 35, 55, 45]},
                {
                    "row_index": 1,
                    "column_index": 1,
                    "text": "Sample Assurance",
                    "bbox": [55, 35, 250, 45],
                },
            ],
        }
    ]
    source = _build(raw)
    if fault in {"external_context", "merged_cell"}:
        rows = []
        for node in source.nodes:
            if node.kind == "TABLE_ROW" and node.row_index == 1:
                node = replace(
                    node,
                    context_node_ids=("synthetic-external-header",)
                    if fault == "external_context"
                    else (),
                    cells=tuple(replace(cell, column_span=2) for cell in node.cells)
                    if fault == "merged_cell"
                    else node.cells,
                )
            rows.append(node)
        source = replace(source, nodes=tuple(rows))
    return source


def test_first_single_cell_title_can_bind_an_explicit_metadata_row(monkeypatch):
    source = _table_source()
    component = _proposal(source, monkeypatch)["components"][0]
    assert {(fact["field"], fact["value"]) for fact in component["facts"]} == {
        ("insurer", "Sample Assurance")
    }
    assert component["unresolved_fields"] == []
    assert validate_component_metadata(
        component, source.to_dict(), revision="document-metadata-v11"
    )


@pytest.mark.parametrize("fault", ["external_context", "merged_cell"])
def test_table_context_cannot_be_invented_from_a_nearby_title(fault, monkeypatch):
    source = _table_source(fault=fault)
    component = _proposal(source, monkeypatch)["components"][0]
    assert component["facts"] == []
    assert component["unresolved_fields"] == ["insurer"]
    assert validate_component_metadata(
        component, source.to_dict(), revision="document-metadata-v11"
    )


@pytest.mark.parametrize(
    "fault", ["duplicate_view", "unrepresented_tail", "other_layer", "distant_fields"]
)
def test_source_ownership_and_field_proximity_are_required(fault, monkeypatch):
    source = _source()
    nodes = list(source.nodes)
    head = next(node for node in nodes if node.kind == "TEXT_LINE")
    if fault == "duplicate_view":
        nodes.append(replace(head, node_id="synthetic-duplicate-view"))
    elif fault == "unrepresented_tail":
        identifier = head.source_spans[0].block_node_id
        nodes = [
            replace(node, text=node.text + " synthetic-tail")
            if node.node_id == identifier
            else node
            for node in nodes
        ]
    else:
        changed = []
        for node in nodes:
            if node.bbox is not None and node.bbox[1] in {35, 50}:
                if fault == "other_layer":
                    node = replace(node, source_layer="ocr")
                else:
                    left, top, right, bottom = node.bbox
                    node = replace(node, bbox=(left, top + 150, right, bottom + 150))
            changed.append(node)
        nodes = changed
    source = replace(source, nodes=tuple(nodes))
    proposal = _proposal(source, monkeypatch)
    if fault in {"duplicate_view", "unrepresented_tail"}:
        assert proposal["components"] == []
    else:
        component = proposal["components"][0]
        assert component["facts"] == []
        assert set(component["unresolved_fields"]) == {"insurer", "product_code"}
        assert validate_component_metadata(
            component, source.to_dict(), revision="document-metadata-v11"
        )
