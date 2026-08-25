"""Claim preparation and outcome tracking domain boundary."""

from familycare_api.claims.domain import (
    ClaimCase,
    ClaimCaseSnapshot,
    ClaimChecklistItem,
    ClaimHistory,
    ClaimHistoryRecord,
    ClaimOutcome,
    ClaimStatus,
    ClaimStatusEvent,
)
from familycare_api.claims.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidClaimTransition,
    allowed_claim_transitions,
    transition_claim_status,
    transition_target,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "ClaimCase",
    "ClaimCaseSnapshot",
    "ClaimChecklistItem",
    "ClaimHistory",
    "ClaimHistoryRecord",
    "ClaimOutcome",
    "ClaimStatus",
    "ClaimStatusEvent",
    "InvalidClaimTransition",
    "allowed_claim_transitions",
    "transition_claim_status",
    "transition_target",
]
