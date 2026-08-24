"""Household-scoped Rider-Clause link validation and transitions."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, NoReturn, Protocol
from uuid import UUID

from familycare_api.clauses.domain import Clause, TermsEdition
from familycare_api.clauses.errors import (
    ClauseRepositoryUnavailable,
    RiderClauseLinkInvalid,
    RiderClauseReasonCode,
)
from familycare_api.common.evidence import EvidenceRef
from familycare_api.common.scope import HouseholdScope
from familycare_api.common.versions import InvalidVersion, require_expected_version

LinkReviewState = Literal[
    "AI_VERIFIED",
    "NEEDS_REVIEW",
    "USER_CONFIRMED",
    "rejected",
]
CandidateReviewState = Literal[
    "AI_VERIFIED",
    "NEEDS_REVIEW",
    "USER_CONFIRMED",
    "rejected",
]

_APPROVED_REVIEW_STATES = frozenset({"AI_VERIFIED", "USER_CONFIRMED"})
_REJECTION_REASONS = frozenset({"USER_REJECTED", "WRONG_CLAUSE", "WRONG_EDITION", "NOT_APPLICABLE"})


def _nonzero_uuid(value: UUID) -> bool:
    return isinstance(value, UUID) and value.int != 0


@dataclass(frozen=True)
class RiderClauseLink:
    """One reviewable connection between a subscribed Rider and Terms Clause."""

    id: UUID
    household_space_id: UUID
    rider_id: UUID
    terms_edition_id: UUID
    clause_id: UUID
    candidate_version_id: UUID
    review_state: LinkReviewState
    applicability_reason_code: str
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    evidence: tuple[EvidenceRef, ...] = ()
    rider_label: str | None = None
    clause_label: str | None = None

    def __post_init__(self) -> None:
        if not all(
            _nonzero_uuid(value)
            for value in (
                self.id,
                self.household_space_id,
                self.rider_id,
                self.terms_edition_id,
                self.clause_id,
                self.candidate_version_id,
            )
        ):
            raise ValueError("link identifiers must be non-zero UUIDs")
        if self.review_state not in {
            "AI_VERIFIED",
            "NEEDS_REVIEW",
            "USER_CONFIRMED",
            "rejected",
        }:
            raise ValueError("unsupported link review state")
        if (
            not isinstance(self.applicability_reason_code, str)
            or not self.applicability_reason_code
            or len(self.applicability_reason_code) > 64
        ):
            raise ValueError("applicability reason code must be bounded")
        try:
            require_expected_version(self.version)
        except InvalidVersion:
            raise ValueError("link version must be positive") from None
        if not all(isinstance(item, EvidenceRef) for item in self.evidence):
            raise ValueError("link evidence must contain EvidenceRef values")
        if any(
            value is not None and (not isinstance(value, str) or not value or len(value) > 160)
            for value in (self.rider_label, self.clause_label)
        ):
            raise ValueError("link labels must be bounded")


@dataclass(frozen=True)
class RiderClauseLinkValidationContext:
    """A transaction-local snapshot used by the deterministic validator."""

    link: RiderClauseLink
    policy_contract_id: UUID
    policy_household_space_id: UUID
    contract_date: date | None
    policy_insurer_key: str
    policy_product_key: str
    policy_document_version_id: UUID
    rider_policy_contract_id: UUID
    rider_document_kind: str
    rider_source_evidence: EvidenceRef
    terms_edition: TermsEdition
    clause: Clause
    candidate_kind: str
    candidate_aggregate_id: UUID | None
    candidate_review_state: CandidateReviewState
    evidence_integrity_valid: bool
    common_special_terms_conflict: bool


def _invalid(reason_code: RiderClauseReasonCode) -> NoReturn:
    raise RiderClauseLinkInvalid(reason_code)


def validate_rider_clause_link(
    scope: HouseholdScope,
    context: RiderClauseLinkValidationContext,
) -> None:
    """Validate a stored link snapshot without selecting fallback rows."""

    link = context.link
    if (
        link.household_space_id != scope.household_space_id
        or context.policy_household_space_id != scope.household_space_id
        or context.terms_edition.household_space_id != scope.household_space_id
        or context.clause.household_space_id != scope.household_space_id
    ):
        _invalid("LINK_SCOPE_MISMATCH")
    if link.deleted_at is not None or link.review_state == "rejected":
        _invalid("LINK_NOT_ACTIVE")
    if link.rider_id.int == 0 or context.rider_policy_contract_id != context.policy_contract_id:
        _invalid("RIDER_POLICY_MISMATCH")
    if (
        context.rider_document_kind != "policy"
        or context.rider_source_evidence.document_version_id != context.policy_document_version_id
        or context.rider_source_evidence.review_state not in _APPROVED_REVIEW_STATES
    ):
        _invalid("TERMS_ONLY_RIDER")
    if not context.evidence_integrity_valid:
        _invalid("LINK_EVIDENCE_INVALID")
    if context.candidate_kind != "rider_clause" or context.candidate_aggregate_id != link.id:
        _invalid("CANDIDATE_DOMAIN_MISMATCH")
    if context.candidate_review_state not in _APPROVED_REVIEW_STATES:
        _invalid("CANDIDATE_NOT_APPROVED")

    edition = context.terms_edition
    if (
        link.terms_edition_id != edition.id
        or context.policy_insurer_key != edition.insurer_key
        or context.policy_product_key != edition.product_key
    ):
        _invalid("TERMS_EDITION_MISMATCH")
    if context.contract_date is None:
        _invalid("CONTRACT_DATE_UNKNOWN")
    if (
        edition.applicability_start is not None
        and context.contract_date < edition.applicability_start
    ) or (
        edition.applicability_end is not None and context.contract_date > edition.applicability_end
    ):
        _invalid("TERMS_EDITION_NOT_APPLICABLE")
    if context.common_special_terms_conflict:
        _invalid("TERMS_SCOPE_CONFLICT")

    clause = context.clause
    if (
        link.clause_id != clause.id
        or clause.terms_edition_id != edition.id
        or clause.deleted_at is not None
        or not clause.evidence
        or any(
            evidence.document_version_id != edition.document_version_id
            or evidence.content_sha256 != edition.content_sha256
            or evidence.review_state not in _APPROVED_REVIEW_STATES
            or evidence.physical_page < clause.physical_page_start
            or evidence.physical_page > clause.physical_page_end
            for evidence in clause.evidence
        )
    ):
        _invalid("CLAUSE_DOCUMENT_MISMATCH")

    expected_evidence = {
        context.rider_source_evidence.evidence_id,
        *(item.evidence_id for item in clause.evidence),
    }
    actual_evidence = {item.evidence_id for item in link.evidence}
    if actual_evidence != expected_evidence:
        _invalid("LINK_EVIDENCE_INCOMPLETE")


class RiderClauseLinkStore(Protocol):
    """Persistence contract whose confirmation remains one DB transaction."""

    def list_for_rider(
        self, scope: HouseholdScope, rider_id: UUID
    ) -> tuple[RiderClauseLink, ...]: ...

    def confirm(
        self,
        scope: HouseholdScope,
        link_id: UUID,
        *,
        expected_version: int,
    ) -> RiderClauseLink: ...

    def reject(
        self,
        scope: HouseholdScope,
        link_id: UUID,
        *,
        expected_version: int,
        reason_code: str,
    ) -> RiderClauseLink: ...


class RiderClauseLinkService:
    """Validate public transition inputs before repository-owned transactions."""

    def __init__(self, repository: RiderClauseLinkStore) -> None:
        self.repository = repository

    @classmethod
    def from_environment(cls) -> RiderClauseLinkService:
        database_url = os.getenv("FAMILYCARE_DATABASE_URL")
        if not database_url:
            raise ClauseRepositoryUnavailable
        from familycare_api.clauses.repository import RiderClauseLinkRepository

        return cls(RiderClauseLinkRepository(database_url))

    def list_rider_clause_links(
        self, scope: HouseholdScope, rider_id: UUID
    ) -> tuple[RiderClauseLink, ...]:
        if not _nonzero_uuid(rider_id):
            _invalid("RIDER_POLICY_MISMATCH")
        return self.repository.list_for_rider(scope, rider_id)

    def confirm_rider_clause_link(
        self,
        scope: HouseholdScope,
        link_id: UUID,
        *,
        expected_version: int,
    ) -> RiderClauseLink:
        if not _nonzero_uuid(link_id):
            _invalid("LINK_NOT_ACTIVE")
        try:
            version = require_expected_version(expected_version)
        except InvalidVersion:
            _invalid("LINK_NOT_ACTIVE")
        return self.repository.confirm(
            scope,
            link_id,
            expected_version=version,
        )

    def reject_rider_clause_link(
        self,
        scope: HouseholdScope,
        link_id: UUID,
        *,
        expected_version: int,
        reason_code: str,
    ) -> RiderClauseLink:
        if reason_code not in _REJECTION_REASONS:
            _invalid("INVALID_REJECTION_REASON")
        try:
            version = require_expected_version(expected_version)
        except InvalidVersion:
            _invalid("LINK_NOT_ACTIVE")
        return self.repository.reject(
            scope,
            link_id,
            expected_version=version,
            reason_code=reason_code,
        )


__all__ = [
    "CandidateReviewState",
    "LinkReviewState",
    "RiderClauseLink",
    "RiderClauseLinkService",
    "RiderClauseLinkStore",
    "RiderClauseLinkValidationContext",
    "validate_rider_clause_link",
]
