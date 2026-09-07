"""Published enrollment must be unique in the complete stored native page."""

from copy import deepcopy
from typing import Any

import pytest
from familycare_api.insurance_reconciliation.source_inventory import unique_native_name_location
from familycare_api.policies.enrollment_locator import physical_enrollment_locator

from apps.api.tests.test_enrollment_locator import _refs, _source


def _expected(source: dict[str, Any]) -> dict[str, Any]:
    result = physical_enrollment_locator(source, "Sample Rider", _refs(source, "line"))
    assert result is not None
    return result


def _additional_words(source: dict[str, Any], *, gap: bool = False) -> None:
    for node in deepcopy(source["nodes"][:2]):
        node["node_id"] += "-other"
        node["bbox"][1] += 50
        node["bbox"][3] += 50
        if gap and node["text"] == "Rider":
            node["bbox"][0] += 200
            node["bbox"][2] += 200
        source["nodes"].append(node)


def test_table_line_and_raw_word_views_count_as_one_exact_location() -> None:
    source = _source()
    before = deepcopy(source)
    assert unique_native_name_location(source, 1, "Sample Rider", _expected(source))
    assert source == before


def test_supported_raw_words_alone_can_prove_the_same_native_location() -> None:
    source = _source()
    expected = _expected(source)
    source["nodes"] = source["nodes"][:3]
    assert unique_native_name_location(source, 1, "Sample Rider", expected)


@pytest.mark.parametrize("gap", [False, True])
def test_unpublished_other_name_words_cannot_disappear_from_inventory(gap: bool) -> None:
    source = _source()
    expected = _expected(source)
    _additional_words(source, gap=gap)
    assert not unique_native_name_location(source, 1, "Sample Rider", expected)


def test_geometric_word_order_finds_an_unpublished_reordered_occurrence() -> None:
    source = _source()
    expected = _expected(source)
    _additional_words(source)
    source["nodes"][-2:] = reversed(source["nodes"][-2:])
    assert not unique_native_name_location(source, 1, "Sample Rider", expected)


@pytest.mark.parametrize("kind", ["BLOCK", "TEXT_LINE", "TABLE_ROW"])
@pytest.mark.parametrize("variant", ["unresolved", "ocr", "missing_geometry"])
def test_matching_unknown_source_cannot_be_ignored(kind: str, variant: str) -> None:
    source = _source()
    expected = _expected(source)
    node = deepcopy(next(node for node in source["nodes"] if node["kind"] == kind))
    node["node_id"] = "uncertain"
    node["text"] = "Sample Rider"
    if variant == "unresolved":
        node["issue_codes"] = ["CONTEXT_REFERENCE_UNRESOLVED"]
    elif variant == "ocr":
        node["source_layer"] = "ocr"
    else:
        node["bbox"] = None
        node.pop("source_spans", None)
        node.pop("cells", None)
    source["nodes"].append(node)
    assert not unique_native_name_location(source, 1, "Sample Rider", expected)


def test_unrelated_unsupported_nodes_do_not_block_the_known_name() -> None:
    source = _source()
    expected = _expected(source)
    source["nodes"].append(
        {
            "node_id": "other",
            "kind": "BLOCK",
            "source_layer": "ocr",
            "page_number": 1,
            "text": "Other information",
            "bbox": None,
            "issue_codes": ["CONTEXT_REFERENCE_UNRESOLVED"],
        }
    )
    assert unique_native_name_location(source, 1, "Sample Rider", expected)


def test_one_unrelated_name_fragment_is_not_another_name_occurrence() -> None:
    source = _source()
    expected = _expected(source)
    source["nodes"].append(
        {
            "node_id": "fragment",
            "kind": "BLOCK",
            "source_layer": "native",
            "page_number": 1,
            "text": "Sample",
            "bbox": None,
        }
    )
    assert unique_native_name_location(source, 1, "Sample Rider", expected)


def test_another_page_does_not_make_the_bound_page_ambiguous() -> None:
    source = _source()
    expected = _expected(source)
    _additional_words(source)
    for node in source["nodes"][-2:]:
        node["page_number"] = 2
    assert unique_native_name_location(source, 1, "Sample Rider", expected)


@pytest.mark.parametrize("variant", ["page", "boolean_page", "hash", "box", "missing_name"])
def test_inventory_must_match_the_expected_locator(variant: str) -> None:
    source = _source()
    expected = _expected(source)
    if variant == "page":
        expected["physical_page"] = 2
    elif variant == "boolean_page":
        expected["physical_page"] = True
    elif variant == "hash":
        expected["content_sha256"] = "c" * 64
    elif variant == "box":
        expected["name_bbox"][0] += 0.001
    else:
        source["nodes"] = []
    assert not unique_native_name_location(source, 1, "Sample Rider", expected)


def test_a_long_raw_block_cannot_supply_interpolated_name_geometry() -> None:
    source = _source()
    expected = _expected(source)
    source["nodes"].append(
        {
            "node_id": "coarse",
            "kind": "BLOCK",
            "source_layer": "native",
            "page_number": 1,
            "text": "Sample Rider sum assured: 20만원",
            "bbox": [10, 70, 200, 80],
        }
    )
    assert not unique_native_name_location(source, 1, "Sample Rider", expected)


def test_unknown_view_cannot_cover_up_a_matching_raw_block() -> None:
    source = _source()
    expected = _expected(source)
    _additional_words(source)
    source["nodes"].append(
        {
            "node_id": "false-view",
            "kind": "TEXT_LINE",
            "source_layer": "native",
            "page_number": 1,
            "text": "Other information",
            "bbox": [10, 70, 73.625, 80],
            "source_spans": [
                {
                    "block_node_id": "name-a-other",
                    "block_start": 0,
                    "block_end": 6,
                    "line_start": 0,
                    "line_end": 5,
                }
            ],
        }
    )
    assert not unique_native_name_location(source, 1, "Sample Rider", expected)


def test_name_split_over_separate_unsupported_rows_is_not_uniqueness_proof() -> None:
    source = _source()
    expected = _expected(source)
    _additional_words(source)
    source["nodes"][-1]["bbox"][1] += 30
    source["nodes"][-1]["bbox"][3] += 30
    assert not unique_native_name_location(source, 1, "Sample Rider", expected)


def test_reordered_name_fragments_on_adjacent_wrapped_lines_remain_unresolved() -> None:
    source = _source()
    expected = _expected(source)
    _additional_words(source)
    source["nodes"][-1]["bbox"] = [10.125, 82.25, 35.625, 92.75]
    source["nodes"][-2:] = reversed(source["nodes"][-2:])
    assert not unique_native_name_location(source, 1, "Sample Rider", expected)


def test_name_fragments_in_distant_unrelated_rows_are_not_a_page_wide_gate() -> None:
    source = _source()
    expected = _expected(source)
    _additional_words(source)
    source["nodes"][-1]["bbox"][1] += 100
    source["nodes"][-1]["bbox"][3] += 100
    source["nodes"][-2:] = reversed(source["nodes"][-2:])
    assert unique_native_name_location(source, 1, "Sample Rider", expected)


def test_ocr_word_fragments_matching_the_name_are_not_native_identity() -> None:
    source = _source()
    expected = _expected(source)
    _additional_words(source)
    for node in source["nodes"][-2:]:
        node["source_layer"] = "ocr"
    assert not unique_native_name_location(source, 1, "Sample Rider", expected)
