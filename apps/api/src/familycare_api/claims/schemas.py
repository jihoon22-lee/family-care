"""Strict HTTP contracts for insurer-specific claim tracking."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from familycare_api.claims.domain import ClaimStatus

_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_AMOUNT = re.compile(r"^(0|[1-9][0-9]{0,15})(\.[0-9]{1,2})?$")
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_RECEIPT_NUMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,159}$")
_TRANSITION_METADATA = frozenset({"amount", "currency", "payment_date", "reason_code"})
ClaimApiErrorCode = Literal[
    "AUTHENTICATION_REQUIRED",
    "CLAIM_CHECKLIST_ITEM_NOT_FOUND",
    "CLAIM_INVALID",
    "CLAIM_NOT_FOUND",
    "INVALID_CLAIM_TRANSITION",
    "INVALID_REQUEST",
    "RESOURCE_LIMIT_EXCEEDED",
    "VERSION_CONFLICT",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClaimErrorResponse(StrictModel):
    error_code: ClaimApiErrorCode
    message: str
    fields: list[str] | None = None


class ClaimCreateRequest(StrictModel):
    rider_id: UUID


class ClaimUpdateRequest(StrictModel):
    expected_version: Annotated[int, Field(ge=1, le=2_147_483_647)]
    receipt_number: Annotated[str, Field(min_length=1, max_length=160)] | None = None
    claimed_amount: Annotated[str, Field(min_length=1, max_length=19)] | None = None
    currency: Annotated[str, Field(min_length=3, max_length=3)] | None = None
    outcome_reason_code: Annotated[str, Field(min_length=1, max_length=64)] | None = None

    @model_validator(mode="after")
    def validate_update(self) -> Self:
        if self.model_fields_set <= {"expected_version"}:
            raise ValueError("empty update")
        if (
            self.receipt_number is not None
            and _RECEIPT_NUMBER.fullmatch(self.receipt_number) is None
        ):
            raise ValueError("invalid receipt number")
        if self.claimed_amount is not None:
            _validate_amount(self.claimed_amount)
            if self.currency is None:
                raise ValueError("currency required with amount")
        if self.currency is not None and _CURRENCY.fullmatch(self.currency) is None:
            raise ValueError("invalid currency")
        if (
            self.outcome_reason_code is not None
            and _REASON_CODE.fullmatch(self.outcome_reason_code) is None
        ):
            raise ValueError("invalid reason code")
        return self

    def editable_values(self) -> dict[str, object]:
        values = self.model_dump(exclude={"expected_version"}, exclude_unset=True)
        if self.claimed_amount is not None:
            values["claimed_amount"] = _validate_amount(self.claimed_amount)
        return cast(dict[str, object], values)


class ClaimTransitionRequest(StrictModel):
    target_status: ClaimStatus
    expected_version: Annotated[int, Field(ge=1, le=2_147_483_647)]
    occurred_at: datetime
    metadata: dict[
        Annotated[str, Field(min_length=1, max_length=32)],
        Annotated[str, Field(min_length=1, max_length=160)],
    ] = Field(default_factory=dict, max_length=8)

    @model_validator(mode="after")
    def validate_transition_metadata(self) -> Self:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        if set(self.metadata) - _TRANSITION_METADATA:
            raise ValueError("unsupported transition metadata")
        reason = self.metadata.get("reason_code")
        if reason is not None and _REASON_CODE.fullmatch(reason) is None:
            raise ValueError("invalid reason code")
        payment_keys = {"amount", "currency", "payment_date"}
        if self.target_status in {"paid", "partially_paid"}:
            if not payment_keys <= self.metadata.keys():
                raise ValueError("payment metadata required")
            _validate_amount(self.metadata["amount"])
            if _CURRENCY.fullmatch(self.metadata["currency"]) is None:
                raise ValueError("invalid currency")
            if _DATE.fullmatch(self.metadata["payment_date"]) is None:
                raise ValueError("invalid payment date")
            try:
                date.fromisoformat(self.metadata["payment_date"])
            except ValueError:
                raise ValueError("invalid payment date") from None
        elif payment_keys & self.metadata.keys():
            raise ValueError("payment metadata is not allowed for this transition")
        return self

    def normalized_metadata(self) -> dict[str, object]:
        values: dict[str, object] = dict(self.metadata)
        if "amount" in values:
            values["amount"] = _validate_amount(cast(str, values["amount"]))
        if "payment_date" in values:
            values["payment_date"] = date.fromisoformat(cast(str, values["payment_date"]))
        return values


class ExpectedVersionRequest(StrictModel):
    expected_version: Annotated[int, Field(ge=1, le=2_147_483_647)]


class ChecklistUpdateRequest(StrictModel):
    expected_version: Annotated[int, Field(ge=1, le=2_147_483_647)]
    prepared: bool
    note_code: Annotated[str, Field(min_length=1, max_length=64)] | None = None

    @model_validator(mode="after")
    def validate_note_code(self) -> Self:
        if self.note_code is not None and _REASON_CODE.fullmatch(self.note_code) is None:
            raise ValueError("invalid note code")
        return self


class CandidateSnapshotResponse(StrictModel):
    candidate_ids: list[UUID] = Field(default_factory=list, max_length=64)
    rider_ids: list[UUID] = Field(default_factory=list, max_length=64)
    aggregate_results: list[Literal["MATCH", "NO_MATCH", "UNKNOWN"]] = Field(
        default_factory=list, max_length=64
    )


class RuleSnapshotResponse(StrictModel):
    rule_version_ids: list[UUID] = Field(default_factory=list, max_length=256)
    reason_codes: list[str] = Field(default_factory=list, max_length=256)
    evaluator_versions: list[str] = Field(default_factory=list, max_length=32)


class PolicySnapshotResponse(StrictModel):
    policy_contract_id: UUID
    rider_ids: list[UUID] = Field(default_factory=list, max_length=64)
    status_codes: list[str] = Field(default_factory=list, max_length=64)
    captured_at: datetime | None = None


class EvidenceSnapshotResponse(StrictModel):
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=512)
    content_sha256: list[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]] = Field(
        default_factory=list, max_length=512
    )


class CalculationSnapshotResponse(StrictModel):
    calculation_ids: list[UUID] = Field(default_factory=list, max_length=64)
    versions: list[int] = Field(default_factory=list, max_length=64)
    statuses: list[Literal["computed", "partial", "unknown"]] = Field(
        default_factory=list, max_length=64
    )


class ClaimSnapshotResponse(StrictModel):
    snapshot_version: Annotated[int, Field(ge=1)]
    snapshot_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    candidate: CandidateSnapshotResponse
    rules: RuleSnapshotResponse
    policy: PolicySnapshotResponse
    evidence: EvidenceSnapshotResponse
    calculation: CalculationSnapshotResponse


class ClaimChecklistItemResponse(StrictModel):
    id: UUID
    document_kind: Annotated[str, Field(min_length=1, max_length=64)]
    requirement_code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")]
    required: bool
    conditional: bool
    prepared: bool
    note_code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")] | None
    source_rule_version_id: UUID | None
    source_evidence_id: UUID | None
    version: Annotated[int, Field(ge=1)]


class ClaimStatusEventResponse(StrictModel):
    from_status: ClaimStatus | None
    to_status: ClaimStatus
    occurred_at: datetime
    reason_code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")] | None


class ClaimCaseResponse(StrictModel):
    schema_version: Literal["1"] = "1"
    id: UUID
    medical_event_id: UUID
    family_member_id: UUID
    policy_contract_id: UUID
    rider_id: UUID
    insurer_key: str
    status: ClaimStatus
    receipt_number: str | None
    submitted_at: datetime | None
    claimed_amount: Decimal | None
    paid_amount: Decimal | None
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None
    outcome_reason_code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")] | None
    version: Annotated[int, Field(ge=1)]
    deleted: bool
    allowed_transitions: list[ClaimStatus]
    snapshot: ClaimSnapshotResponse
    checklist: list[ClaimChecklistItemResponse] = Field(max_length=128)
    status_events: list[ClaimStatusEventResponse] = Field(max_length=256)

    @field_serializer("claimed_amount", "paid_amount")
    def serialize_amount(self, value: Decimal | None) -> str | None:
        return None if value is None else format(value, "f")


class ClaimCaseListResponse(StrictModel):
    schema_version: Literal["1"] = "1"
    items: list[ClaimCaseResponse] = Field(max_length=100)
    next_cursor: UUID | None = None


def _validate_amount(value: str) -> Decimal:
    if _AMOUNT.fullmatch(value) is None:
        raise ValueError("invalid decimal amount")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValueError("invalid decimal amount") from None
    exponent = cast(int, amount.as_tuple().exponent)
    if amount < 0 or exponent < -2 or len(amount.as_tuple().digits) > 18:
        raise ValueError("invalid decimal amount")
    return amount


__all__ = [
    "ChecklistUpdateRequest",
    "ClaimCaseListResponse",
    "ClaimCaseResponse",
    "ClaimCreateRequest",
    "ClaimTransitionRequest",
    "ClaimUpdateRequest",
    "ExpectedVersionRequest",
]
