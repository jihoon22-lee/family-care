"""HTTP boundary tests for manual receipt lines and benefit calculations."""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.decisions.calculations import Money, ReceiptLine
from familycare_api.decisions.router import get_calculation_service, router
from familycare_api.errors import install_error_handlers
from familycare_api.policies.errors import VersionConflict
from fastapi import FastAPI
from fastapi.testclient import TestClient

SCOPE = HouseholdScope(UUID("00000000-0000-4000-8000-000000000101"))
EVENT_ID = UUID("00000000-0000-4000-8000-000000000201")
LINE_ID = UUID("00000000-0000-4000-8000-000000000301")
CANDIDATE_ID = UUID("00000000-0000-4000-8000-000000000401")
RULE_ID = UUID("00000000-0000-4000-8000-000000000501")
CALCULATION_ID = UUID("00000000-0000-4000-8000-000000000601")


def _line(*, amount: str = "12500.50", version: int = 1) -> ReceiptLine:
    return ReceiptLine(
        line_id=LINE_ID,
        category="outpatient",
        coverage_category="covered",
        amount=Money(Decimal(amount), "KRW"),
        confirmation_level="user",
        note_code="USER_ENTERED",
        version=version,
    )


def _calculation() -> dict[str, object]:
    return {
        "schema_version": "1",
        "kind": "indemnity",
        "status": "partial",
        "calculation_id": CALCULATION_ID,
        "claim_candidate_id": CANDIDATE_ID,
        "rule_version_id": RULE_ID,
        "currency": "KRW",
        "confirmed": {"amount": Decimal("10000"), "currency": "KRW"},
        "additional": {"amount": Decimal("2500.50"), "currency": "KRW"},
        "excluded": {"amount": Decimal("0"), "currency": "KRW"},
        "deductible": {"amount": Decimal("1000"), "currency": "KRW"},
        "applied_rate": Decimal("0.8"),
        "applied_limit": {"amount": Decimal("100000"), "currency": "KRW"},
        "rounding_rule": "half_up",
        "engine_version": "benefit-calculation-v1",
        "version": 1,
        "created_at": "2026-08-25T09:00:00Z",
        "steps": [
            {
                "step_number": 1,
                "operation": "subtract",
                "input_amount": {"amount": Decimal("13750.50"), "currency": "KRW"},
                "output_amount": {"amount": Decimal("12750.50"), "currency": "KRW"},
                "rounding_rule": None,
                "reason_code": "INDEMNITY_DEDUCTIBLE",
            }
        ],
        "hold_reason_codes": ["ADDITIONAL_RECEIPT_REVIEW_REQUIRED"],
        "excluded_reason_codes": [],
        "evidence_ids": [RULE_ID],
    }


class _FakeCalculationService:
    def __init__(self) -> None:
        self.version = 1
        self.created: list[tuple[UUID, object]] = []
        self.updated: list[tuple[UUID, UUID, object]] = []
        self.deleted: list[tuple[UUID, UUID, int]] = []

    def create_receipt_line(self, event_id: UUID, request: object) -> ReceiptLine:
        self.created.append((event_id, request))
        return _line()

    def list_receipt_lines(self, event_id: UUID) -> tuple[ReceiptLine, ...]:
        assert event_id == EVENT_ID
        return (_line(),)

    def update_receipt_line(self, event_id: UUID, line_id: UUID, request: object) -> ReceiptLine:
        data = request.model_dump(exclude_unset=True)  # type: ignore[attr-defined]
        if data["expected_version"] != self.version:
            raise VersionConflict
        self.updated.append((event_id, line_id, request))
        self.version += 1
        return _line(amount=str(data.get("amount", "12500.50")), version=self.version)

    def delete_receipt_line(self, event_id: UUID, line_id: UUID, *, expected_version: int) -> None:
        if expected_version != self.version:
            raise VersionConflict
        self.deleted.append((event_id, line_id, expected_version))
        self.version += 1

    def get_calculations(self, event_id: UUID) -> tuple[dict[str, object], ...]:
        assert event_id == EVENT_ID
        return (_calculation(),)


@pytest.fixture()
def service() -> _FakeCalculationService:
    return _FakeCalculationService()


@pytest.fixture()
def client(service: _FakeCalculationService) -> Iterator[TestClient]:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_calculation_service] = lambda: service
    app.dependency_overrides[resolve_household_scope] = lambda: SCOPE
    with TestClient(app) as test_client:
        yield test_client


def _receipt_request() -> dict[str, object]:
    return {
        "category": "outpatient",
        "coverage_category": "covered",
        "amount": "12500.50",
        "currency": "KRW",
        "confirmation_level": "user",
        "note_code": "USER_ENTERED",
    }


def _assert_no_store(response: Any) -> None:
    assert response.headers.get("cache-control") == "no-store"


def test_receipt_create_round_trips_decimal_string_without_scope(client: TestClient) -> None:
    response = client.post(
        f"/api/v1/medical-events/{EVENT_ID}/receipt-lines",
        json=_receipt_request(),
    )

    assert response.status_code == 201
    assert response.json()["amount"] == "12500.50"
    assert response.json()["currency"] == "KRW"
    assert "household_space_id" not in response.json()
    _assert_no_store(response)


def test_receipt_list_returns_reopenable_versioned_metadata(client: TestClient) -> None:
    response = client.get(f"/api/v1/medical-events/{EVENT_ID}/receipt-lines")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "1",
        "receipt_lines": [
            {
                "id": str(LINE_ID),
                "category": "outpatient",
                "coverage_category": "covered",
                "amount": "12500.50",
                "currency": "KRW",
                "confirmation_level": "user",
                "note_code": "USER_ENTERED",
                "version": 1,
                "deleted": False,
            }
        ],
    }
    _assert_no_store(response)


@pytest.mark.parametrize(
    "field",
    ["household_space_id", "confirmed_amount", "applied_rate", "rule_version_id", "file_path"],
)
def test_receipt_create_rejects_client_scope_calculation_authority_and_files(
    client: TestClient,
    field: str,
) -> None:
    response = client.post(
        f"/api/v1/medical-events/{EVENT_ID}/receipt-lines",
        json={**_receipt_request(), field: "SYNTHETIC_PRIVATE_VALUE"},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_REQUEST"
    assert "SYNTHETIC_PRIVATE_VALUE" not in response.text
    _assert_no_store(response)


def test_receipt_update_and_delete_require_current_version(
    client: TestClient,
    service: _FakeCalculationService,
) -> None:
    updated = client.patch(
        f"/api/v1/medical-events/{EVENT_ID}/receipt-lines/{LINE_ID}",
        json={"expected_version": 1, "amount": "13000.00"},
    )
    stale = client.patch(
        f"/api/v1/medical-events/{EVENT_ID}/receipt-lines/{LINE_ID}",
        json={"expected_version": 1, "amount": "14000.00"},
    )
    deleted = client.request(
        "DELETE",
        f"/api/v1/medical-events/{EVENT_ID}/receipt-lines/{LINE_ID}",
        json={"expected_version": 2},
    )

    assert updated.status_code == 200
    assert updated.json()["amount"] == "13000.00"
    assert stale.status_code == 409
    assert stale.json() == {"error_code": "VERSION_CONFLICT", "message": "version conflict"}
    assert deleted.status_code == 204
    assert service.deleted == [(EVENT_ID, LINE_ID, 2)]
    _assert_no_store(updated)
    _assert_no_store(stale)
    _assert_no_store(deleted)


def test_get_calculations_returns_partial_trace_with_decimal_strings(client: TestClient) -> None:
    response = client.get(f"/api/v1/medical-events/{EVENT_ID}/calculations")

    assert response.status_code == 200
    body = response.json()
    calculation = body["calculations"][0]
    assert calculation["status"] == "partial"
    assert calculation["confirmed"] == {"amount": "10000", "currency": "KRW"}
    assert calculation["additional"] == {"amount": "2500.50", "currency": "KRW"}
    assert calculation["applied_rate"] == "0.8"
    assert calculation["steps"][0]["reason_code"] == "INDEMNITY_DEDUCTIBLE"
    assert "file_path" not in response.text
    _assert_no_store(response)
