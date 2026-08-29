"""Unit proofs for deterministic candidate-to-ledger Evidence lineage."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

import pytest
from familycare_api.policies.candidate_repository import CandidateRepository

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000000101")
CANDIDATE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000201")
AGGREGATE_ID = UUID("00000000-0000-4000-8000-000000000301")
DOCUMENT_VERSION_ID = UUID("00000000-0000-4000-8000-000000000401")
POLICY_ID = UUID("00000000-0000-4000-8000-000000000501")


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None, *, rowcount: int = 0) -> None:
        self.rows = rows or []
        self.rowcount = rowcount

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class _ProjectionConnection:
    def __init__(
        self,
        *,
        candidate_kind: str,
        fields: dict[str, object],
        evidence_rows: list[dict[str, Any]],
    ) -> None:
        self.candidate_kind = candidate_kind
        self.fields = fields
        self.evidence_rows = evidence_rows
        self.inserted: dict[str, tuple[object, ...]] = {}

    def execute(
        self,
        query: str,
        params: tuple[object, ...] = (),
    ) -> _Result:
        statement = " ".join(query.split())
        if statement.startswith("SELECT * FROM analysis_candidate_versions"):
            return _Result(
                [
                    {
                        "id": CANDIDATE_VERSION_ID,
                        "status": "USER_CONFIRMED",
                        "issues": [],
                        "candidate_kind": self.candidate_kind,
                        "aggregate_id": AGGREGATE_ID,
                    }
                ]
            )
        if statement.startswith("WITH RECURSIVE lineage AS"):
            return _Result()
        if statement.startswith("SELECT field_id, value FROM analysis_candidate_fields"):
            return _Result(
                [{"field_id": field_id, "value": value} for field_id, value in self.fields.items()]
            )
        if "FROM analysis_candidate_evidence AS ce" in statement:
            return _Result(self.evidence_rows)
        if statement.startswith("INSERT INTO policy_contracts"):
            self.inserted["policy_contracts"] = params
            return _Result([{"id": AGGREGATE_ID}])
        if statement.startswith("SELECT id, source_document_version_id FROM policy_contracts"):
            return _Result([{"id": POLICY_ID, "source_document_version_id": DOCUMENT_VERSION_ID}])
        if statement.startswith("INSERT INTO riders"):
            self.inserted["riders"] = params
            return _Result([{"id": UUID("00000000-0000-4000-8000-000000000502")}])
        if statement.startswith("UPDATE analysis_candidate_versions"):
            return _Result(rowcount=1)
        raise AssertionError(f"unexpected SQL in projection unit test: {statement}")


def _evidence(field_id: str, suffix: int) -> dict[str, Any]:
    return {
        "field_id": field_id,
        "evidence_id": UUID(f"00000000-0000-4000-8000-{suffix:012d}"),
        "document_version_id": DOCUMENT_VERSION_ID,
        "document_kind": "policy",
    }


def _publish(
    *,
    candidate_kind: str,
    fields: dict[str, object],
    evidence_rows: list[dict[str, Any]],
) -> tuple[object, ...]:
    connection = _ProjectionConnection(
        candidate_kind=candidate_kind,
        fields=fields,
        evidence_rows=evidence_rows,
    )
    repository = CandidateRepository("postgresql://synthetic.invalid/familycare_test")

    assert repository._publish_projection(  # noqa: SLF001 - focused persistence unit proof
        cast(Any, connection),
        HOUSEHOLD_ID,
        CANDIDATE_VERSION_ID,
    )

    return connection.inserted[
        "policy_contracts" if candidate_kind == "policy_contract" else "riders"
    ]


@pytest.mark.parametrize(
    ("candidate_kind", "fields", "evidence_rows", "expected_source", "expected_status"),
    [
        (
            "policy_contract",
            {
                "insurer": "Sample Insurer",
                "product_name": "Sample Policy",
                "contract_start": "2026-01-01",
                "policy_status": "active",
            },
            [
                _evidence("contract_start", 601),
                _evidence("insurer", 602),
                _evidence("product_name", 603),
                _evidence("policy_status", 604),
            ],
            _evidence("product_name", 603)["evidence_id"],
            _evidence("policy_status", 604)["evidence_id"],
        ),
        (
            "rider",
            {
                "rider_name": "Sample Rider",
                "rider_key": "sample-rider",
                "benefit_type": "fixed",
                "rider_status": "active",
            },
            [
                _evidence("benefit_type", 611),
                _evidence("rider_key", 612),
                _evidence("rider_name", 613),
                _evidence("rider_status", 614),
            ],
            _evidence("rider_name", 613)["evidence_id"],
            _evidence("rider_status", 614)["evidence_id"],
        ),
    ],
)
def test_projection_uses_identifying_and_status_field_evidence(
    candidate_kind: str,
    fields: dict[str, object],
    evidence_rows: list[dict[str, Any]],
    expected_source: UUID,
    expected_status: UUID,
) -> None:
    inserted = _publish(
        candidate_kind=candidate_kind,
        fields=fields,
        evidence_rows=evidence_rows,
    )

    source_index = 3 if candidate_kind == "policy_contract" else 2
    assert inserted[source_index] == expected_source
    assert inserted[12] == expected_status


@pytest.mark.parametrize(
    ("candidate_kind", "fields", "evidence_rows"),
    [
        (
            "policy_contract",
            {
                "insurer": "Sample Insurer",
                "product_name": "Sample Policy",
                "policy_status": "active",
            },
            [_evidence("insurer", 621), _evidence("product_name", 622)],
        ),
        (
            "rider",
            {
                "rider_name": "Sample Rider",
                "rider_key": "sample-rider",
                "benefit_type": "fixed",
                "rider_status": "active",
            },
            [_evidence("rider_key", 631), _evidence("rider_name", 632)],
        ),
    ],
)
def test_projection_downgrades_unproved_non_unknown_status(
    candidate_kind: str,
    fields: dict[str, object],
    evidence_rows: list[dict[str, Any]],
) -> None:
    inserted = _publish(
        candidate_kind=candidate_kind,
        fields=fields,
        evidence_rows=evidence_rows,
    )

    assert inserted[11] == "unknown"
    assert inserted[12] is None
