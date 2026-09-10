"""A known metadata prefix reaches a formal title through the complete source."""

from copy import deepcopy
from dataclasses import asdict, replace

import pytest
from familycare_api.insurance_documents.metadata_header_validation import (
    header_selection as api_header,
)
from familycare_api.insurance_documents.metadata_validation import validate_component_metadata
from familycare_worker.metadata_header import header_selection as worker_header

from apps.api.tests.test_metadata_header_budget import _node
from apps.api.tests.test_metadata_header_regions import _proposal
from workers.analyzer.tests.test_document_structure import _build, _extraction, _page
from workers.analyzer.tests.test_document_text_lines import _words


def _source(*, prefix=("Sample Assurance",), title="보험 약관", fault=None):
    lines = [*prefix]
    if fault == "intervening_body":
        lines.append("Synthetic intervening body")
    if title is not None:
        lines.append(title)
    lines.extend(["상품코드: SYNTHETIC-PRELUDE", "Synthetic ordinary body"])
    blocks = _words(lines)
    blocks.append(
        {"text": "Synthetic-sidebar", "reading_order": len(blocks), "bbox": [400, 20, 550, 30]}
    )
    if fault in {
        "preceding_body",
        "reference",
        "unlocated",
        "overlap",
        "ambiguous_top",
        "earlier_other_title",
    }:
        blocks.append(
            {
                "text": {
                    "preceding_body": "Synthetic preceding body",
                    "reference": "청구 제출서류",
                    "unlocated": "Synthetic unlocated body",
                    "overlap": "Synthetic overlapping body",
                    "ambiguous_top": "Another Assurance",
                    "earlier_other_title": "보험 증권",
                }[fault],
                "reading_order": len(blocks),
                "bbox": {
                    "preceding_body": [10, 1, 200, 11],
                    "reference": [300, 25, 390, 34],
                    "unlocated": None,
                    "overlap": [15, 22, 110, 29],
                    "ambiguous_top": [250, 20, 390, 30],
                    "earlier_other_title": [300, 31, 390, 34],
                }[fault],
            }
        )
    if fault in {"other_column_title", "distant_title"}:
        for block in blocks:
            if 35 <= block["bbox"][1] < 60:
                axis = 0 if fault == "other_column_title" else 1
                block["bbox"][axis] += 250
                block["bbox"][axis + 2] += 250
    source = _build(_extraction(_page(1, blocks)))
    if fault == "forged_lineage":
        line = next(node for node in source.nodes if node.kind == "TEXT_LINE")
        source = replace(
            source,
            nodes=tuple(
                replace(node, text=node.text + " synthetic-tail") if node == line else node
                for node in source.nodes
            ),
        )
    return source


@pytest.mark.parametrize("title,role", [("보험 약관", "terms"), ("보험 증권", "policy")])
@pytest.mark.parametrize(
    "prefix,expected",
    [
        (("Sample Assurance",), ("insurer", "Sample Assurance")),
        (("합성생명보험",), ("insurer", "합성생명보험")),
        (("Sample Policy",), ("product_name", "Sample Policy")),
        (("보험사: Sample Assurance",), ("insurer", "Sample Assurance")),
        (
            ("상품명: Sample Policy", "상품코드: SYNTHETIC-PRELUDE"),
            ("product_name", "Sample Policy"),
        ),
    ],
)
def test_known_prefix_can_reach_the_first_formal_title(prefix, expected, title, role, monkeypatch):
    source = _source(prefix=prefix, title=title)
    original = deepcopy(source.to_dict())
    components = _proposal(source, monkeypatch)["components"]
    assert len(components) == 1
    component = components[0]
    assert component["role"] == role
    assert expected in {(fact["field"], fact["value"]) for fact in component["facts"]}
    assert component["unresolved_fields"] == []
    assert validate_component_metadata(component, original, revision="document-metadata-v11")
    assert source.to_dict() == original


def test_known_prefix_does_not_establish_a_role_without_a_title(monkeypatch):
    assert _proposal(_source(title=None), monkeypatch)["components"] == []


@pytest.mark.parametrize("prefix", [("합성생명",), ("합성화재",), ("합성주식회사",), ("무배당",)])
def test_broad_cover_preludes_do_not_authorize_a_new_header(prefix, monkeypatch):
    assert _proposal(_source(prefix=prefix), monkeypatch)["components"] == []


@pytest.mark.parametrize(
    "fault",
    [
        "preceding_body",
        "intervening_body",
        "reference",
        "unlocated",
        "overlap",
        "ambiguous_top",
        "other_column_title",
        "distant_title",
        "forged_lineage",
        "earlier_other_title",
    ],
)
def test_prefix_cannot_bypass_an_unproved_source_boundary(fault, monkeypatch):
    assert _proposal(_source(fault=fault), monkeypatch)["components"] == []


@pytest.mark.parametrize("revision", ["document-metadata-v9", "document-metadata-v10"])
def test_historical_versions_keep_their_existing_prefix_boundary(revision, monkeypatch):
    assert _proposal(_source(), monkeypatch, revision)["components"] == []


@pytest.mark.parametrize("fault", ["unknown_prefix", "intervening_body", "reference", "unlocated"])
def test_api_independently_rejects_new_source_barriers(fault, monkeypatch):
    source = _source()
    component = _proposal(source, monkeypatch)["components"][0]
    retained = deepcopy(source.to_dict())
    node = deepcopy(next(node for node in retained["nodes"] if node["text"] == "Synthetic-sidebar"))
    node.update(
        node_id="synthetic-new-prefix-barrier",
        text="청구 제출서류" if fault == "reference" else "Synthetic extra body",
        bbox={
            "unknown_prefix": [10, 1, 200, 11],
            "intervening_body": [10, 31, 200, 34],
            "reference": [300, 25, 390, 34],
            "unlocated": None,
        }[fault],
    )
    retained["nodes"].append(node)
    assert (
        validate_component_metadata(component, retained, revision="document-metadata-v11") is None
    )


def test_proved_prefix_does_not_expand_caption_to_title_distance(monkeypatch):
    source = _source(prefix=("Sample Assurance", "Sample Policy", "상품코드: SYNTHETIC-PRELUDE"))
    component = _proposal(source, monkeypatch)["components"][0]
    assert component["role"] == "terms"
    assert "insurer" not in {fact["field"] for fact in component["facts"]}
    assert validate_component_metadata(
        component, source.to_dict(), revision="document-metadata-v11"
    )


@pytest.mark.parametrize("prefix", [("계약일: invalid-date",), ("보험사: Sample of document",)])
def test_invalid_or_reference_field_cannot_start_the_prefix(prefix, monkeypatch):
    assert _proposal(_source(prefix=prefix), monkeypatch)["components"] == []


def test_valid_explicit_table_field_can_precede_a_single_cell_title(monkeypatch):
    raw = _extraction(
        _page(1, [{"text": "Synthetic-sidebar", "reading_order": 0, "bbox": [400, 20, 550, 30]}])
    )
    raw["pages"][0]["tables"] = [
        {
            "bbox": [10, 20, 250, 45],
            "metadata_json": {"header_rows": []},
            "cells": [
                {"row_index": 0, "column_index": 0, "text": "보험사", "bbox": [10, 20, 55, 30]},
                {
                    "row_index": 0,
                    "column_index": 1,
                    "text": "Sample Assurance",
                    "bbox": [55, 20, 250, 30],
                },
                {"row_index": 1, "column_index": 0, "text": "보험 약관", "bbox": [10, 35, 250, 45]},
            ],
        }
    ]
    source = _build(raw)
    component = _proposal(source, monkeypatch)["components"][0]
    assert {(fact["field"], fact["value"]) for fact in component["facts"]} == {
        ("insurer", "Sample Assurance")
    }
    assert component["unresolved_fields"] == []
    assert validate_component_metadata(
        component, source.to_dict(), revision="document-metadata-v11"
    )


@pytest.mark.parametrize("consumer", ["worker", "api"])
def test_prefix_budget_exhaustion_cannot_promote_a_partial_header(consumer):
    nodes = [_node(i, "상품명: Sample Policy", 10 + 1.5 * i) for i in range(200)]
    nodes.append(_node(200, "보험약관", 310))
    original = [asdict(node) for node in nodes]
    if consumer == "worker":
        result = worker_header(
            nodes,
            title=lambda node: node.text == "보험약관",
            metadata=lambda node: node.text.startswith("상품명:"),
            prelude=lambda node: node.text.startswith("상품명:"),
            reference=lambda node: False,
        )
    else:
        result = api_header(
            original,
            title=lambda node: node["text"] == "보험약관",
            metadata=lambda node: node["text"].startswith("상품명:"),
            prelude=lambda node: node["text"].startswith("상품명:"),
            reference=lambda node: False,
        )
    assert result is None
    assert [asdict(node) for node in nodes] == original
