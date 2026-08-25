"""Pure claim status and domain value contracts."""

from __future__ import annotations

from typing import get_args

import pytest
from familycare_api.claims.domain import ClaimOutcome, ClaimStatus
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
