"""Persistence mapper tests for immutable decision Evidence snapshots."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest
from familycare_api.common.evidence import EvidenceRef
from familycare_api.decisions.errors import DecisionRepositoryUnavailable
from familycare_api.decisions.repository import _evidence_snapshot_values, _rule_evaluation

EVALUATION_ID = UUID("00000000-0000-4000-8000-000000000101")
RIDER_ID = UUID("00000000-0000-4000-8000-000000000102")
RULE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000103")
EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000104")
DOCUMENT_VERSION_ID = UUID("00000000-0000-4000-8000-000000000105")
EXTRACTION_ID = UUID("00000000-0000-4000-8000-000000000106")


def _evidence() -> EvidenceRef:
    return EvidenceRef(
        evidence_id=EVIDENCE_ID,
        document_version_id=DOCUMENT_VERSION_ID,
        extraction_id=EXTRACTION_ID,
        content_sha256="a" * 64,
        physical_page=1,
        bbox=(Decimal("1.0000"), Decimal("2.0000"), Decimal("3.0000"), Decimal("4.0000")),
        review_state="USER_CONFIRMED",
    )


def _row(snapshot: object) -> dict[str, object]:
    return {
        "id": EVALUATION_ID,
        "rider_id": RIDER_ID,
        "coverage_rule_version_id": RULE_VERSION_ID,
        "result": "UNKNOWN",
        "required": True,
        "reason_code": "EVIDENCE_UNAVAILABLE",
        "facts_json": {},
        "evidence_snapshot_json": snapshot,
        "missing_fields_json": [],
        "conflicting_fields_json": [],
        "evaluator_version": "decision-engine-v1",
    }


def test_evidence_snapshot_round_trip_preserves_exact_metadata() -> None:
    values = _evidence_snapshot_values(
        [
            {
                "evidence_id": str(EVIDENCE_ID),
                "document_version_id": str(DOCUMENT_VERSION_ID),
                "extraction_id": str(EXTRACTION_ID),
                "content_sha256": "a" * 64,
                "physical_page": 1,
                "bbox": ["1.0000", "2.0000", "3.0000", "4.0000"],
                "review_state": "USER_CONFIRMED",
            }
        ]
    )

    assert values == (_evidence(),)


def test_empty_snapshot_does_not_fall_back_to_mutable_evidence_rows() -> None:
    evaluation = _rule_evaluation(_row([]))

    assert evaluation.evidence == ()
    assert evaluation.evidence_ids == ()


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        [{}],
        [{"bbox": "not-a-bounding-box"}],
        [
            {
                "evidence_id": str(EVIDENCE_ID),
                "document_version_id": str(DOCUMENT_VERSION_ID),
                "extraction_id": str(EXTRACTION_ID),
                "content_sha256": "not-a-sha256",
                "physical_page": 1,
                "bbox": None,
                "review_state": "USER_CONFIRMED",
            }
        ],
    ],
)
def test_malformed_persisted_snapshot_fails_as_repository_unavailable(snapshot: object) -> None:
    with pytest.raises(DecisionRepositoryUnavailable):
        _evidence_snapshot_values(snapshot)
