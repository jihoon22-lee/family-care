"""Synthetic layout relations never move source text, rows or monetary values."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from familycare_worker.document_layout import annotate_extraction_layout


def _block(text: str, order: int, x: int = 10, y: int = 65, width: int = 300) -> dict[str, Any]:
    return {"text": text, "reading_order": order, "bbox": [x, y, x + width, y + 10]}


def _table(
    rows: list[list[str]], *, x: int = 10, top: int = 100, widths: tuple[int, ...] = (180, 150, 160)
) -> dict[str, Any]:
    cells = []
    for row_index, row in enumerate(rows):
        left = x
        for column, text in enumerate(row):
            right = left + widths[column]
            cells.append(
                {
                    "row_index": row_index,
                    "column_index": column,
                    "text": text,
                    "bbox": [left, top + row_index * 15, right, top + (row_index + 1) * 15],
                }
            )
            left = right
    return {
        "bbox": [x, top, x + sum(widths[: len(rows[0])]), top + len(rows) * 15],
        "cells": cells,
        "review_state": "candidate",
    }


def _page(
    number: int, tables: list[dict[str, Any]], blocks: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "page_number": number,
        "width_points": 600,
        "height_points": 800,
        "blocks": blocks or [],
        "tables": tables,
        "quality": {"classification": "TEXT_SUFFICIENT"},
    }


def _source(*pages: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": "1", "content_sha256": "a" * 64, "pages": list(pages), "evidence": []}


def _metadata(result: dict[str, Any], page: int = 0, table: int = 0) -> dict[str, Any]:
    return result["pages"][page]["tables"][table]["metadata_json"]


def _strip_annotations(source: dict[str, Any]) -> dict[str, Any]:
    cleaned = deepcopy(source)
    for page in cleaned["pages"]:
        for table in page["tables"]:
            table.pop("metadata_json", None)
    return cleaned


@pytest.mark.parametrize(
    "headers,unit",
    [
        (["특약명", "가입금액", "보험기간"], "단위: 만원"),
        (["Rider name", "Sum assured", "Coverage period"], "Unit: USD"),
    ],
)
def test_explicit_headers_and_unique_nearby_unit_preserve_every_cell(
    headers: list[str], unit: str
) -> None:
    table = _table(
        [headers, ["Sample A", "17", "Sample period"], ["Sample B", "91", "Other period"]]
    )
    source = _source(_page(1, [table], [_block(unit, 0, y=80)]))
    before = deepcopy(source)
    result = annotate_extraction_layout(source)
    metadata = _metadata(result)
    assert metadata["header_rows"] == [0]
    assert metadata["unit_block_orders"] == [0]
    assert _strip_annotations(result) == source == before
    annotations = metadata["layout_annotations"]
    assert annotations["authority"] == "LAYOUT_RELATION_PROPOSAL"
    assert {relation["field"] for relation in annotations["relations"]} >= {
        "header_rows",
        "unit_block_orders",
    }
    for relation in annotations["relations"]:
        assert relation["reason_code"]
        assert relation["evidence"]
        assert all(item["source_path"].startswith("/pages/0/") for item in relation["evidence"])
    assert annotate_extraction_layout(result) == result


def test_word_level_unit_caption_is_reconstructed_from_one_geometric_line() -> None:
    table = _table([["담보명", "가입금액"], ["Sample A", "17"]])
    source = _source(
        _page(
            1,
            [table],
            [
                _block("단위:", 3, x=10, y=80, width=35),
                _block("만원", 4, x=50, y=80, width=30),
            ],
        )
    )
    result = annotate_extraction_layout(source)
    assert _metadata(result)["unit_block_orders"] == [3, 4]
    assert _strip_annotations(result) == source


def test_numeric_business_row_or_single_header_word_does_not_become_a_header() -> None:
    table = _table([["가입금액", "170", "보험기간"], ["Sample A", "91", "Sample period"]])
    source = _source(_page(1, [table]))
    result = annotate_extraction_layout(source)
    assert "header_rows" not in _metadata(result)
    assert "HEADER_ROW_UNRESOLVED" in _metadata(result)["layout_annotations"]["unresolved_codes"]
    assert _strip_annotations(result) == source


def test_two_competing_units_do_not_select_one_or_reinterpret_money() -> None:
    source = _source(
        _page(
            1,
            [_table([["담보명", "가입금액"], ["Sample A", "17"]])],
            [
                _block("단위: 만원", 0, y=65),
                _block("단위: 원", 1, y=80),
            ],
        )
    )
    metadata = _metadata(annotate_extraction_layout(source))
    assert "unit_block_orders" not in metadata
    assert "UNIT_CAPTION_AMBIGUOUS" in metadata["layout_annotations"]["unresolved_codes"]


def test_common_caption_for_two_nearby_tables_is_unresolved_for_both() -> None:
    left = _table([["담보명", "가입금액"], ["Sample A", "17"]], x=10, widths=(80, 80))
    right = _table([["담보명", "가입금액"], ["Sample B", "91"]], x=220, widths=(80, 80))
    source = _source(_page(1, [left, right], [_block("단위: 만원", 0, y=80, width=500)]))
    result = annotate_extraction_layout(source)
    for index in (0, 1):
        metadata = _metadata(result, table=index)
        assert "unit_block_orders" not in metadata
        assert "UNIT_TABLE_AMBIGUOUS" in metadata["layout_annotations"]["unresolved_codes"]


def test_matching_cell_symbol_links_only_its_nearby_footnote() -> None:
    table = _table([["담보명", "가입금액"], ["Sample A*", "17"], ["Sample B", "91"]])
    source = _source(
        _page(
            1,
            [table],
            [
                _block("* 합성 각주 조건", 4, y=160),
                _block("† 다른 각주 조건", 5, y=175),
            ],
        )
    )
    result = annotate_extraction_layout(source)
    metadata = _metadata(result)
    assert metadata["footnote_block_orders"] == [4]
    relation = next(
        item
        for item in metadata["layout_annotations"]["relations"]
        if item["field"] == "footnote_block_orders"
    )
    assert any("/cells/" in evidence["source_path"] for evidence in relation["evidence"])
    assert any("/blocks/" in evidence["source_path"] for evidence in relation["evidence"])
    assert _strip_annotations(result) == source


def test_missing_or_ambiguous_footnote_symbol_is_not_guessed() -> None:
    table = _table([["담보명", "가입금액"], ["Sample A*", "17"]])
    source = _source(
        _page(
            1,
            [table],
            [
                _block("* First synthetic condition", 1, y=145),
                _block("* Different synthetic condition", 2, y=165),
            ],
        )
    )
    metadata = _metadata(annotate_extraction_layout(source))
    assert "footnote_block_orders" not in metadata
    assert "FOOTNOTE_AMBIGUOUS" in metadata["layout_annotations"]["unresolved_codes"]


def _continued_source(
    *,
    next_headers: list[str] | None = None,
    second_title: str = "Sample Policy A",
    widths: tuple[int, ...] = (180, 150),
    marker: str = "계속",
) -> dict[str, Any]:
    headers = ["담보명", "가입금액"]
    return _source(
        _page(
            1,
            [_table([headers, ["Sample A", "17"]], widths=(180, 150))],
            [_block("계약명: Sample Policy A", 0, y=30)],
        ),
        _page(
            2,
            [_table([next_headers or headers, ["Sample B", "91"]], widths=widths)],
            [_block(f"계약명: {second_title}", 0, y=30), _block(marker, 1, y=80)],
        ),
    )


@pytest.mark.parametrize("marker", ["계속", "(계속)", "Continued", "Table continued"])
def test_next_page_continuation_requires_explicit_marker_matching_headers_and_geometry(
    marker: str,
) -> None:
    source = _continued_source(marker=marker)
    result = annotate_extraction_layout(source)
    metadata = _metadata(result, page=1)
    assert metadata["continuation_of"] == {"page_number": 1, "table_index": 0}
    relation = next(
        item
        for item in metadata["layout_annotations"]["relations"]
        if item["field"] == "continuation_of"
    )
    assert {item["page_number"] for item in relation["evidence"]} == {1, 2}
    assert _strip_annotations(result) == source


@pytest.mark.parametrize(
    "change",
    [
        {"marker": "합성 본문"},
        {"next_headers": ["담보명", "보험기간"]},
        {"second_title": "Distinct Policy B"},
        {"widths": (100, 230)},
    ],
)
def test_different_contract_columns_or_missing_marker_cannot_link_tables(
    change: dict[str, Any],
) -> None:
    source = _continued_source(**change)
    result = annotate_extraction_layout(source)
    metadata = _metadata(result, page=1)
    assert "continuation_of" not in metadata
    assert any(
        code.startswith("CONTINUATION_")
        for code in metadata["layout_annotations"]["unresolved_codes"]
    )
    assert _strip_annotations(result) == source


def test_existing_explicit_metadata_is_preserved_and_disagreement_is_recorded() -> None:
    table = _table([["담보명", "가입금액"], ["Sample A", "17"]])
    table["metadata_json"] = {"header_rows": [9], "manual_revision": "synthetic-review-v1"}
    source = _source(_page(1, [table]))
    result = annotate_extraction_layout(source)
    metadata = _metadata(result)
    assert metadata["header_rows"] == [9]
    assert metadata["manual_revision"] == "synthetic-review-v1"
    assert "EXISTING_METADATA_CONFLICT" in metadata["layout_annotations"]["unresolved_codes"]
    assert source["pages"][0]["tables"][0]["metadata_json"] == table["metadata_json"]


def test_annotation_relations_feed_ir_without_turning_context_into_new_money_rows() -> None:
    from uuid import UUID

    from familycare_worker.document_structure import build_document_structure, plan_structure_chunks

    table = _table([["담보명", "가입금액"], ["Sample A*", "17"], ["Sample B", "91"]])
    source = _source(
        _page(
            1, [table], [_block("단위: 만원", 0, y=80), _block("* Synthetic condition", 1, y=160)]
        )
    )
    annotated = annotate_extraction_layout(source)
    structure = build_document_structure(
        annotated,
        extraction_id=UUID("00000000-0000-4000-8000-000000000501"),
        document_version_id=UUID("00000000-0000-4000-8000-000000000502"),
        extraction_revision="synthetic-layout-v1",
    )
    plan = plan_structure_chunks(
        structure, max_content_chars=240, max_context_chars=240, max_chunks=20
    )
    rows = [
        node for node in structure.nodes if node.kind == "TABLE_ROW" and node.row_role == "data"
    ]
    assert [[cell.text for cell in row.cells] for row in rows] == [
        ["Sample A*", "17"],
        ["Sample B", "91"],
    ]
    for row in rows:
        chunk = next(item for item in plan.chunks if item.node_id == row.node_id)
        assert "단위: 만원" in chunk.context_text
        assert "Synthetic condition" in chunk.context_text
    assert plan.complete


@pytest.mark.parametrize("state", ["failed", "cancelled", "OCR_REQUIRED"])
def test_failed_or_unreadable_page_does_not_acquire_automatic_layout_authority(state: str) -> None:
    page = _page(
        1, [_table([["담보명", "가입금액"], ["Sample A", "17"]])], [_block("단위: 만원", 0, y=80)]
    )
    if state == "OCR_REQUIRED":
        page["quality"] = {"classification": state}
    else:
        page["status"] = state
    result = annotate_extraction_layout(_source(page))
    metadata = _metadata(result)
    assert "header_rows" not in metadata
    assert "unit_block_orders" not in metadata
    assert "SOURCE_PAGE_UNAVAILABLE" in metadata["layout_annotations"]["unresolved_codes"]


def test_two_possible_previous_tables_are_not_joined_by_position_guessing() -> None:
    source = _continued_source()
    source["pages"][0]["tables"].append(
        _table([["담보명", "가입금액"], ["Distinct row", "113"]], top=250, widths=(180, 150))
    )
    metadata = _metadata(annotate_extraction_layout(source), page=1)
    assert "continuation_of" not in metadata
    assert "CONTINUATION_TABLE_AMBIGUOUS" in metadata["layout_annotations"]["unresolved_codes"]


def test_identical_title_cannot_hide_an_explicit_different_contract_number() -> None:
    source = _continued_source()
    source["pages"][0]["blocks"].append(_block("증권번호: synthetic-policy-001", 2, y=45))
    source["pages"][1]["blocks"].append(_block("증권번호: synthetic-policy-002", 2, y=45))
    metadata = _metadata(annotate_extraction_layout(source), page=1)
    assert "continuation_of" not in metadata
    assert "CONTINUATION_CONTRACT_CONFLICT" in metadata["layout_annotations"]["unresolved_codes"]


def test_matching_identity_allows_continuation_without_copying_identity_values() -> None:
    source = _continued_source()
    for page in source["pages"]:
        page["blocks"].append(_block("증권번호: synthetic-policy-001", 2, y=45))
    metadata = _metadata(annotate_extraction_layout(source), page=1)
    assert metadata["continuation_of"] == {"page_number": 1, "table_index": 0}
    assert "synthetic-policy-001" not in str(metadata["layout_annotations"])
    assert "Sample Policy A" not in str(metadata["layout_annotations"])


def test_distant_caption_and_unmatched_footnote_are_not_assigned_to_a_table() -> None:
    source = _source(
        _page(
            1,
            [_table([["담보명", "가입금액"], ["Sample A*", "17"]])],
            [
                _block("단위: 만원", 0, y=5),
                _block("† Unrelated synthetic note", 1, y=145),
            ],
        )
    )
    metadata = _metadata(annotate_extraction_layout(source))
    assert "unit_block_orders" not in metadata
    assert "footnote_block_orders" not in metadata
    assert "FOOTNOTE_REFERENCE_UNRESOLVED" in metadata["layout_annotations"]["unresolved_codes"]


def test_duplicate_source_addresses_fail_with_a_fixed_message_without_echoing_text() -> None:
    from familycare_worker.document_layout import DocumentLayoutError

    source = _source(_page(1, [], [_block("Sensitive-shaped synthetic text", 0)]))
    source["pages"][0]["blocks"].append(_block("Other synthetic text", 0, y=85))
    with pytest.raises(DocumentLayoutError, match="^DOCUMENT_LAYOUT_INVALID$"):
        annotate_extraction_layout(source)
