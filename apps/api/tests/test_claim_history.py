"""ClaimHistory projection tests for future deterministic decisions."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import pytest
from familycare_api.claims.domain import ClaimHistoryRecord
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions import repository as decision_repository_module
from familycare_api.decisions.domain import ClaimHistoryFact
from familycare_api.decisions.repository import DecisionRepository

SCOPE = HouseholdScope(UUID("00000000-0000-4000-8000-000000000101"))
MEMBER_ID = UUID("00000000-0000-4000-8000-000000000201")
RIDER_ID = UUID("00000000-0000-4000-8000-000000000202")


class _HistoryReader:
    def __init__(self, facts: tuple[ClaimHistoryFact, ...]) -> None:
        self.facts = facts
        self.calls: list[tuple[HouseholdScope, UUID]] = []

    def for_family_member(
        self, scope: HouseholdScope, family_member_id: UUID
    ) -> tuple[ClaimHistoryFact, ...]:
        self.calls.append((scope, family_member_id))
        return self.facts


class _HistoryRows:
    def fetchall(self) -> list[dict[str, object]]:
        return [
            {
                "outcome": "paid",
                "counted_occurrence": True,
                "payment_date": date(2026, 8, 4),
                "rider_id": RIDER_ID,
            }
        ]


class _RepeatableReadConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[UUID, UUID]]] = []

    def execute(self, query: str, parameters: tuple[UUID, UUID]) -> _HistoryRows:
        self.calls.append((query, parameters))
        return _HistoryRows()


def test_decision_repository_consumes_public_claim_history_reader() -> None:
    facts = (
        ClaimHistoryFact("paid", True, date(2026, 8, 1), RIDER_ID),
        ClaimHistoryFact("partially_paid", True, date(2026, 8, 2), RIDER_ID),
        ClaimHistoryFact("denied", False, None, RIDER_ID),
    )
    history = _HistoryReader(facts)
    repository = DecisionRepository(
        "postgresql://synthetic:synthetic@localhost/synthetic",
        history_reader=history,
    )

    assert repository.for_family_member(SCOPE, MEMBER_ID) == facts
    assert history.calls == [(SCOPE, MEMBER_ID)]


def test_decision_transaction_reads_claim_history_on_its_existing_connection() -> None:
    connection = _RepeatableReadConnection()
    repository = DecisionRepository("postgresql://synthetic:synthetic@localhost/synthetic")
    readers = decision_repository_module._ConnectionReaders(
        repository,
        cast(Any, connection),
    )

    facts = readers.for_family_member(SCOPE, MEMBER_ID)

    assert facts == (ClaimHistoryFact("paid", True, date(2026, 8, 4), RIDER_ID),)
    assert len(connection.calls) == 1
    query, parameters = connection.calls[0]
    assert "FROM claim_history" in query
    assert "household_space_id = %s AND family_member_id = %s" in query
    assert parameters == (SCOPE.household_space_id, MEMBER_ID)


def test_denied_history_is_audit_only_not_a_counted_mismatch() -> None:
    denied = ClaimHistoryFact("denied", False, None, RIDER_ID)

    assert denied.outcome == "denied"
    assert denied.counted_occurrence is False
    assert denied.outcome != "NO_MATCH"


@pytest.mark.parametrize(
    ("outcome", "counted_occurrence"),
    [("paid", False), ("partially_paid", False), ("denied", True)],
)
def test_claim_history_rejects_counting_that_disagrees_with_outcome(
    outcome: str,
    counted_occurrence: bool,
) -> None:
    with pytest.raises(ValueError, match="counted occurrence must agree with outcome"):
        ClaimHistoryRecord(
            id=UUID("00000000-0000-4000-8000-000000000301"),
            household_space_id=SCOPE.household_space_id,
            medical_event_id=UUID("00000000-0000-4000-8000-000000000302"),
            family_member_id=MEMBER_ID,
            policy_contract_id=UUID("00000000-0000-4000-8000-000000000303"),
            rider_id=RIDER_ID,
            outcome=outcome,  # type: ignore[arg-type]
            payment_date=date(2026, 8, 3),
            counted_occurrence=counted_occurrence,
            created_at=datetime(2026, 8, 3, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("outcome", "payment_date", "amount", "currency"),
    [
        ("paid", None, Decimal("1000.00"), "KRW"),
        ("partially_paid", date(2026, 8, 3), None, "KRW"),
        ("denied", date(2026, 8, 3), None, None),
        ("denied", None, Decimal("0.00"), "KRW"),
    ],
)
def test_claim_history_rejects_payment_fields_that_disagree_with_outcome(
    outcome: str,
    payment_date: date | None,
    amount: Decimal | None,
    currency: str | None,
) -> None:
    with pytest.raises(ValueError, match="payment details must agree with outcome"):
        ClaimHistoryRecord(
            id=UUID("00000000-0000-4000-8000-000000000311"),
            household_space_id=SCOPE.household_space_id,
            medical_event_id=UUID("00000000-0000-4000-8000-000000000312"),
            family_member_id=MEMBER_ID,
            policy_contract_id=UUID("00000000-0000-4000-8000-000000000313"),
            rider_id=RIDER_ID,
            outcome=outcome,  # type: ignore[arg-type]
            payment_date=payment_date,
            counted_occurrence=outcome != "denied",
            amount=amount,
            currency=currency,
            created_at=datetime(2026, 8, 3, tzinfo=UTC),
        )
