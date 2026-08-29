"""Bounded, deterministic policy Evidence text loading."""

from __future__ import annotations

from uuid import UUID

import pytest
from familycare_worker.ai.evidence_loader import (
    EvidenceLoadError,
    _household_member_terms,
    _member_terms,
    _to_slices,
)

DOCUMENT_VERSION_ID = UUID("00000000-0000-4000-8000-000000000101")


def _row(page: int, *, text: str = "Sample Policy text") -> dict[str, object]:
    return {
        "evidence_id": UUID(int=page),
        "document_version_id": DOCUMENT_VERSION_ID,
        "physical_page": page,
        "document_kind": "policy",
        "evidence_text": text,
    }


def test_evidence_loader_normalizes_and_bounds_one_slice_per_page() -> None:
    rows = [_row(page, text=f"  Sample\n Policy {page}  " + "x" * 300) for page in range(1, 66)]

    slices = _to_slices(rows, expected_document_version_id=DOCUMENT_VERSION_ID)

    assert len(slices) == 64
    assert [item.page for item in slices] == list(range(1, 65))
    assert slices[0].text.startswith("Sample Policy 1 ")
    assert len(slices[0].text) == 240
    assert slices[0].bbox is None
    assert all(item.document_version_id == DOCUMENT_VERSION_ID for item in slices)


def test_member_terms_are_bounded_deduplicated_runtime_values() -> None:
    assert _member_terms(
        {
            "display_name": "Family Member A",
            "internal_alias": "family-member-a",
        }
    ) == ("Family Member A", "family-member-a")
    assert _member_terms(
        {
            "display_name": "Family Member A",
            "internal_alias": "Family Member A",
        }
    ) == ("Family Member A",)


def test_household_member_terms_cover_other_active_family_members() -> None:
    rows = (
        {
            "display_name": "Family Member A",
            "internal_alias": "family-member-a",
        },
        {
            "display_name": "Family Member B",
            "internal_alias": "family-member-b",
        },
    )

    assert _household_member_terms(rows) == (
        "Family Member A",
        "family-member-a",
        "Family Member B",
        "family-member-b",
    )


def test_household_member_terms_fail_closed_when_the_redaction_set_is_too_large() -> None:
    rows = tuple(
        {
            "display_name": f"Family Member {index}",
            "internal_alias": f"family-member-{index}",
        }
        for index in range(9)
    )

    with pytest.raises(EvidenceLoadError):
        _household_member_terms(rows)


@pytest.mark.parametrize(
    "rows",
    [
        [_row(1), _row(1)],
        [{**_row(1), "document_version_id": UUID(int=999)}],
        [{**_row(1), "document_kind": "supporting"}],
        [{**_row(1), "physical_page": 0}],
    ],
)
def test_evidence_loader_rejects_ambiguous_or_foreign_rows(
    rows: list[dict[str, object]],
) -> None:
    with pytest.raises(EvidenceLoadError):
        _to_slices(rows, expected_document_version_id=DOCUMENT_VERSION_ID)
