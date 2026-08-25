"""ClaimHistory projection tests for future deterministic decisions."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import ClaimHistoryFact
from familycare_api.decisions.repository import DecisionRepository

SCOPE = HouseholdScope(UUID("00000000-0000-4000-8000-000000000101"))
MEMBER_ID = UUID("00000000-0000-4000-8000-000000000201")


class _HistoryReader:
    def __init__(self, facts: tuple[ClaimHistoryFact, ...]) -> None:
        self.facts = facts
        self.calls: list[tuple[HouseholdScope, UUID]] = []

    def for_family_member(
        self, scope: HouseholdScope, family_member_id: UUID
    ) -> tuple[ClaimHistoryFact, ...]:
        self.calls.append((scope, family_member_id))
        return self.facts


def test_decision_repository_consumes_public_claim_history_reader() -> None:
    facts = (
        ClaimHistoryFact("paid", True, date(2026, 8, 1)),
        ClaimHistoryFact("partially_paid", True, date(2026, 8, 2)),
        ClaimHistoryFact("denied", False, None),
    )
    history = _HistoryReader(facts)
    repository = DecisionRepository(
        "postgresql://synthetic:synthetic@localhost/synthetic",
        history_reader=history,
    )

    assert repository.for_family_member(SCOPE, MEMBER_ID) == facts
    assert history.calls == [(SCOPE, MEMBER_ID)]


def test_denied_history_is_audit_only_not_a_counted_mismatch() -> None:
    denied = ClaimHistoryFact("denied", False, None)

    assert denied.outcome == "denied"
    assert denied.counted_occurrence is False
    assert denied.outcome != "NO_MATCH"
