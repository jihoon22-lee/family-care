"""Physical source identity uses wholly synthetic native word coordinates."""

from copy import deepcopy
from typing import Any

import pytest
from familycare_api.policies.enrollment_locator import physical_enrollment_locator


def _source() -> dict[str, Any]:
    words = [
        {
            "node_id": key,
            "kind": "BLOCK",
            "source_layer": "native",
            "page_number": 1,
            "text": text,
            "bbox": box,
        }
        for key, text, box in (
            ("name-a", "Sample", [10.125, 20.25, 45.625, 30.75]),
            ("name-b", "Rider", [48.125, 20.25, 73.625, 30.75]),
            ("amount", "20만원", [110, 20.25, 145, 30.75]),
        )
    ]
    header = {
        "node_id": "header",
        "kind": "TABLE_ROW",
        "source_layer": "native",
        "page_number": 1,
        "row_index": 0,
        "row_role": "header",
        "text": "담보명\t가입금액",
        "cells": [
            {"row_index": 0, "column_index": 0, "text": "담보명"},
            {"row_index": 0, "column_index": 1, "text": "가입금액"},
        ],
    }
    row = {
        "node_id": "row",
        "kind": "TABLE_ROW",
        "source_layer": "native",
        "page_number": 1,
        "text": "Sample Rider\t20만원",
        "row_index": 1,
        "row_role": "data",
        "context_node_ids": ["header"],
        "bbox": [0, 0, 200, 200],
        "cells": [
            {
                "row_index": 1,
                "column_index": 0,
                "text": "Sample Rider",
                "bbox": [0, 18, 100, 35],
            },
            {"row_index": 1, "column_index": 1, "text": "20만원"},
        ],
    }
    line = {
        "node_id": "line",
        "kind": "TEXT_LINE",
        "source_layer": "native",
        "page_number": 1,
        "text": "Sample Rider 20만원",
        "bbox": [10.125, 20.25, 145, 30.75],
        "source_spans": [
            {
                "block_node_id": key,
                "block_start": 0,
                "block_end": len(text),
                "line_start": start,
                "line_end": start + len(text),
            }
            for key, text, start in (
                ("name-a", "Sample", 0),
                ("name-b", "Rider", 7),
                ("amount", "20만원", 13),
            )
        ],
    }
    return {
        "lineage": {"content_sha256": "a" * 64, "extraction_id": "extraction-a"},
        "nodes": [*words, header, row, line],
    }


def _refs(source: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    return [
        {
            "node_id": node["node_id"],
            "page": node["page_number"],
            "start": 0,
            "end": len(node["text"]),
            "primary": True,
            "source_role": "policy",
        }
        for key in keys
        for node in source["nodes"]
        if node["node_id"] == key
    ]


def test_native_views_resolve_to_the_same_exact_name_word_anchor() -> None:
    source = _source()
    expected = {
        "schema_version": "enrollment-physical-v1",
        "content_sha256": "a" * 64,
        "physical_page": 1,
        "name_bbox": [10.125, 20.25, 73.625, 30.75],
    }
    for keys in (("name-a", "name-b"), ("row",), ("line",), ("row", "line")):
        assert physical_enrollment_locator(source, "Sample Rider", _refs(source, *keys)) == expected


def test_extraction_node_identity_and_array_order_do_not_change_the_locator() -> None:
    source = _source()
    original = physical_enrollment_locator(source, "Sample Rider", _refs(source, "row"))
    source["lineage"]["extraction_id"] = "extraction-b"
    source["lineage"]["extraction_revision"] = "synthetic-revised"
    for node in source["nodes"]:
        node["node_id"] += "-new"
        node["context_node_ids"] = [key + "-new" for key in node.get("context_node_ids", ())]
        for span in node.get("source_spans", ()):
            span["block_node_id"] += "-new"
    source["nodes"].reverse()
    assert physical_enrollment_locator(source, "Sample Rider", _refs(source, "row-new")) == original


def test_equal_names_at_different_locations_and_content_are_distinct() -> None:
    source = _source()
    original = physical_enrollment_locator(source, "Sample Rider", _refs(source, "row"))
    shifted = deepcopy(source)
    for node in shifted["nodes"]:
        if node.get("bbox"):
            node["bbox"][1] += 50
            node["bbox"][3] += 50
        for cell in node.get("cells", ()):
            if cell.get("bbox"):
                cell["bbox"][1] += 50
                cell["bbox"][3] += 50
    assert physical_enrollment_locator(shifted, "Sample Rider", _refs(shifted, "row")) != original
    source["lineage"]["content_sha256"] = "b" * 64
    assert physical_enrollment_locator(source, "Sample Rider", _refs(source, "row")) != original


def test_key_uses_exact_coordinates_and_page_but_not_amount_or_row_index() -> None:
    source = _source()
    original = physical_enrollment_locator(source, "Sample Rider", _refs(source, "row"))
    row = source["nodes"][-2]
    row["row_index"] = 8
    for cell in row["cells"]:
        cell["row_index"] = 8
    row["cells"][1]["text"] = "30만원"
    row["text"] = "Sample Rider\t30만원"
    assert physical_enrollment_locator(source, "Sample Rider", _refs(source, "row")) == original
    source["nodes"][0]["bbox"][0] = 10.126
    changed = physical_enrollment_locator(source, "Sample Rider", _refs(source, "row"))
    assert changed is not None and changed["name_bbox"][0] == 10.126 and changed != original
    for node in source["nodes"]:
        node["page_number"] = 2
    moved = physical_enrollment_locator(source, "Sample Rider", _refs(source, "row"))
    assert moved is not None and moved["physical_page"] == 2 and moved != changed


def test_whole_name_phrase_block_has_the_same_physical_anchor() -> None:
    source = _source()
    original = physical_enrollment_locator(source, "Sample Rider", _refs(source, "row"))
    source["nodes"][0]["text"] = "Sample Rider"
    source["nodes"][0]["bbox"][2] = 73.625
    source["nodes"].pop(1)
    assert physical_enrollment_locator(source, "Sample Rider", _refs(source, "name-a")) == original
    assert physical_enrollment_locator(source, "Sample Rider", _refs(source, "row")) == original


@pytest.mark.parametrize(
    "variant",
    [
        "missing_box",
        "ocr",
        "missing_word",
        "spanning_cell",
        "ambiguous_header",
        "wrong_name",
        "word_on_other_page",
        "duplicate_word",
        "distant_words",
    ],
)
def test_table_requires_unambiguous_native_name_words(variant: str) -> None:
    source = _source()
    first, second, _, header, row, _ = source["nodes"]
    if variant == "missing_box":
        first["bbox"] = None
    elif variant == "ocr":
        row["source_layer"] = "ocr"
    elif variant == "missing_word":
        first["text"] = "Another"
    elif variant == "spanning_cell":
        row["cells"][0]["column_span"] = 2
    elif variant == "ambiguous_header":
        header["cells"][1]["text"] = "담보명"
    elif variant == "wrong_name":
        row["cells"][0]["text"] = "Another Rider"
    elif variant == "word_on_other_page":
        second["page_number"] = 2
    elif variant == "duplicate_word":
        duplicate = deepcopy(first)
        duplicate["node_id"] = "duplicate"
        source["nodes"].append(duplicate)
    else:
        second["bbox"] = [80, 20.25, 99, 30.75]
    assert physical_enrollment_locator(source, "Sample Rider", _refs(source, "row")) is None


@pytest.mark.parametrize(
    "variant",
    [
        "partial_word",
        "wrong_mapping",
        "missing_span",
        "ocr_word",
        "cross_page",
        "overlap",
        "nonspace_gap",
    ],
)
def test_line_mapping_cannot_invent_a_name_location(variant: str) -> None:
    source = _source()
    line = source["nodes"][-1]
    if variant == "partial_word":
        line["source_spans"][0]["block_end"] -= 1
    elif variant == "wrong_mapping":
        line["source_spans"][0]["block_node_id"] = "amount"
    elif variant == "missing_span":
        line["source_spans"].pop(0)
    elif variant == "ocr_word":
        source["nodes"][0]["source_layer"] = "ocr"
    elif variant == "cross_page":
        source["nodes"][0]["page_number"] = 2
    elif variant == "overlap":
        source["nodes"][1]["bbox"] = [20, 20.25, 73.625, 30.75]
    else:
        line["text"] = "SampleXRider 20만원"
    assert physical_enrollment_locator(source, "Sample Rider", _refs(source, "line")) is None


def test_partial_or_unscoped_refs_and_whole_line_boxes_are_not_word_geometry() -> None:
    source = _source()
    for update in (
        {"start": 1},
        {"end": 8},
        {"page": 2},
        {"primary": False},
        {"source_role": "terms"},
    ):
        refs = _refs(source, "row")
        refs[0].update(update)
        assert physical_enrollment_locator(source, "Sample Rider", refs) is None
    phrase = source["nodes"][0]
    phrase["text"] = "Sample Rider 가입금액: 20만원"
    assert physical_enrollment_locator(source, "Sample Rider", _refs(source, "name-a")) is None


def test_two_selected_enrollment_occurrences_are_ambiguous() -> None:
    source = _source()
    other = deepcopy(source["nodes"][-1])
    other["node_id"] = "second-line"
    for node in deepcopy(source["nodes"][:3]):
        node["node_id"] += "-second"
        node["bbox"][1] += 50
        node["bbox"][3] += 50
        source["nodes"].append(node)
    for span in other["source_spans"]:
        span["block_node_id"] += "-second"
    source["nodes"].append(other)
    assert (
        physical_enrollment_locator(source, "Sample Rider", _refs(source, "line", "second-line"))
        is None
    )


def test_shared_primary_header_is_context_for_the_named_data_row() -> None:
    source = _source()
    assert physical_enrollment_locator(
        source, "Sample Rider", _refs(source, "row", "header")
    ) == physical_enrollment_locator(source, "Sample Rider", _refs(source, "row"))
