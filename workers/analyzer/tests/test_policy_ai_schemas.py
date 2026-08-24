"""Strict provider-response and Evidence-boundary tests for policy candidates."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest
from familycare_worker.ai.policy_pipeline import run_policy_pipeline
from familycare_worker.ai.provider import EvidenceSlice

from workers.analyzer.tests.fixtures.policy_ai_responses import (
    SYNTHETIC_FOREIGN_DOCUMENT_VERSION_ID,
    SYNTHETIC_RIDER_EVIDENCE_ID,
    SYNTHETIC_UNKNOWN_EVIDENCE_ID,
    VALID_STRUCTURED,
    VALID_VERIFIED,
    FakeProvider,
    synthetic_policy_evidence,
)

STRUCTURER_MODEL = "gpt-5.6-luna"
VERIFIER_MODEL = "gpt-5.6-terra"


def _run(
    provider: FakeProvider,
    *,
    evidence: tuple[EvidenceSlice, ...] | None = None,
) -> Any:
    return run_policy_pipeline(
        evidence=evidence or synthetic_policy_evidence(),
        provider=provider,
        structurer_model=STRUCTURER_MODEL,
        verifier_model=VERIFIER_MODEL,
    )


def _candidate(result: Any) -> Any:
    candidates = result.candidates
    assert len(candidates) == 1
    return candidates[0]


def _issue_codes(candidate: Any) -> set[str]:
    return set(candidate.issue_codes)


def test_structurer_rejects_unknown_response_fields_before_verifier_call() -> None:
    """An extra provider key cannot silently enter the candidate projection."""

    response = deepcopy(VALID_STRUCTURED)
    response["unexpected"] = "synthetic-extra"
    provider = FakeProvider(structurer=response, verifier=VALID_VERIFIED)

    candidate = _candidate(_run(provider))

    assert candidate.status == "NEEDS_REVIEW"
    assert "UNSUPPORTED_STRUCTURE" in _issue_codes(candidate)
    assert [call.stage for call in provider.calls] == ["structurer"]


def test_structurer_rejects_unregistered_field_ids() -> None:
    """A model cannot create a new policy field by inventing an enum value."""

    response = deepcopy(VALID_STRUCTURED)
    fields = response["fields"]
    assert isinstance(fields, list)
    first_field = fields[0]
    assert isinstance(first_field, dict)
    first_field["field_id"] = "unregistered_field"
    provider = FakeProvider(structurer=response, verifier=VALID_VERIFIED)

    candidate = _candidate(_run(provider))

    assert candidate.status == "NEEDS_REVIEW"
    assert "UNSUPPORTED_STRUCTURE" in _issue_codes(candidate)


def test_structurer_cannot_select_a_publishable_candidate_status() -> None:
    """Only the two AI stages and deterministic validation choose AI_VERIFIED."""

    response = deepcopy(VALID_STRUCTURED)
    response["status"] = "AI_VERIFIED"
    provider = FakeProvider(structurer=response, verifier=VALID_VERIFIED)

    candidate = _candidate(_run(provider))

    assert candidate.status == "NEEDS_REVIEW"
    assert "UNSUPPORTED_STRUCTURE" in _issue_codes(candidate)


def test_missing_evidence_is_needs_review_and_never_synthesized() -> None:
    """A missing field reference remains review work instead of becoming a fact."""

    response = deepcopy(VALID_STRUCTURED)
    fields = response["fields"]
    assert isinstance(fields, list)
    first_field = fields[0]
    assert isinstance(first_field, dict)
    first_field["evidence_ids"] = []
    provider = FakeProvider(structurer=response, verifier=VALID_VERIFIED)

    candidate = _candidate(_run(provider))

    assert candidate.status == "NEEDS_REVIEW"
    assert "MISSING_EVIDENCE" in _issue_codes(candidate)


def test_evidence_from_another_document_version_is_not_accepted() -> None:
    """Evidence IDs must belong to the bounded document version being analyzed."""

    response = deepcopy(VALID_STRUCTURED)
    fields = response["fields"]
    assert isinstance(fields, list)
    first_field = fields[0]
    assert isinstance(first_field, dict)
    first_field["evidence_ids"] = [str(SYNTHETIC_UNKNOWN_EVIDENCE_ID)]
    foreign_evidence = EvidenceSlice(
        evidence_id=SYNTHETIC_UNKNOWN_EVIDENCE_ID,
        document_version_id=SYNTHETIC_FOREIGN_DOCUMENT_VERSION_ID,
        page=1,
        text="Synthetic foreign document evidence.",
        bbox=(10.0, 20.0, 110.0, 60.0),
    )
    provider = FakeProvider(structurer=response, verifier=VALID_VERIFIED)

    candidate = _candidate(
        _run(provider, evidence=synthetic_policy_evidence() + (foreign_evidence,))
    )

    assert candidate.status == "NEEDS_REVIEW"
    assert candidate.status != "AI_VERIFIED"


def test_ai_response_cannot_supply_decision_or_amount_fields() -> None:
    """AI structure output never becomes a tri-state decision or calculated amount."""

    response = deepcopy(VALID_STRUCTURED)
    response["decision"] = "MATCH"
    response["amount"] = 1000
    provider = FakeProvider(structurer=response, verifier=VALID_VERIFIED)

    candidate = _candidate(_run(provider))

    assert candidate.status == "NEEDS_REVIEW"
    assert "UNSUPPORTED_STRUCTURE" in _issue_codes(candidate)


def test_duplicate_candidate_field_ids_never_publish() -> None:
    """A later duplicate cannot shadow the first Evidence-backed value."""

    response = deepcopy(VALID_STRUCTURED)
    fields = response["fields"]
    assert isinstance(fields, list)
    fields.append(deepcopy(fields[0]))
    provider = FakeProvider(structurer=response, verifier=VALID_VERIFIED)

    candidate = _candidate(_run(provider))

    assert candidate.status == "NEEDS_REVIEW"
    assert "UNSUPPORTED_STRUCTURE" in _issue_codes(candidate)


def test_terms_only_rider_never_becomes_ai_verified() -> None:
    """Even two agreeing model stages cannot turn terms presence into enrollment."""

    evidence = tuple(replace(item, document_kind="terms") for item in synthetic_policy_evidence())
    provider = FakeProvider(structurer=VALID_STRUCTURED, verifier=VALID_VERIFIED)

    candidate = _candidate(_run(provider, evidence=evidence))

    assert candidate.status == "NEEDS_REVIEW"
    assert "TERMS_ONLY_RIDER" in _issue_codes(candidate)


def test_negative_sum_assured_is_a_review_issue() -> None:
    response = deepcopy(VALID_STRUCTURED)
    fields = response["fields"]
    assert isinstance(fields, list)
    sum_field = next(
        field
        for field in fields
        if isinstance(field, dict) and field.get("field_id") == "sum_assured"
    )
    sum_field["value"] = -1
    provider = FakeProvider(structurer=response, verifier=VALID_VERIFIED)

    candidate = _candidate(_run(provider))

    assert candidate.status == "NEEDS_REVIEW"
    assert "INVALID_UNIT" in _issue_codes(candidate)


@pytest.mark.parametrize(
    ("field_id", "value"),
    [
        ("benefit_type", "fixed_amount"),
        ("rider_status", "enrolled"),
        ("renewable", "yes"),
    ],
)
def test_invalid_ledger_semantics_never_publish(field_id: str, value: object) -> None:
    response = deepcopy(VALID_STRUCTURED)
    fields = response["fields"]
    assert isinstance(fields, list)
    existing = next(
        (
            field
            for field in fields
            if isinstance(field, dict) and field.get("field_id") == field_id
        ),
        None,
    )
    if existing is None:
        existing = {
            "field_id": field_id,
            "value": value,
            "evidence_ids": [str(SYNTHETIC_RIDER_EVIDENCE_ID)],
        }
        fields.append(existing)
    else:
        existing["value"] = value
    provider = FakeProvider(structurer=response, verifier=VALID_VERIFIED)

    candidate = _candidate(_run(provider))

    assert candidate.status == "NEEDS_REVIEW"
    assert "UNSUPPORTED_STRUCTURE" in _issue_codes(candidate)
