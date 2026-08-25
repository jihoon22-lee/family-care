"""Pure claim status and domain value contracts."""

from __future__ import annotations

from datetime import datetime
from typing import get_args
from uuid import UUID

import pytest
from familycare_api.claims.domain import ClaimCase, ClaimOutcome, ClaimStatus, ClaimStatusEvent
from familycare_api.claims.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidClaimTransition,
    allowed_claim_transitions,
    transition_claim_status,
)

ALL_STATUSES = (
    "preparing",
    "submitted",
    "supplementation_requested",
    "paid",
    "partially_paid",
    "denied",
    "closed",
)


def test_claim_status_and_outcome_are_exact_literals() -> None:
    assert get_args(ClaimStatus) == ALL_STATUSES
    assert get_args(ClaimOutcome) == ("paid", "partially_paid", "denied")
    assert tuple(ALLOWED_TRANSITIONS) == ALL_STATUSES


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("preparing", ("submitted",)),
        (
            "submitted",
            ("supplementation_requested", "paid", "partially_paid", "denied"),
        ),
        (
            "supplementation_requested",
            ("submitted", "paid", "partially_paid", "denied"),
        ),
        ("paid", ("closed",)),
        ("partially_paid", ("closed",)),
        ("denied", ("closed",)),
        ("closed", ()),
    ],
)
def test_allowed_claim_transitions_are_explicit(
    status: ClaimStatus,
    expected: tuple[ClaimStatus, ...],
) -> None:
    assert allowed_claim_transitions(status) == frozenset(expected)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("preparing", "paid"),
        ("preparing", "denied"),
        ("submitted", "preparing"),
        ("submitted", "closed"),
        ("supplementation_requested", "preparing"),
        ("paid", "submitted"),
        ("partially_paid", "denied"),
        ("denied", "paid"),
        ("closed", "preparing"),
        ("closed", "submitted"),
    ],
)
def test_invalid_claim_transitions_raise_fixed_error(
    current: ClaimStatus,
    target: ClaimStatus,
) -> None:
    with pytest.raises(InvalidClaimTransition) as error:
        transition_claim_status(current, target)

    assert error.value.code == "INVALID_CLAIM_TRANSITION"
    assert error.value.current == current
    assert error.value.target == target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("preparing", "submitted"),
        ("submitted", "supplementation_requested"),
        ("submitted", "paid"),
        ("submitted", "partially_paid"),
        ("submitted", "denied"),
        ("supplementation_requested", "submitted"),
        ("supplementation_requested", "paid"),
        ("supplementation_requested", "partially_paid"),
        ("supplementation_requested", "denied"),
        ("paid", "closed"),
        ("partially_paid", "closed"),
        ("denied", "closed"),
    ],
)
def test_valid_claim_transitions_return_target(
    current: ClaimStatus,
    target: ClaimStatus,
) -> None:
    assert transition_claim_status(current, target) == target


@pytest.mark.parametrize(
    ("current", "target"),
    [("unknown", "submitted"), ("preparing", "unknown")],
)
def test_unknown_status_values_are_rejected(current: str, target: str) -> None:
    with pytest.raises(InvalidClaimTransition) as error:
        transition_claim_status(current, target)

    assert error.value.code == "INVALID_CLAIM_TRANSITION"


def test_denied_is_an_outcome_not_a_future_mismatch() -> None:
    assert "denied" in get_args(ClaimOutcome)
    assert "NO_MATCH" not in get_args(ClaimOutcome)


def test_claim_case_rejects_receipt_text_instead_of_a_bounded_identifier() -> None:
    with pytest.raises(ValueError, match="receipt number"):
        ClaimCase(
            id=UUID("00000000-0000-4000-8000-000000000401"),
            household_space_id=UUID("00000000-0000-4000-8000-000000000402"),
            medical_event_id=UUID("00000000-0000-4000-8000-000000000403"),
            family_member_id=UUID("00000000-0000-4000-8000-000000000404"),
            policy_contract_id=UUID("00000000-0000-4000-8000-000000000405"),
            rider_id=UUID("00000000-0000-4000-8000-000000000406"),
            insurer_key="synthetic-insurer",
            receipt_number="synthetic-receipt\nmedical-note",
        )


def test_claim_status_event_requires_timezone_aware_time() -> None:
    with pytest.raises(ValueError, match="occurred at"):
        ClaimStatusEvent(
            id=UUID("00000000-0000-4000-8000-000000000411"),
            claim_case_id=UUID("00000000-0000-4000-8000-000000000412"),
            from_status="preparing",
            to_status="submitted",
            occurred_at=datetime(2026, 8, 26, 9, 0),
        )
