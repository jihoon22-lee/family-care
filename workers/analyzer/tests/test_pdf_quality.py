from __future__ import annotations

import json

import pytest
from familycare_worker.pdf.coordinates import normalize_bbox
from familycare_worker.pdf.errors import PdfCorrupt
from familycare_worker.pdf.quality import classify_page_quality


def test_normalize_bbox_checks_page_bounds_and_rounds_to_three_decimals() -> None:
    assert normalize_bbox(
        10.1234,
        20.9876,
        30.5555,
        40.4444,
        page_width=100,
        page_height=200,
    ) == [10.123, 20.988, 30.555, 40.444]


def test_normalize_bbox_accepts_inclusive_page_edges() -> None:
    assert normalize_bbox(0, 0, 100, 200, page_width=100, page_height=200) == [
        0.0,
        0.0,
        100.0,
        200.0,
    ]


@pytest.mark.parametrize(
    "coordinates",
    [
        (float("nan"), 0, 1, 1),
        (0, float("inf"), 1, 1),
        (0, 0, float("-inf"), 1),
        (-0.001, 0, 1, 1),
        (0, -0.001, 1, 1),
        (0, 0, 101, 1),
        (0, 0, 1, 201),
        (2, 0, 1, 1),
        (0, 2, 1, 1),
    ],
)
def test_normalize_bbox_rejects_invalid_coordinates_with_sanitized_error(
    coordinates: tuple[float, float, float, float],
) -> None:
    with pytest.raises(PdfCorrupt) as raised:
        normalize_bbox(
            *coordinates,
            page_width=100,
            page_height=200,
        )

    assert str(raised.value) == "PDF_CORRUPT"
    assert raised.value.args == ("PDF_CORRUPT",)


def test_quality_v1_non_whitespace_boundary_is_inclusive() -> None:
    below = classify_page_quality("A" * 19, "quality-v1")
    at_boundary = classify_page_quality("A" * 20, "quality-v1")

    assert below["non_whitespace_chars"] == 19
    assert below["classification"] == "OCR_REQUIRED"
    assert at_boundary["non_whitespace_chars"] == 20
    assert at_boundary["classification"] == "TEXT_SUFFICIENT"


def test_quality_v1_alphanumeric_ratio_boundary_is_inclusive() -> None:
    below = classify_page_quality("A!?#" * 4 + "!?#!", "quality-v1")
    at_boundary = classify_page_quality("A!?#" * 5, "quality-v1")

    assert below["non_whitespace_chars"] == 20
    assert below["alphanumeric_ratio"] == 0.2
    assert below["classification"] == "OCR_REQUIRED"
    assert at_boundary["alphanumeric_ratio"] == 0.25
    assert at_boundary["classification"] == "TEXT_SUFFICIENT"


def test_quality_v1_replacement_ratio_boundary_is_inclusive() -> None:
    below = classify_page_quality("\ufffd\ufffd" + "A" * 18, "quality-v1")
    at_boundary = classify_page_quality("\ufffd" + "A" * 19, "quality-v1")

    assert below["replacement_character_ratio"] == 0.1
    assert below["classification"] == "OCR_REQUIRED"
    assert at_boundary["replacement_character_ratio"] == 0.05
    assert at_boundary["classification"] == "TEXT_SUFFICIENT"


def test_quality_v1_repeated_run_boundary_is_inclusive() -> None:
    at_boundary = classify_page_quality("A" * 20, "quality-v1")
    above = classify_page_quality("A" * 21, "quality-v1")

    assert at_boundary["maximum_repeated_character_run"] == 20
    assert at_boundary["classification"] == "TEXT_SUFFICIENT"
    assert above["maximum_repeated_character_run"] == 21
    assert above["classification"] == "OCR_REQUIRED"


def test_quality_v1_result_is_complete_and_json_serializable() -> None:
    result = classify_page_quality("Synthetic policy text A1.", "quality-v1")

    assert set(result) == {
        "rule_version",
        "classification",
        "non_whitespace_chars",
        "alphanumeric_ratio",
        "replacement_character_ratio",
        "maximum_repeated_character_run",
    }
    assert json.loads(json.dumps(result)) == result
