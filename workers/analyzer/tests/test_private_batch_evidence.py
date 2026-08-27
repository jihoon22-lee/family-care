"""Synthetic page-level Evidence records for private batch persistence."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

import pytest
from familycare_worker.generated_contracts import ExtractionResult
from familycare_worker.repository import InvalidExtractionResult, _page_evidence_records

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000000001")
DOCUMENT_VERSION_ID = UUID("00000000-0000-4000-8000-000000000002")
EXTRACTION_ID = UUID("00000000-0000-4000-8000-000000000003")
CONTENT_SHA256 = "a" * 64


def _result(*page_numbers: int) -> ExtractionResult:
    return cast(
        ExtractionResult,
        cast(
            Any,
            {
                "pages": [{"page_number": page_number} for page_number in page_numbers],
            },
        ),
    )


def test_page_evidence_records_bind_every_page_to_one_private_scope() -> None:
    records = _page_evidence_records(
        _result(1, 2),
        household_space_id=HOUSEHOLD_ID,
        document_version_id=DOCUMENT_VERSION_ID,
        extraction_id=EXTRACTION_ID,
        content_sha256=CONTENT_SHA256,
        expected_page_count=2,
    )

    assert records == (
        (
            HOUSEHOLD_ID,
            DOCUMENT_VERSION_ID,
            EXTRACTION_ID,
            CONTENT_SHA256,
            1,
            "NEEDS_REVIEW",
        ),
        (
            HOUSEHOLD_ID,
            DOCUMENT_VERSION_ID,
            EXTRACTION_ID,
            CONTENT_SHA256,
            2,
            "NEEDS_REVIEW",
        ),
    )


@pytest.mark.parametrize(
    ("page_numbers", "expected_page_count"),
    [
        ((1,), 2),
        ((1, 3), 2),
        (tuple(range(1, 502)), 501),
    ],
)
def test_page_evidence_records_reject_mismatched_nonsequential_or_unbounded_pages(
    page_numbers: tuple[int, ...],
    expected_page_count: int,
) -> None:
    with pytest.raises(InvalidExtractionResult):
        _page_evidence_records(
            _result(*page_numbers),
            household_space_id=HOUSEHOLD_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            extraction_id=EXTRACTION_ID,
            content_sha256=CONTENT_SHA256,
            expected_page_count=expected_page_count,
        )
