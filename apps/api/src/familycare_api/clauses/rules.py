"""Immutable CoverageRule versions and deterministic publication validation."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Literal, NoReturn, Protocol, cast
from uuid import UUID

from familycare_api.clauses.dsl import (
    RULE_SCHEMA_VERSION,
    RuleKind,
    RuleValidationError,
    ValidatedRule,
    validate_rule_document,
)
from familycare_api.clauses.errors import (
    ClauseRepositoryUnavailable,
    CoverageRuleInvalid,
    CoverageRuleReasonCode,
)
from familycare_api.common.evidence import EvidenceRef
from familycare_api.common.scope import HouseholdScope
from familycare_api.common.versions import InvalidVersion, require_expected_version

RuleStatus = Literal["generated", "published", "rejected"]
RuleReviewState = Literal["AI_VERIFIED", "NEEDS_REVIEW", "USER_CONFIRMED"]
CandidateRuleReviewState = Literal[
    "AI_VERIFIED",
    "NEEDS_REVIEW",
    "USER_CONFIRMED",
    "rejected",
]
LinkPublicationState = Literal[
    "AI_VERIFIED",
    "NEEDS_REVIEW",
    "USER_CONFIRMED",
    "rejected",
]

_APPROVED_STATES = frozenset({"AI_VERIFIED", "USER_CONFIRMED"})


def _nonzero_uuid(value: UUID) -> bool:
    return isinstance(value, UUID) and value.int != 0


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        frozen = {str(key): _freeze_json(item) for key, item in value.items()}
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(_freeze_json(item) for item in value)
    return value


@dataclass(frozen=True)
class CoverageRule:
    id: UUID
    household_space_id: UUID
    rider_clause_link_id: UUID
    rule_key: str
    current_status: RuleStatus
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None

    def __post_init__(self) -> None:
        if not all(
            _nonzero_uuid(item)
            for item in (self.id, self.household_space_id, self.rider_clause_link_id)
        ):
            raise ValueError("rule identifiers must be non-zero UUIDs")
        if not isinstance(self.rule_key, str) or not self.rule_key or len(self.rule_key) > 160:
            raise ValueError("rule key must be bounded")
        if self.current_status not in {"generated", "published", "rejected"}:
            raise ValueError("unsupported rule status")
        try:
            require_expected_version(self.version)
        except InvalidVersion:
            raise ValueError("rule version must be positive") from None


@dataclass(frozen=True)
class CoverageRuleVersion:
    id: UUID
    coverage_rule_id: UUID
    candidate_version_id: UUID
    version_number: int
    schema_version: str
    rule_kind: RuleKind
    required: bool
    input_field_paths: tuple[str, ...]
    rule_document: Mapping[str, object]
    result_reason_code: str
    review_state: RuleReviewState
    executable: bool
    generator_version: str
    verifier_version: str
    created_at: datetime
    published_at: datetime | None
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if not all(
            _nonzero_uuid(item)
            for item in (self.id, self.coverage_rule_id, self.candidate_version_id)
        ):
            raise ValueError("rule version identifiers must be non-zero UUIDs")
        try:
            require_expected_version(self.version_number)
        except InvalidVersion:
            raise ValueError("version number must be positive") from None
        if not isinstance(self.required, bool):
            raise ValueError("required must be boolean")
        if not self.input_field_paths or any(
            not isinstance(item, str) or not item for item in self.input_field_paths
        ):
            raise ValueError("input fields must be non-empty")
        if (
            not isinstance(self.result_reason_code, str)
            or not self.result_reason_code
            or len(self.result_reason_code) > 64
        ):
            raise ValueError("result reason code must be bounded")
        if self.review_state not in {
            "AI_VERIFIED",
            "NEEDS_REVIEW",
            "USER_CONFIRMED",
        }:
            raise ValueError("unsupported rule review state")
        if self.executable and (
            self.review_state not in _APPROVED_STATES or self.published_at is None
        ):
            raise ValueError("executable rule version must be approved and published")
        if not self.generator_version or not self.verifier_version:
            raise ValueError("generator and verifier versions are required")
        if not self.evidence or not all(isinstance(item, EvidenceRef) for item in self.evidence):
            raise ValueError("rule evidence is required")
        frozen = _freeze_json(self.rule_document)
        if not isinstance(frozen, Mapping):
            raise ValueError("rule document must be an object")
        object.__setattr__(self, "rule_document", cast(Mapping[str, object], frozen))


@dataclass(frozen=True)
class CoverageRuleVersionCollection:
    """Immutable version list paired with the aggregate concurrency token."""

    rule_id: UUID
    expected_version: int
    versions: tuple[CoverageRuleVersion, ...]

    def __post_init__(self) -> None:
        if not _nonzero_uuid(self.rule_id):
            raise ValueError("rule identifier must be a non-zero UUID")
        try:
            require_expected_version(self.expected_version)
        except InvalidVersion:
            raise ValueError("expected version must be positive") from None
        if any(version.coverage_rule_id != self.rule_id for version in self.versions):
            raise ValueError("rule version collection must contain one aggregate")


@dataclass(frozen=True)
class RulePublicationContext:
    """All rows locked and resolved inside the publisher transaction."""

    rule: CoverageRule
    candidate_version: CoverageRuleVersion
    link_id: UUID
    link_review_state: LinkPublicationState
    link_evidence_ids: frozenset[UUID]
    candidate_kind: str
    candidate_aggregate_id: UUID | None
    candidate_review_state: CandidateRuleReviewState
    candidate_is_current: bool
    candidate_evidence_ids: frozenset[UUID]
    evidence_integrity_valid: bool


def _invalid(reason_code: CoverageRuleReasonCode) -> NoReturn:
    raise CoverageRuleInvalid(reason_code)


def validate_publishable_rule(
    scope: HouseholdScope,
    context: RulePublicationContext,
) -> ValidatedRule:
    """Revalidate one stored candidate without executing it."""

    rule = context.rule
    version = context.candidate_version
    if rule.household_space_id != scope.household_space_id:
        _invalid("RULE_SCOPE_MISMATCH")
    if rule.deleted_at is not None or rule.current_status == "rejected":
        _invalid("RULE_NOT_ACTIVE")
    if version.coverage_rule_id != rule.id or context.link_id != rule.rider_clause_link_id:
        _invalid("RULE_CANDIDATE_MISMATCH")
    if context.link_review_state not in _APPROVED_STATES:
        _invalid("RIDER_CLAUSE_LINK_NOT_APPROVED")
    if (
        context.candidate_kind != "coverage_rule"
        or context.candidate_aggregate_id != rule.id
        or version.candidate_version_id.int == 0
    ):
        _invalid("RULE_CANDIDATE_MISMATCH")
    if not context.candidate_is_current:
        _invalid("RULE_CANDIDATE_STALE")
    if context.candidate_review_state not in _APPROVED_STATES:
        _invalid("RULE_CANDIDATE_NOT_APPROVED")
    if version.review_state not in _APPROVED_STATES:
        _invalid("RULE_VERSION_NOT_APPROVED")
    if version.executable or version.published_at is not None:
        _invalid("RULE_VERSION_ALREADY_PUBLISHED")
    if not context.evidence_integrity_valid:
        _invalid("RULE_EVIDENCE_INVALID")

    version_evidence_ids = frozenset(item.evidence_id for item in version.evidence)
    if not version_evidence_ids or not (
        version_evidence_ids == context.link_evidence_ids == context.candidate_evidence_ids
    ):
        _invalid("RULE_EVIDENCE_MISMATCH")
    try:
        validated = validate_rule_document(
            version.rule_document,
            version_evidence_ids,
        )
    except RuleValidationError:
        _invalid("RULE_DSL_INVALID")
    if (
        version.schema_version != RULE_SCHEMA_VERSION
        or validated.schema_version != version.schema_version
        or validated.rule_kind != version.rule_kind
        or validated.required is not version.required
        or validated.input_field_paths != version.input_field_paths
        or validated.result_reason_code != version.result_reason_code
        or frozenset(str(item) for item in validated.evidence_ids)
        != frozenset(str(item) for item in version_evidence_ids)
    ):
        _invalid("RULE_DSL_INVALID")
    return validated


class CoverageRuleStore(Protocol):
    def list_versions(
        self,
        scope: HouseholdScope,
        rule_id: UUID,
    ) -> CoverageRuleVersionCollection: ...

    def publish(
        self,
        scope: HouseholdScope,
        rule_id: UUID,
        version_id: UUID,
        *,
        expected_version: int,
    ) -> CoverageRuleVersion: ...


class CoverageRuleService:
    def __init__(self, repository: CoverageRuleStore) -> None:
        self.repository = repository

    @classmethod
    def from_environment(cls) -> CoverageRuleService:
        database_url = os.getenv("FAMILYCARE_DATABASE_URL")
        if not database_url:
            raise ClauseRepositoryUnavailable
        from familycare_api.clauses.repository import CoverageRuleRepository

        return cls(CoverageRuleRepository(database_url))

    def list_rule_versions(
        self,
        scope: HouseholdScope,
        rule_id: UUID,
    ) -> CoverageRuleVersionCollection:
        if not _nonzero_uuid(rule_id):
            _invalid("RULE_NOT_ACTIVE")
        return self.repository.list_versions(scope, rule_id)

    def publish_coverage_rule(
        self,
        scope: HouseholdScope,
        rule_id: UUID,
        version_id: UUID,
        *,
        expected_version: int,
    ) -> CoverageRuleVersion:
        if not _nonzero_uuid(rule_id) or not _nonzero_uuid(version_id):
            _invalid("RULE_NOT_ACTIVE")
        try:
            version = require_expected_version(expected_version)
        except InvalidVersion:
            _invalid("RULE_NOT_ACTIVE")
        return self.repository.publish(
            scope,
            rule_id,
            version_id,
            expected_version=version,
        )


__all__ = [
    "CandidateRuleReviewState",
    "CoverageRule",
    "CoverageRuleService",
    "CoverageRuleStore",
    "CoverageRuleVersion",
    "CoverageRuleVersionCollection",
    "LinkPublicationState",
    "RulePublicationContext",
    "RuleReviewState",
    "RuleStatus",
    "validate_publishable_rule",
]
