"""Only a complete native table name cell can establish a wrapped name anchor."""

from copy import deepcopy
from typing import Any

import pytest
from familycare_api.policies.enrollment_locator import physical_enrollment_locator

from apps.api.tests.test_enrollment_locator import _refs, _source


def wrapped_source() -> dict[str, Any]:
    source = _source()
    by_id = {n["node_id"]: n for n in source["nodes"]}
    by_id["name-b"]["bbox"] = [10.125, 34.25, 35.625, 44.75]
    header, row = by_id["header"], by_id["row"]
    header["cells"][0]["bbox"] = [0, 0, 100, 15]
    header["cells"][1]["bbox"] = [110, 0, 180, 15]
    row["bbox"] = [0, 18, 180, 50]
    row["cells"][0]["bbox"] = [0, 18, 100, 50]
    row["cells"][0]["text"] = "Sample\nRider"
    row["cells"][1]["bbox"] = [110, 18, 180, 50]
    row["text"] = "Sample\nRider\t20만원"
    by_id["line"]["bbox"] = [10.125, 20.25, 145, 44.75]
    return source


def wrapped_locator():
    return {
        "schema_version": "enrollment-physical-v1",
        "content_sha256": "a" * 64,
        "physical_page": 1,
        "name_bbox": [10.125, 20.25, 45.625, 44.75],
    }


@pytest.mark.parametrize(
    "keys",
    [("row",), ("line",), ("name-a", "name-b"), ("name-b", "name-a"), ("row", "name-a", "name-b")],
)
def test_complete_wrapped_cell_and_equivalent_native_views_share_one_anchor(keys):
    source = wrapped_source()
    before = deepcopy(source)
    assert (
        physical_enrollment_locator(source, "Sample Rider", _refs(source, *keys))
        == wrapped_locator()
    )
    assert source == before


@pytest.mark.parametrize(
    "fault",
    [
        "no_table",
        "no_header",
        "header_conflict",
        "spanning_name",
        "spanning_header",
        "ocr_word",
        "ocr_header",
        "word_issue",
        "far_line",
        "indented_line",
        "overlapping_words",
        "overlapping_cell",
        "cross_cell",
        "changed_word",
        "missing_sibling_geometry",
        "partial_cell_intersection",
    ],
)
def test_uncertain_wrap_never_falls_back_to_general_line_joining(fault):
    source = wrapped_source()
    nodes = {n["node_id"]: n for n in source["nodes"]}
    row, header = nodes["row"], nodes["header"]
    if fault == "no_table":
        source["nodes"] = [n for n in source["nodes"] if n["kind"] != "TABLE_ROW"]
    elif fault == "no_header":
        row["context_node_ids"] = []
    elif fault == "header_conflict":
        header["cells"][1]["text"] = "담보명"
    elif fault == "spanning_name":
        row["cells"][0]["row_span"] = 2
    elif fault == "spanning_header":
        header["cells"][0]["column_span"] = 2
    elif fault == "ocr_word":
        nodes["name-b"]["source_layer"] = "ocr"
    elif fault == "ocr_header":
        header["source_layer"] = "ocr"
    elif fault == "word_issue":
        nodes["name-b"]["issue_codes"] = ["CONTEXT_REFERENCE_UNRESOLVED"]
    elif fault == "far_line":
        nodes["name-b"]["bbox"][1:4:2] = [70, 80.5]
        row["cells"][0]["bbox"][3] = 85
    elif fault == "indented_line":
        nodes["name-b"]["bbox"] = [60, 34.25, 85.5, 44.75]
    elif fault == "overlapping_words":
        nodes["name-b"]["bbox"] = [20, 20.25, 45.5, 30.75]
    elif fault == "overlapping_cell":
        row["cells"][1]["bbox"][0] = 50
    elif fault == "cross_cell":
        nodes["name-b"]["bbox"] = [120, 34.25, 145.5, 44.75]
    elif fault == "changed_word":
        nodes["name-b"]["text"] = "Different"
    elif fault == "missing_sibling_geometry":
        row["cells"][1].pop("bbox")
    else:
        source["nodes"].append(
            {
                "node_id": "partial",
                "kind": "BLOCK",
                "source_layer": "native",
                "page_number": 1,
                "text": "Other",
                "bbox": [95, 20, 115, 30],
            }
        )
    assert (
        physical_enrollment_locator(source, "Sample Rider", _refs(source, "name-a", "name-b"))
        is None
    )


@pytest.mark.parametrize("keys", [("name-a",), ("name-b",), ("name-a", "name-b", "amount")])
def test_wrapped_cell_raw_equivalence_requires_its_complete_exact_block_set(keys):
    source = wrapped_source()
    assert physical_enrollment_locator(source, "Sample Rider", _refs(source, *keys)) is None


def test_a_second_name_cell_cannot_supply_a_missing_wrapped_word():
    source = wrapped_source()
    row = next(n for n in source["nodes"] if n["node_id"] == "row")
    row["cells"][0]["bbox"][3] = 32
    assert (
        physical_enrollment_locator(source, "Sample Rider", _refs(source, "name-a", "name-b"))
        is None
    )
