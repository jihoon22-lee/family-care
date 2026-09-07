"""Wholly synthetic source structure, bounded scheduling and lineage regressions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

import pytest
from familycare_worker.document_structure import (
    DocumentStructureError,
    build_document_structure,
    plan_structure_chunks,
)

DOCUMENT_ID = UUID("00000000-0000-4000-8000-000000000201")
EXTRACTION_ID = UUID("00000000-0000-4000-8000-000000000202")


def _block(text: str, order: int = 0, *, y: int = 10) -> dict[str, Any]:
    return {"text": text, "reading_order": order, "bbox": [10, y, 400, y + 8]}


def _page(number: int, blocks: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "page_number": number,
        "width_points": 600,
        "height_points": 800,
        "blocks": blocks,
        "tables": [],
        "quality": {"classification": "TEXT_SUFFICIENT"},
        **extra,
    }


def _extraction(*pages: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "document_version_id": str(DOCUMENT_ID),
        "content_sha256": "a" * 64,
        "extractor_config_hash": "b" * 64,
        "pages": list(pages),
        "evidence": [],
    }


def _build(source: dict[str, Any], **extra: Any) -> Any:
    return build_document_structure(
        source, extraction_id=EXTRACTION_ID, extraction_revision="synthetic-extractor-v1", **extra
    )


def _table(rows: list[list[str]], **metadata: Any) -> dict[str, Any]:
    return {
        "bbox": [10, 100, 500, 700],
        "cells": [
            {
                "row_index": row_index,
                "column_index": column_index,
                "text": text,
                "bbox": [
                    10 + column_index * 100,
                    100 + row_index * 10,
                    100 + column_index * 100,
                    109 + row_index * 10,
                ],
            }
            for row_index, row in enumerate(rows)
            for column_index, text in enumerate(row)
        ],
        "metadata_json": metadata,
    }


def test_every_character_after_excerpt_and_every_block_after_sixty_four_is_preserved() -> None:
    late_text = "synthetic preface " * 30 + "Late Sample Rider 317"
    blocks = [_block(late_text)] + [_block(f"Synthetic block {i}", i) for i in range(1, 70)]
    source = _extraction(_page(1, blocks))
    structure = _build(source)
    plan = plan_structure_chunks(
        structure, max_content_chars=240, max_context_chars=120, max_chunks=100
    )
    assert structure.to_dict()["source_extraction"] == source
    assert len(structure.nodes) == 70
    assert len(plan.chunks) > 64
    reconstructed: dict[str, str] = {}
    for chunk in plan.chunks:
        assert len(chunk.text) <= 240
        reconstructed[chunk.node_id] = reconstructed.get(chunk.node_id, "") + chunk.text
    assert reconstructed == {node.node_id: node.text for node in structure.nodes}
    assert plan.complete
    assert plan.processed_characters == plan.total_characters


def test_budget_exhaustion_is_an_explicit_nonoverlapping_partition_of_source() -> None:
    structure = _build(_extraction(_page(1, [_block("X" * 701)])))
    plan = plan_structure_chunks(
        structure, max_content_chars=240, max_context_chars=0, max_chunks=2
    )
    assert not plan.complete
    assert [(item.start, item.end) for item in plan.chunks] == [(0, 240), (240, 480)]
    assert [(item.start, item.end, item.reason_code) for item in plan.unprocessed] == [
        (480, 701, "CHUNK_BUDGET_EXHAUSTED")
    ]
    assert plan.processed_characters == 480
    assert plan.total_characters == 701


def test_tables_keep_more_than_thirty_two_rows_and_never_shift_adjacent_amounts() -> None:
    rows = [["담보", "가입금액"]] + [[f"Sample Rider {i}", str(i * 7)] for i in range(1, 41)]
    table = _table(rows, header_rows=[0], unit_block_orders=[1])
    source = _extraction(
        _page(1, [_block("보험증권 가입금액"), _block("단위: 만원", 1, y=30)], tables=[table])
    )
    structure = _build(source)
    plan = plan_structure_chunks(
        structure, max_content_chars=100, max_context_chars=100, max_chunks=100
    )
    row_nodes = [node for node in structure.nodes if node.kind == "TABLE_ROW"]
    assert len(row_nodes) == 41
    for i, node in enumerate(row_nodes[1:], 1):
        assert [(cell.column_index, cell.text) for cell in node.cells] == [
            (0, f"Sample Rider {i}"),
            (1, str(i * 7)),
        ]
        chunk = next(chunk for chunk in plan.chunks if chunk.node_id == node.node_id)
        assert "단위: 만원" in chunk.context_text
        assert "가입금액" in chunk.context_text
        assert chunk.start == 0 and chunk.end == len(node.text)
    assert plan.complete


def test_explicit_table_continuation_carries_header_unit_and_footnote_without_guessing() -> None:
    first = _table(
        [["담보", "금액"], ["Sample A", "5"]],
        header_rows=[0],
        unit_block_orders=[0],
        footnote_block_orders=[1],
    )
    second = _table([["Sample B", "9"]], continuation_of={"page_number": 1, "table_index": 0})
    source = _extraction(
        _page(1, [_block("단위: 만원"), _block("각주: 합성 조건", 1, y=30)], tables=[first]),
        _page(2, [], tables=[second], printed_page_label="별표 2"),
    )
    structure = _build(source)
    plan = plan_structure_chunks(
        structure, max_content_chars=100, max_context_chars=100, max_chunks=50
    )
    row = next(node for node in structure.nodes if node.page_number == 2)
    chunk = next(item for item in plan.chunks if item.node_id == row.node_id)
    assert "담보\t금액" in chunk.context_text
    assert "단위: 만원" in chunk.context_text
    assert "각주: 합성 조건" in chunk.context_text
    assert structure.pages[1].printed_page_label == "별표 2"
    assert row.continuation_of is not None
    assert structure.to_dict()["source_extraction"] == source

    no_relationship = deepcopy(source)
    no_relationship["pages"][1]["tables"][0]["metadata_json"] = {}
    unlinked = _build(no_relationship)
    row = next(node for node in unlinked.nodes if node.page_number == 2)
    assert row.continuation_of is None
    assert not row.context_node_ids
    assert "TABLE_CONTEXT_UNRESOLVED" in row.issue_codes


def test_oversized_row_or_required_context_is_not_silently_truncated() -> None:
    structure = _build(
        _extraction(
            _page(
                1,
                [],
                tables=[_table([["Header " * 20, "금액"], ["Sample A", "7"]], header_rows=[0])],
            )
        )
    )
    plan = plan_structure_chunks(
        structure, max_content_chars=40, max_context_chars=20, max_chunks=10
    )
    assert not plan.chunks
    assert {item.reason_code for item in plan.unprocessed} == {
        "ROW_EXCEEDS_CONTENT_BUDGET",
        "CONTEXT_EXCEEDS_BUDGET",
    }
    assert not plan.complete


def test_composite_document_is_classified_from_content_and_conflicting_page_stays_unresolved() -> (
    None
):
    structure = _build(
        _extraction(
            _page(1, [_block("보험증권\n가입금액\nSample Policy")]),
            _page(2, [_block("보험약관\n제1조 목적\nSample Terms")]),
            _page(3, [_block("보험증권 가입금액 보험약관 제1조")]),
            _page(4, [_block("아무 분류 근거 없는 합성 본문")], document_kind="policy"),
        )
    )
    assert [page.role for page in structure.pages] == ["policy", "terms", "ambiguous", "unknown"]
    assert len(structure.components) == 4
    assert structure.pages[2].classification_codes == ("COMPONENT_ROLE_CONFLICT",)
    assert structure.pages[3].classification_codes == ("COMPONENT_ROLE_UNRESOLVED",)


def test_ocr_required_pages_use_only_successful_revision_bound_ocr_and_keep_both_layers() -> None:
    source = _extraction(
        _page(1, [_block("good native")]),
        _page(2, [_block("garbled")], quality={"classification": "OCR_REQUIRED"}),
        _page(3, [], quality={"classification": "OCR_REQUIRED"}),
    )
    ocr = [
        {"page_number": 2, "status": "completed", "blocks": [_block("recognized synthetic text")]}
    ]
    structure = _build(source, ocr_pages=ocr, ocr_revision="synthetic-ocr-v1")
    assert [page.active_layer for page in structure.pages] == ["native", "ocr", "unavailable"]
    assert structure.to_dict()["source_ocr_pages"] == ocr
    assert structure.to_dict()["source_extraction"] == source
    plan = plan_structure_chunks(
        structure, max_content_chars=100, max_context_chars=0, max_chunks=20
    )
    assert "garbled" not in [chunk.text for chunk in plan.chunks]
    assert "recognized synthetic text" in [chunk.text for chunk in plan.chunks]
    assert any(
        item.page_number == 3 and item.reason_code == "OCR_REQUIRED" for item in plan.unprocessed
    )
    assert not plan.complete


def test_lineage_and_chunk_ids_are_stable_revision_bound_and_do_not_expose_text_in_repr() -> None:
    source = _extraction(_page(1, [_block("Sensitive-shaped wholly synthetic text")]))
    first = _build(source)
    second = _build(deepcopy(source))
    assert first.to_dict() == second.to_dict()
    kwargs = {"max_content_chars": 240, "max_context_chars": 0, "max_chunks": 10}
    assert (
        plan_structure_chunks(first, **kwargs).to_dict()
        == plan_structure_chunks(second, **kwargs).to_dict()
    )
    changed = build_document_structure(
        source, extraction_id=EXTRACTION_ID, extraction_revision="v2"
    )
    assert changed.nodes[0].node_id != first.nodes[0].node_id
    assert first.lineage.document_version_id == DOCUMENT_ID
    assert first.lineage.extraction_id == EXTRACTION_ID
    assert "Sensitive-shaped" not in repr(first)
    assert "Sensitive-shaped" not in repr(first.nodes[0])
    assert "Sensitive-shaped" not in repr(plan_structure_chunks(first, **kwargs).chunks[0])
    source["pages"][0]["blocks"][0]["text"] = "mutated"
    assert first.nodes[0].text == "Sensitive-shaped wholly synthetic text"


def test_duplicate_page_or_cell_source_identity_is_rejected_without_echoing_payload() -> None:
    page = _page(1, [_block("Synthetic duplicate")])
    with pytest.raises(DocumentStructureError, match="^DOCUMENT_STRUCTURE_INVALID$"):
        _build(_extraction(page, deepcopy(page)))
    table = _table([["Synthetic duplicate", "9"]])
    table["cells"].append(deepcopy(table["cells"][0]))
    with pytest.raises(DocumentStructureError, match="^DOCUMENT_STRUCTURE_INVALID$"):
        _build(_extraction(_page(1, [], tables=[table])))


def test_explicit_spans_and_other_source_metadata_survive_without_inferred_span_values() -> None:
    table = _table([["Synthetic merged header", ""], ["Sample A", "17"]], header_rows=[0])
    table["cells"][0]["column_span"] = 2
    table["cells"][0]["row_span"] = 1
    structure = _build(_extraction(_page(1, [], tables=[table])))
    assert structure.nodes[0].cells[0].column_span == 2
    assert structure.nodes[0].cells[0].row_span == 1
    assert structure.nodes[1].cells[0].column_span is None
    assert structure.to_dict()["source_extraction"]["pages"][0]["tables"][0] == table


def test_raw_extraction_evidence_supplies_identity_without_an_invented_top_level_field() -> None:
    source = _extraction(_page(1, [_block("Synthetic actual extraction shape")]))
    del source["document_version_id"]
    source["evidence"] = [
        {
            "document_version_id": str(DOCUMENT_ID),
            "content_sha256": "a" * 64,
            "page_number": 1,
            "review_state": "candidate",
        }
    ]
    structure = _build(source)
    assert structure.lineage.document_version_id == DOCUMENT_ID
    source["evidence"][0]["content_sha256"] = "c" * 64
    with pytest.raises(DocumentStructureError):
        _build(source)


def test_spatially_duplicated_table_words_are_context_only_but_unrepresented_notes_survive() -> (
    None
):
    table = _table([["Sample A", "17"]])
    blocks = [
        {"text": "Sample A", "reading_order": 0, "bbox": [11, 101, 80, 108]},
        {"text": "17", "reading_order": 1, "bbox": [111, 101, 130, 108]},
        {"text": "Synthetic footer", "reading_order": 2, "bbox": [11, 500, 80, 508]},
    ]
    structure = _build(_extraction(_page(1, blocks, tables=[table])))
    plan = plan_structure_chunks(
        structure, max_content_chars=100, max_context_chars=0, max_chunks=10
    )
    assert [chunk.text for chunk in plan.chunks] == ["Synthetic footer", "Sample A\t17"]
    assert len(structure.nodes) == 4
    assert plan.complete


def test_cancelled_page_does_not_hide_successful_sibling_and_bad_context_remains_explicit() -> None:
    source = _extraction(
        _page(1, [_block("good synthetic page")]),
        _page(2, [], status="cancelled"),
        _page(3, [], tables=[_table([["Sample A", "17"]], unit_block_orders=[999])]),
    )
    structure = _build(source)
    plan = plan_structure_chunks(
        structure, max_content_chars=100, max_context_chars=100, max_chunks=10
    )
    assert [chunk.text for chunk in plan.chunks] == ["good synthetic page"]
    assert {item.reason_code for item in plan.unprocessed} == {
        "PAGE_UNAVAILABLE",
        "CONTEXT_REFERENCE_UNRESOLVED",
    }
    assert not plan.complete


def test_header_row_identity_and_column_gaps_remain_distinct_from_data() -> None:
    table = _table([["담보", "금액"], ["Sample A", "17"]], header_rows=[0])
    table["cells"][3]["column_index"] = 2
    structure = _build(_extraction(_page(1, [], tables=[table])))
    assert [node.row_role for node in structure.nodes] == ["header", "data"]
    assert [cell.column_index for cell in structure.nodes[1].cells] == [0, 2]


@pytest.mark.parametrize("budget", [0, -1, True])
def test_nonpositive_or_boolean_content_budget_is_rejected(budget: Any) -> None:
    structure = _build(_extraction(_page(1, [_block("Synthetic text")])))
    with pytest.raises(DocumentStructureError):
        plan_structure_chunks(
            structure, max_content_chars=budget, max_context_chars=0, max_chunks=10
        )


def test_ocr_revision_and_document_evidence_must_identify_the_supplied_source() -> None:
    source = _extraction(_page(1, [_block("Synthetic text")]))
    with pytest.raises(DocumentStructureError):
        _build(source, ocr_pages=[{"page_number": 1, "status": "completed", "blocks": []}])
    with pytest.raises(DocumentStructureError):
        build_document_structure(
            source,
            extraction_id=EXTRACTION_ID,
            extraction_revision="v1",
            document_version_id=EXTRACTION_ID,
        )


def test_missing_page_inside_known_document_range_is_not_marked_complete() -> None:
    source = _extraction(
        _page(1, [_block("First synthetic page")]), _page(3, [_block("Third synthetic page")])
    )
    source["page_count"] = 4
    structure = _build(source)
    plan = plan_structure_chunks(
        structure, max_content_chars=100, max_context_chars=0, max_chunks=20
    )
    assert [item.page_number for item in plan.unprocessed] == [2, 4]
    assert {item.reason_code for item in plan.unprocessed} == {"PAGE_MISSING"}
    assert not plan.complete


@pytest.mark.parametrize("field", ["document_version_id", "content_sha256", "page_number"])
def test_ocr_page_evidence_cannot_refer_to_another_document_digest_or_page(field: str) -> None:
    source = _extraction(_page(1, [], quality={"classification": "OCR_REQUIRED"}))
    evidence = {
        "document_version_id": str(DOCUMENT_ID),
        "content_sha256": "a" * 64,
        "page_number": 1,
    }
    evidence[field] = {
        "document_version_id": str(EXTRACTION_ID),
        "content_sha256": "c" * 64,
        "page_number": 2,
    }[field]
    overlay = {
        "page_number": 1,
        "status": "completed",
        "blocks": [_block("Synthetic OCR")],
        "evidence": evidence,
    }
    with pytest.raises(DocumentStructureError):
        _build(source, ocr_pages=[overlay], ocr_revision="synthetic-ocr-v1")
