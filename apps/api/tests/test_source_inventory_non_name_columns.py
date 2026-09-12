"""A proven description-column reference is not another enrolled name location."""

from copy import deepcopy
from typing import Any

import pytest
from familycare_api.insurance_reconciliation.source_inventory import unique_native_name_location
from familycare_api.policies.enrollment_locator import physical_enrollment_locator

from apps.api.tests.test_enrollment_locator import _refs, _source


def _node(source: dict[str, Any], key: str) -> dict[str, Any]:
    return next(n for n in source["nodes"] if n["node_id"] == key)


def _fixture(*, split: bool = False, view: bool = False, multiline: bool = False):
    source = _source()
    expected = physical_enrollment_locator(source, "Sample Rider", _refs(source, "line"))
    assert expected is not None
    header = {
        "node_id": "reference-header",
        "kind": "TABLE_ROW",
        "source_layer": "native",
        "page_number": 1,
        "row_index": 0,
        "row_role": "header",
        "text": "담보명\t보장 설명",
        "cells": [
            {"row_index": 0, "column_index": 0, "text": "담보명", "bbox": [0, 60, 110, 75]},
            {"row_index": 0, "column_index": 1, "text": "보장 설명", "bbox": [120, 60, 300, 75]},
        ],
    }
    words = [
        ("other-a", "Other", [10, 90, 43, 100]),
        ("other-b", "Rider", [46, 90, 78, 100]),
        ("prefix", "See", [130, 90, 147, 100]),
        ("reference", "Sample Rider", [150, 90, 214, 100]),
        ("suffix", "reference", [217, 90, 280, 100]),
    ]
    if split:
        words[3:4] = [
            ("reference", "Sample", [150, 90, 182, 100]),
            ("reference-b", "Rider", [185, 90, 214, 100]),
        ]
    if multiline:
        words.append(("detail", "Other detail", [130, 105, 205, 115]))
    description = "See Sample Rider reference" + ("\nOther detail" if multiline else "")
    row = {
        "node_id": "reference-row",
        "kind": "TABLE_ROW",
        "source_layer": "native",
        "page_number": 1,
        "row_index": 1,
        "row_role": "data",
        "text": "Other Rider\t" + description,
        "bbox": [0, 80, 300, 120],
        "context_node_ids": ["reference-header"],
        "cells": [
            {"row_index": 1, "column_index": 0, "text": "Other Rider", "bbox": [0, 80, 110, 120]},
            {"row_index": 1, "column_index": 1, "text": description, "bbox": [120, 80, 300, 120]},
        ],
    }
    source["nodes"].append(header)
    for key, text, box in words:
        source["nodes"].append(
            {
                "node_id": key,
                "kind": "BLOCK",
                "source_layer": "native",
                "page_number": 1,
                "text": text,
                "bbox": box,
                "schedulable": False,
                "issue_codes": [],
            }
        )
    if view:
        pieces = [item for item in words if item[0] not in {"other-a", "other-b", "detail"}]
        offset = 0
        spans = []
        for key, text, _ in pieces:
            spans.append(
                {
                    "block_node_id": key,
                    "block_start": 0,
                    "block_end": len(text),
                    "line_start": offset,
                    "line_end": offset + len(text),
                }
            )
            offset += len(text) + 1
        source["nodes"].append(
            {
                "node_id": "reference-view",
                "kind": "TEXT_LINE",
                "source_layer": "native",
                "page_number": 1,
                "text": "See Sample Rider reference",
                "bbox": [130, 90, 280, 100],
                "source_spans": spans,
            }
        )
    source["nodes"].append(row)
    return source, expected


@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize("view", [False, True])
@pytest.mark.parametrize("multiline", [False, True])
def test_non_name_column_words_views_and_row_text_keep_one_enrollment(split, view, multiline):
    source, expected = _fixture(split=split, view=view, multiline=multiline)
    before = deepcopy(source)
    assert unique_native_name_location(source, 1, "Sample Rider", expected)
    assert source == before


@pytest.mark.parametrize(
    "fault",
    [
        "missing_header",
        "unknown_name_header",
        "ambiguous_name_columns",
        "conflicting_headers",
        "spanning_cell",
        "spanning_header",
        "overlapping_cell",
        "overlapping_header",
        "overlapping_name_cell",
        "name_word_issue",
        "overlapping_table",
        "ocr_block",
        "ocr_row",
        "missing_name_words",
        "outside_cell",
        "unrepresented_text",
        "header_column_misaligned",
        "row_issue",
        "header_issue",
        "bad_view_lineage",
    ],
)
def test_uncertain_column_or_original_source_is_never_hidden(fault):
    source, expected = _fixture(view=True)
    row, header = _node(source, "reference-row"), _node(source, "reference-header")
    if fault == "missing_header":
        row["context_node_ids"] = []
    elif fault == "unknown_name_header":
        header["cells"][0]["text"] = "Unknown column"
    elif fault == "ambiguous_name_columns":
        header["cells"][1]["text"] = "특약명"
    elif fault == "conflicting_headers":
        other = deepcopy(header)
        other["node_id"] = "conflicting-header"
        other["cells"][0]["text"], other["cells"][1]["text"] = "보장 설명", "담보명"
        row["context_node_ids"].append(other["node_id"])
        source["nodes"].append(other)
    elif fault == "spanning_cell":
        row["cells"][1]["row_span"] = 2
    elif fault == "spanning_header":
        header["cells"][1]["column_span"] = 2
    elif fault == "overlapping_cell":
        extra = deepcopy(row["cells"][1])
        extra["column_index"] = 2
        extra["text"] = "Other text"
        row["cells"].append(extra)
        row["text"] += "\tOther text"
    elif fault == "overlapping_header":
        header["cells"][0]["bbox"][2] = 200
    elif fault == "overlapping_name_cell":
        other = deepcopy(row)
        other["node_id"] = "overlapping-name-row"
        other["cells"] = [deepcopy(row["cells"][0])]
        other["text"] = "Other Rider"
        source["nodes"].append(other)
    elif fault == "name_word_issue":
        _node(source, "other-a")["issue_codes"] = ["CONTEXT_REFERENCE_UNRESOLVED"]
    elif fault == "overlapping_table":
        other = deepcopy(row)
        other["node_id"] = "overlapping-table-row"
        source["nodes"].append(other)
    elif fault == "ocr_block":
        _node(source, "reference")["source_layer"] = "ocr"
    elif fault == "ocr_row":
        row["source_layer"] = "ocr"
    elif fault == "missing_name_words":
        source["nodes"] = [n for n in source["nodes"] if n["node_id"] not in {"other-a", "other-b"}]
    elif fault == "outside_cell":
        _node(source, "reference")["bbox"] = [320, 90, 384, 100]
    elif fault == "unrepresented_text":
        row["cells"][1]["text"] = "Other reference"
        row["text"] = "Other Rider\tOther reference"
    elif fault == "header_column_misaligned":
        header["cells"][1]["bbox"] = [320, 60, 500, 75]
    elif fault == "row_issue":
        row["issue_codes"] = ["CONTEXT_REFERENCE_UNRESOLVED"]
    elif fault == "header_issue":
        header["issue_codes"] = ["CONTEXT_REFERENCE_UNRESOLVED"]
    else:
        _node(source, "reference-view")["source_spans"][0]["block_end"] -= 1
    assert not unique_native_name_location(source, 1, "Sample Rider", expected)


def test_an_unpublished_actual_name_row_still_prevents_uniqueness():
    source, expected = _fixture()
    duplicate = deepcopy(_source())
    for node in duplicate["nodes"]:
        node["node_id"] += "-unpublished"
        node["context_node_ids"] = [
            key + "-unpublished" for key in node.get("context_node_ids", ())
        ]
        for span in node.get("source_spans", ()):
            span["block_node_id"] += "-unpublished"
        for value in (node, *node.get("cells", ())):
            if value.get("bbox"):
                value["bbox"][1] += 150
                value["bbox"][3] += 150
    source["nodes"].extend(duplicate["nodes"])
    assert not unique_native_name_location(source, 1, "Sample Rider", expected)


def test_reference_columns_alone_cannot_prove_that_the_expected_enrollment_exists():
    source, expected = _fixture()
    source["nodes"] = source["nodes"][len(_source()["nodes"]) :]
    assert not unique_native_name_location(source, 1, "Sample Rider", expected)


def test_a_longer_name_cell_does_not_receive_the_non_name_column_exception():
    source, expected = _fixture()
    row = _node(source, "reference-row")
    row["cells"][0]["text"] = "Sample Rider Extension"
    row["text"] = "Sample Rider Extension\t" + row["cells"][1]["text"]
    _node(source, "other-a")["text"] = "Sample"
    _node(source, "other-b")["text"] = "Rider"
    source["nodes"].append(
        {
            "node_id": "extension",
            "kind": "BLOCK",
            "source_layer": "native",
            "page_number": 1,
            "text": "Extension",
            "bbox": [80, 90, 108, 100],
        }
    )
    assert not unique_native_name_location(source, 1, "Sample Rider", expected)
