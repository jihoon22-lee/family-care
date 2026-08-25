"""Mapper tests for bounded Evidence disclosure."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest
from familycare_api.decisions.errors import DecisionRepositoryUnavailable
from familycare_api.decisions.evidence_repository import _detail


def _row() -> dict[str, object]:
    return {
        "id": UUID("00000000-0000-4000-8000-000000000101"),
        "document_version_id": UUID("00000000-0000-4000-8000-000000000102"),
        "physical_page": 3,
        "x0": Decimal("10.0000"),
        "y0": Decimal("20.0000"),
        "x1": Decimal("200.0000"),
        "y1": Decimal("80.0000"),
        "review_state": "AI_VERIFIED",
        "document_label": "Sample Terms",
        "clause_label": "Sample Clause 3",
        "excerpt": "  Wholly\n synthetic   excerpt.  " + ("x" * 600),
    }


def test_detail_normalizes_and_bounds_excerpt_without_paths() -> None:
    detail = _detail(_row())

    assert detail.bounded_excerpt.startswith("Wholly synthetic excerpt.")
    assert len(detail.bounded_excerpt) == 480
    assert detail.bbox == (10.0, 20.0, 200.0, 80.0)
    assert "/mnt/" not in repr(detail)


@pytest.mark.parametrize(
    "mutation",
    [
        {"physical_page": 0},
        {"review_state": "MATCH"},
        {"document_label": ""},
        {"x0": None},
    ],
)
def test_detail_rejects_malformed_persisted_values(mutation: dict[str, object]) -> None:
    row = _row()
    row.update(mutation)

    with pytest.raises(DecisionRepositoryUnavailable):
        _detail(row)
