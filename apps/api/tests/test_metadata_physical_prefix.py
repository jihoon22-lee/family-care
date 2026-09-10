"""Retained native word order does not hide a proven physical metadata prefix."""

from copy import deepcopy
from uuid import UUID

import pytest
from familycare_api.insurance_documents.metadata_validation import validate_component_metadata
from familycare_worker.document_metadata import metadata_proposal

from apps.api.tests.test_document_metadata_validation import _legacy_identity
from workers.analyzer.tests.test_document_structure import _build, _extraction, _page
from workers.analyzer.tests.test_document_text_lines import _words


@pytest.fixture(autouse=True)
def _historical_v10_producer(monkeypatch):
    """Keep the v10 source-flow acceptance independent of later producers."""
    from familycare_worker import document_metadata, document_metadata_repository

    monkeypatch.setattr(document_metadata, "REVISION", "document-metadata-v10")
    monkeypatch.setattr(document_metadata_repository, "REVISION", "document-metadata-v10")


TITLE = "보험 약관"
INSURER = "보험사: Sample Assurance"
PRODUCT = "상품코드: SYNTHETIC-PHYSICAL"
ARTICLE = "제7조 (가상 지급 조건)"
BODY = "회사는 보험수익자에게 보험금을 지급합니다."


def _source(*, reference_first=False, fault=None, labelled_insurer=True):
    insurer = INSURER if labelled_insurer else "Sample Assurance"
    emitted = [BODY, ARTICLE, PRODUCT, insurer, TITLE]
    physical = [TITLE, insurer, PRODUCT, ARTICLE, BODY]
    blocks = _words(emitted)
    cursor = 0
    for line in emitted:
        for block in blocks[cursor : cursor + len(line.split())]:
            left, _, right, _ = block["bbox"]
            top = 20 + 15 * physical.index(line)
            if fault == "column" and line == TITLE:
                left += 250
                right += 250
            if fault == "overlap" and line == TITLE:
                top = 30
            block["bbox"] = [left, top, right, top + 10]
        cursor += len(line.split())
    if fault == "unlocated":
        blocks.append(
            {"text": "Synthetic unresolved passage", "reading_order": len(blocks), "bbox": None}
        )
    pages = [_page(2 if reference_first else 1, blocks)]
    if reference_first:
        pages.insert(0, _page(1, _words(["청구 제출서류"])))
    return _build(_extraction(*pages))


@pytest.mark.parametrize("reference_first", [False, True])
def test_proven_native_prefix_recovers_role_and_identity_without_rewriting_source(reference_first):
    source = _source(reference_first=reference_first)
    retained = source.to_dict()
    original = deepcopy(retained)
    proposal = metadata_proposal(source, UUID(int=205), "c" * 64)
    assert proposal["revision"] == "document-metadata-v10"
    assert len(proposal["components"]) == 1
    component = proposal["components"][0]
    assert component["role"] == "terms"
    assert component["unresolved_fields"] == []
    assert {(fact["field"], fact["value"]) for fact in component["facts"]} == {
        ("insurer", "Sample Assurance"),
        ("product_code", "SYNTHETIC-PHYSICAL"),
    }
    assert (
        validate_component_metadata(component, retained, revision="document-metadata-v10")
        is not None
    )
    legacy = deepcopy(component)
    _legacy_identity(legacy, retained, revision="document-metadata-v9")
    assert validate_component_metadata(legacy, retained, revision="document-metadata-v9") is None
    assert source.to_dict() == retained == original


@pytest.mark.parametrize("fault", ["column", "overlap", "unlocated"])
def test_uncertain_physical_prefix_does_not_pull_a_late_title_into_metadata(fault):
    source = _source(fault=fault)
    original = deepcopy(source.to_dict())
    proposal = metadata_proposal(source, UUID(int=205), "c" * 64)
    assert not any(
        component["facts"] and not component["unresolved_fields"]
        for component in proposal["components"]
    )
    assert source.to_dict() == original


def test_adjacent_unlabelled_insurer_caption_uses_physical_role_region():
    source = _source(labelled_insurer=False)
    original = deepcopy(source.to_dict())
    proposal = metadata_proposal(source, UUID(int=205), "c" * 64)
    component = proposal["components"][0]
    assert any(
        fact["field"] == "insurer" and fact["value"] == "Sample Assurance"
        for fact in component["facts"]
    )
    assert (
        validate_component_metadata(component, source.to_dict(), revision="document-metadata-v10")
        is not None
    )
    assert source.to_dict() == original


def test_physical_prefix_can_include_a_separate_metadata_table_row():
    raw = _source().to_dict()["source_extraction"]
    page = raw["pages"][0]
    for block in page["blocks"]:
        if block["bbox"][1] >= 65:
            block["bbox"][1] += 15
            block["bbox"][3] += 15
    page["tables"] = [
        {
            "bbox": [10, 65, 150, 75],
            "cells": [
                {"row_index": 0, "column_index": 0, "text": "판본일", "bbox": [10, 65, 45, 75]},
                {
                    "row_index": 0,
                    "column_index": 1,
                    "text": "2024-01-01",
                    "bbox": [50, 65, 150, 75],
                },
            ],
        }
    ]
    source = _build(raw)
    original = deepcopy(source.to_dict())
    component = metadata_proposal(source, UUID(int=205), "c" * 64)["components"][0]
    assert component["unresolved_fields"] == []
    assert any(fact["field"] == "edition_date" for fact in component["facts"])
    assert (
        validate_component_metadata(component, original, revision="document-metadata-v10")
        is not None
    )
    assert source.to_dict() == original


@pytest.mark.parametrize("line_text", [TITLE, INSURER])
def test_api_rejects_physical_prefix_with_forged_original_word_geometry(line_text):
    source = _source()
    component = metadata_proposal(source, UUID(int=205), "c" * 64)["components"][0]
    retained = source.to_dict()
    line = next(
        node
        for node in retained["nodes"]
        if node["kind"] == "TEXT_LINE" and node["text"] == line_text
    )
    by_id = {node["node_id"]: node for node in retained["nodes"]}
    first, second = [by_id[span["block_node_id"]] for span in line["source_spans"][:2]]
    first["bbox"], second["bbox"] = second["bbox"], first["bbox"]
    original = deepcopy(retained)
    assert (
        validate_component_metadata(component, retained, revision="document-metadata-v10") is None
    )
    assert retained == original


@pytest.mark.parametrize("fault", ["missing", "wrong_article", "wrong_span"])
def test_v10_keeps_mandatory_range_proof_and_original_anchors(fault):
    source = _source()
    component = metadata_proposal(source, UUID(int=205), "c" * 64)["components"][0]
    if fault == "missing":
        component.pop("range_evidence")
    elif fault == "wrong_article":
        component["range_evidence"][0]["article_numbers"] = [9]
    else:
        component["role_spans"][0]["start"] += 1
    assert (
        validate_component_metadata(component, source.to_dict(), revision="document-metadata-v10")
        is None
    )
