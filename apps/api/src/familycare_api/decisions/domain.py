"""Pure domain values and reader protocols for deterministic coverage decisions.

The decision package deliberately depends on ports rather than repositories.  A
caller supplies already scoped policy, rule, Evidence, and claim-history
snapshots; this module does not discover facts or invoke an AI provider.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID

from familycare_api.clauses.rules import CoverageRuleVersion
from familycare_api.common.evidence import EvidenceRef
from familycare_api.common.scope import HouseholdScope

TriState = Literal["MATCH", "NO_MATCH", "UNKNOWN"]
FactConfirmation = Literal["user", "ai_structured", "unconfirmed", "conflicting"]
EventMode = Literal["pre_visit", "post_treatment"]
ClaimHistoryOutcome = Literal["paid", "partially_paid", "denied"]


@dataclass(frozen=True)
class FactValue:
    """One normalized value and the trust boundary that produced it."""

    value: object | None
    confirmation: FactConfirmation
    evidence_ids: tuple[UUID, ...]
    evidence_stale: bool = False

    def __post_init__(self) -> None:
        if self.confirmation not in {
            "user",
            "ai_structured",
            "unconfirmed",
            "conflicting",
        }:
            raise ValueError("unsupported fact confirmation")
        if any(not isinstance(item, UUID) or item.int == 0 for item in self.evidence_ids):
            raise ValueError("fact evidence IDs must be non-zero UUIDs")
        if not isinstance(self.evidence_stale, bool):
            raise ValueError("evidence_stale must be boolean")

    @property
    def stale(self) -> bool:
        """Compatibility alias for callers that use the shorter stale name."""

        return self.evidence_stale

    @property
    def is_confirmed(self) -> bool:
        return self.confirmation in {"user", "ai_structured"}


@dataclass(frozen=True)
class FactContext:
    """Facts grouped by the four namespaces accepted by the rule DSL."""

    medical_event: Mapping[str, FactValue]
    policy: Mapping[str, FactValue]
    rider: Mapping[str, FactValue]
    claim_history: Mapping[str, FactValue]
    as_of_date: date | None = None

    def get(self, field_path: str) -> FactValue | None:
        """Resolve a fully-qualified DSL path without dynamic attribute access."""

        namespace, separator, name = field_path.partition(".")
        if not separator or not name:
            return None
        groups: dict[str, Mapping[str, FactValue]] = {
            "MedicalEvent": self.medical_event,
            "PolicyContract": self.policy,
            "Rider": self.rider,
            "ClaimHistory": self.claim_history,
        }
        values = groups.get(namespace)
        if values is None:
            return None
        return values.get(field_path) or values.get(name)


@dataclass(frozen=True)
class MedicalEvent:
    """A structured, versioned event; it never stores document content."""

    id: UUID
    household_space_id: UUID
    family_member_id: UUID
    mode: EventMode
    event_date: date | None
    visit_date: date | None
    facts: Mapping[str, FactValue] = field(default_factory=dict)
    confirmation: Mapping[str, FactConfirmation] = field(default_factory=dict)
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_nonzero_uuid(self.id, "event id")
        _require_nonzero_uuid(self.household_space_id, "household scope")
        _require_nonzero_uuid(self.family_member_id, "family member")
        if self.mode not in {"pre_visit", "post_treatment"}:
            raise ValueError("unsupported event mode")
        if isinstance(self.version, bool) or self.version < 1:
            raise ValueError("event version must be positive")


@dataclass(frozen=True)
class ClaimHistoryFact:
    """A single historical claim occurrence supplied by the claim port."""

    outcome: ClaimHistoryOutcome
    counted_occurrence: bool
    payment_date: date | None

    def __post_init__(self) -> None:
        if self.outcome not in {"paid", "partially_paid", "denied"}:
            raise ValueError("unsupported claim history outcome")
        if not isinstance(self.counted_occurrence, bool):
            raise ValueError("counted_occurrence must be boolean")


@dataclass(frozen=True)
class PolicySnapshot:
    """The contract/rider state effective for one event date."""

    policy_id: UUID
    rider_id: UUID
    effective_status: str
    evidence_ids: tuple[UUID, ...]
    rider_type: str | None = None
    contract_start: date | None = None
    contract_end: date | None = None
    rider_coverage_start: date | None = None
    rider_coverage_end: date | None = None
    rider_status: str | None = None
    insured_amount: Decimal | None = None
    currency: str | None = None
    renewable: bool | None = None
    status_checked_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_nonzero_uuid(self.policy_id, "policy")
        _require_nonzero_uuid(self.rider_id, "rider")
        if not self.effective_status:
            raise ValueError("effective status is required")
        if any(not isinstance(item, UUID) or item.int == 0 for item in self.evidence_ids):
            raise ValueError("policy evidence IDs must be non-zero UUIDs")
        if self.insured_amount is not None and self.insured_amount < 0:
            raise ValueError("insured amount cannot be negative")


@dataclass(frozen=True)
class OperatorOutcome:
    """A tri-state operator result with lossless follow-up metadata."""

    result: TriState
    reason_code: str
    missing_fields: tuple[str, ...] = ()
    conflicting_fields: tuple[str, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if self.result not in {"MATCH", "NO_MATCH", "UNKNOWN"}:
            raise ValueError("unsupported tri-state result")
        if not self.reason_code or len(self.reason_code) > 64:
            raise ValueError("reason code must be bounded")
        if any(not isinstance(item, UUID) or item.int == 0 for item in self.evidence_ids):
            raise ValueError("outcome evidence IDs must be non-zero UUIDs")


@dataclass(frozen=True)
class RuleEvaluation:
    """One immutable evaluation of one executable CoverageRule version."""

    rider_id: UUID
    rule_version_id: UUID
    result: TriState
    required: bool
    reason_code: str
    facts: Mapping[str, FactValue] = field(default_factory=dict)
    fact_paths: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    conflicting_fields: tuple[str, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    evaluator_version: str = "decision-engine-v1"
    id: UUID | None = None

    def __post_init__(self) -> None:
        _require_nonzero_uuid(self.rider_id, "evaluation rider")
        _require_nonzero_uuid(self.rule_version_id, "evaluation rule version")
        if self.result not in {"MATCH", "NO_MATCH", "UNKNOWN"}:
            raise ValueError("unsupported tri-state result")
        if not isinstance(self.required, bool):
            raise ValueError("required must be boolean")
        if not self.reason_code or len(self.reason_code) > 64:
            raise ValueError("reason code must be bounded")
        if not self.evaluator_version:
            raise ValueError("evaluator version is required")
        if self.id is not None:
            _require_nonzero_uuid(self.id, "evaluation")
        if not self.evidence_ids and self.evidence:
            object.__setattr__(
                self,
                "evidence_ids",
                tuple(item.evidence_id for item in self.evidence),
            )

    @property
    def coverage_rule_version_id(self) -> UUID:
        """Database vocabulary alias used by the persistence layer."""

        return self.rule_version_id


@dataclass(frozen=True)
class ClaimCandidate:
    """Rider-level aggregation; this is not a claim submission or payment."""

    rider_id: UUID
    aggregate_result: TriState
    rider_type: str | None = None
    evaluations: tuple[RuleEvaluation, ...] = ()
    questions: tuple[Question, ...] = ()
    hold_reason_codes: tuple[str, ...] = ()
    required_match_count: int = 0
    required_unknown_count: int = 0
    required_no_match_count: int = 0
    id: UUID | None = None
    decision_run_id: UUID | None = None
    version: int = 1

    def __post_init__(self) -> None:
        _require_nonzero_uuid(self.rider_id, "candidate rider")
        if self.aggregate_result not in {"MATCH", "NO_MATCH", "UNKNOWN"}:
            raise ValueError("unsupported candidate result")
        for name, value in (
            ("required_match_count", self.required_match_count),
            ("required_unknown_count", self.required_unknown_count),
            ("required_no_match_count", self.required_no_match_count),
        ):
            if isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.version < 1:
            raise ValueError("candidate version must be positive")


@dataclass(frozen=True)
class Question:
    """A bounded follow-up question, never a generated medical conclusion."""

    field_path: str
    reason_code: str

    def __post_init__(self) -> None:
        if not self.field_path or "." not in self.field_path:
            raise ValueError("question field path must be qualified")
        if not self.reason_code or len(self.reason_code) > 64:
            raise ValueError("question reason code must be bounded")


@dataclass(frozen=True)
class DecisionRun:
    """Metadata for one immutable event evaluation run."""

    id: UUID
    household_space_id: UUID
    medical_event_id: UUID
    engine_version: str
    rule_set_version: str
    event_version: int
    policy_snapshot_at: datetime
    status: str
    stale: bool = False
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.id, "run"),
            (self.household_space_id, "household scope"),
            (self.medical_event_id, "medical event"),
        ):
            _require_nonzero_uuid(value, name)
        if self.event_version < 1:
            raise ValueError("event version must be positive")
        if not self.engine_version or not self.rule_set_version or not self.status:
            raise ValueError("run versions and status are required")


@dataclass(frozen=True)
class DecisionRunResult:
    run_id: UUID
    medical_event_id: UUID
    event_version: int
    engine_version: str
    rule_set_version: str
    policy_snapshot_at: datetime
    candidates: tuple[ClaimCandidate, ...]
    evaluations: tuple[RuleEvaluation, ...]
    stale: bool


class ClaimHistoryReader(Protocol):
    def for_family_member(
        self, scope: HouseholdScope, family_member_id: UUID
    ) -> tuple[ClaimHistoryFact, ...]: ...


class PolicySnapshotReader(Protocol):
    def for_event_date(
        self, scope: HouseholdScope, family_member_id: UUID, event_date: date | None
    ) -> tuple[PolicySnapshot, ...]: ...


class RuleReader(Protocol):
    def executable_for_rider(
        self, scope: HouseholdScope, rider_id: UUID
    ) -> tuple[CoverageRuleVersion, ...]: ...


class EvidenceRepository(Protocol):
    def get_many(
        self, scope: HouseholdScope, evidence_ids: tuple[UUID, ...]
    ) -> tuple[EvidenceRef, ...]: ...


@dataclass(frozen=True)
class DecisionReaders:
    policy: PolicySnapshotReader
    rules: RuleReader
    evidence: EvidenceRepository
    history: ClaimHistoryReader


class CoverageDecisionEngine(Protocol):
    def evaluate(
        self, scope: HouseholdScope, event: MedicalEvent, *, history: ClaimHistoryReader
    ) -> DecisionRunResult: ...


def _require_nonzero_uuid(value: UUID, field_name: str) -> None:
    if not isinstance(value, UUID) or value.int == 0:
        raise ValueError(f"{field_name} must be a non-zero UUID")


__all__ = [
    "ClaimCandidate",
    "ClaimHistoryFact",
    "ClaimHistoryOutcome",
    "ClaimHistoryReader",
    "CoverageDecisionEngine",
    "DecisionReaders",
    "DecisionRun",
    "DecisionRunResult",
    "EventMode",
    "EvidenceRepository",
    "FactConfirmation",
    "FactContext",
    "FactValue",
    "MedicalEvent",
    "OperatorOutcome",
    "PolicySnapshot",
    "PolicySnapshotReader",
    "RuleEvaluation",
    "RuleReader",
    "Question",
    "TriState",
]
