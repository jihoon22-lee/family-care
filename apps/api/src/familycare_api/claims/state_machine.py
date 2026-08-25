"""Explicit ClaimCase status transitions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, cast

from familycare_api.claims.domain import ClaimStatus

CLAIM_STATUSES: Final[tuple[ClaimStatus, ...]] = (
    "preparing",
    "submitted",
    "supplementation_requested",
    "paid",
    "partially_paid",
    "denied",
    "closed",
)

ALLOWED_TRANSITIONS: Mapping[ClaimStatus, frozenset[ClaimStatus]] = {
    "preparing": frozenset({"submitted"}),
    "submitted": frozenset({"supplementation_requested", "paid", "partially_paid", "denied"}),
    "supplementation_requested": frozenset({"submitted", "paid", "partially_paid", "denied"}),
    "paid": frozenset({"closed"}),
    "partially_paid": frozenset({"closed"}),
    "denied": frozenset({"closed"}),
    "closed": frozenset(),
}


class InvalidClaimTransition(ValueError):
    """Stable, sanitized error for a disallowed or unknown transition."""

    code = "INVALID_CLAIM_TRANSITION"

    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(self.code)


def allowed_claim_transitions(status: ClaimStatus) -> frozenset[ClaimStatus]:
    if status not in ALLOWED_TRANSITIONS:
        raise InvalidClaimTransition(str(status), "")
    return ALLOWED_TRANSITIONS[status]


def transition_claim_status(current: str, target: str) -> ClaimStatus:
    if current not in ALLOWED_TRANSITIONS or target not in CLAIM_STATUSES:
        raise InvalidClaimTransition(current, target)
    source = cast(ClaimStatus, current)
    typed_target = target
    if typed_target not in ALLOWED_TRANSITIONS[source]:
        raise InvalidClaimTransition(current, target)
    return typed_target


def transition_target(source: str, target: str) -> None:
    """Validate a transition for callers that only need a guard."""

    transition_claim_status(source, target)


__all__ = [
    "ALLOWED_TRANSITIONS",
    "CLAIM_STATUSES",
    "InvalidClaimTransition",
    "allowed_claim_transitions",
    "transition_claim_status",
    "transition_target",
]
