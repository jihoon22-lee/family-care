from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from familycare_api.decisions.domain import FactContext, FactValue
from familycare_api.decisions.facts import (
    FactNormalizationError,
    normalize_fact,
    normalize_facts,
)

EVIDENCE_ID = UUID("00000000-0000-0000-0000-000000000101")


def test_normalize_fact_preserves_confirmation_and_normalizes_decimal() -> None:
    fact = normalize_fact(
        "12.50",
        confirmation="user",
        evidence_ids=(EVIDENCE_ID,),
        field="Rider.insured_amount",
    )

    assert fact == FactValue(
        value=Decimal("12.50"),
        confirmation="user",
        evidence_ids=(EVIDENCE_ID,),
    )


def test_normalize_facts_parses_dates_without_guessing() -> None:
    facts = normalize_facts(
        {"MedicalEvent.event_date": "2026-08-25", "MedicalEvent.classification": "injury"},
        confirmations={"MedicalEvent.event_date": "ai_structured"},
    )

    assert facts["MedicalEvent.event_date"].value == date(2026, 8, 25)
    assert facts["MedicalEvent.event_date"].confirmation == "ai_structured"
    assert facts["MedicalEvent.classification"].confirmation == "unconfirmed"


@pytest.mark.parametrize("confirmation", ["unconfirmed", "conflicting"])
def test_normalize_fact_does_not_upgrade_untrusted_confirmation(confirmation: str) -> None:
    fact = normalize_fact("active", confirmation=confirmation)

    assert fact.value == "active"
    assert fact.confirmation == confirmation
    assert fact.evidence_ids == ()


def test_normalize_fact_rejects_invalid_date_and_evidence() -> None:
    with pytest.raises(FactNormalizationError, match="INVALID_DATE"):
        normalize_fact("2026-02-30", confirmation="user", field="MedicalEvent.event_date")

    with pytest.raises(FactNormalizationError, match="INVALID_EVIDENCE_ID"):
        normalize_fact("active", confirmation="user", evidence_ids=("not-a-uuid",))


def test_fact_context_resolves_qualified_and_short_paths() -> None:
    context = FactContext(
        medical_event={
            "event_date": FactValue(date(2026, 8, 25), "user", ()),
        },
        policy={},
        rider={"status": FactValue("active", "user", ())},
        claim_history={},
    )

    assert context.get("MedicalEvent.event_date") == context.medical_event["event_date"]
    assert context.get("Rider.status") == context.rider["status"]
    assert context.get("Unknown.field") is None
