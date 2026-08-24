"""Decision-table tests for scoped Rider-Clause link validation."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from familycare_api.clauses.domain import Clause, TermsEdition
from familycare_api.clauses.errors import (
    ClauseVersionConflict,
    RiderClauseLinkInvalid,
)
from familycare_api.clauses.links import (
    RiderClauseLink,
    RiderClauseLinkService,
    RiderClauseLinkValidationContext,
    validate_rider_clause_link,
)
from familycare_api.common.evidence import EvidenceRef
from familycare_api.common.scope import HouseholdScope

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _id(value: int) -> UUID:
    return UUID(int=value)


def _evidence(
    value: int,
    *,
    document_version_id: UUID,
    content_sha256: str,
    page: int,
) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=_id(value),
        document_version_id=document_version_id,
        extraction_id=_id(value + 100),
        content_sha256=content_sha256,
        physical_page=page,
        bbox=(Decimal("1"), Decimal("2"), Decimal("30"), Decimal("40")),
        review_state="USER_CONFIRMED",
    )


def _context() -> RiderClauseLinkValidationContext:
    household_id = _id(1)
    policy_document_id = _id(20)
    terms_document_id = _id(30)
    policy_evidence = _evidence(
        40,
        document_version_id=policy_document_id,
        content_sha256="a" * 64,
        page=1,
    )
    terms_evidence = _evidence(
        50,
        document_version_id=terms_document_id,
        content_sha256="b" * 64,
        page=2,
    )
    link = RiderClauseLink(
        id=_id(60),
        household_space_id=household_id,
        rider_id=_id(70),
        terms_edition_id=_id(80),
        clause_id=_id(90),
        candidate_version_id=_id(100),
        review_state="AI_VERIFIED",
        applicability_reason_code="APPLICABLE",
        version=1,
        created_at=NOW,
        updated_at=NOW,
        deleted_at=None,
        evidence=(policy_evidence, terms_evidence),
    )
    edition = TermsEdition(
        id=link.terms_edition_id,
        household_space_id=household_id,
        document_version_id=terms_document_id,
        insurer_display="Synthetic Insurer",
        insurer_key="synthetic-insurer",
        product_display="Sample Policy",
        product_key="sample-policy",
        applicability_start=date(2025, 1, 1),
        applicability_end=date(2025, 12, 31),
        content_sha256="b" * 64,
        normalization_version="unicode-nfc-v1",
        version=1,
        created_at=NOW,
        updated_at=NOW,
        deleted_at=None,
    )
    clause = Clause(
        id=link.clause_id,
        household_space_id=household_id,
        terms_edition_id=edition.id,
        parent_clause_id=None,
        clause_type="article",
        label="Article A",
        normalized_title="synthetic eligibility",
        normalized_text="synthetic clause text",
        physical_page_start=2,
        physical_page_end=2,
        normalization_version="unicode-nfc-v1",
        version=1,
        created_at=NOW,
        updated_at=NOW,
        deleted_at=None,
        evidence=(terms_evidence,),
    )
    return RiderClauseLinkValidationContext(
        link=link,
        policy_contract_id=_id(110),
        policy_household_space_id=household_id,
        contract_date=date(2025, 6, 1),
        policy_insurer_key="synthetic-insurer",
        policy_product_key="sample-policy",
        policy_document_version_id=policy_document_id,
        rider_policy_contract_id=_id(110),
        rider_document_kind="policy",
        rider_source_evidence=policy_evidence,
        terms_edition=edition,
        clause=clause,
        candidate_kind="rider_clause",
        candidate_aggregate_id=link.id,
        candidate_review_state="AI_VERIFIED",
        evidence_integrity_valid=True,
        common_special_terms_conflict=False,
    )


def _reason(context: RiderClauseLinkValidationContext) -> str:
    with pytest.raises(RiderClauseLinkInvalid) as captured:
        validate_rider_clause_link(HouseholdScope(context.policy_household_space_id), context)
    return captured.value.reason_code


def test_verified_policy_rider_and_applicable_terms_pass() -> None:
    context = _context()

    validate_rider_clause_link(HouseholdScope(context.policy_household_space_id), context)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"rider_document_kind": "terms"}, "TERMS_ONLY_RIDER"),
        ({"contract_date": None}, "CONTRACT_DATE_UNKNOWN"),
        ({"contract_date": date(2024, 12, 31)}, "TERMS_EDITION_NOT_APPLICABLE"),
        ({"policy_insurer_key": "different-insurer"}, "TERMS_EDITION_MISMATCH"),
        ({"policy_product_key": "different-product"}, "TERMS_EDITION_MISMATCH"),
        ({"candidate_kind": "rider"}, "CANDIDATE_DOMAIN_MISMATCH"),
        ({"candidate_review_state": "NEEDS_REVIEW"}, "CANDIDATE_NOT_APPROVED"),
        ({"evidence_integrity_valid": False}, "LINK_EVIDENCE_INVALID"),
        ({"common_special_terms_conflict": True}, "TERMS_SCOPE_CONFLICT"),
    ],
)
def test_validation_rejects_ineligible_context(change: dict[str, object], reason: str) -> None:
    context = replace(_context(), **change)

    assert _reason(context) == reason


def test_cross_household_link_is_denied() -> None:
    context = _context()

    with pytest.raises(RiderClauseLinkInvalid) as captured:
        validate_rider_clause_link(HouseholdScope(_id(999)), context)

    assert captured.value.reason_code == "LINK_SCOPE_MISMATCH"


def test_clause_must_belong_to_selected_terms_document() -> None:
    context = _context()
    wrong_evidence = replace(context.clause.evidence[0], document_version_id=_id(777))
    wrong_clause = replace(context.clause, evidence=(wrong_evidence,))

    assert _reason(replace(context, clause=wrong_clause)) == "CLAUSE_DOCUMENT_MISMATCH"


def test_link_evidence_must_exactly_cover_policy_and_clause_evidence() -> None:
    context = _context()
    incomplete = replace(
        context,
        link=replace(context.link, evidence=(context.rider_source_evidence,)),
    )

    assert _reason(incomplete) == "LINK_EVIDENCE_INCOMPLETE"


def test_soft_deleted_link_cannot_be_confirmed() -> None:
    context = _context()
    deleted = replace(context, link=replace(context.link, deleted_at=NOW))

    assert _reason(deleted) == "LINK_NOT_ACTIVE"


class _FakeLinkRepository:
    def __init__(self, context: RiderClauseLinkValidationContext) -> None:
        self.context = context
        self.last_rejection_reason: str | None = None

    def list_for_rider(self, scope: HouseholdScope, rider_id: UUID) -> tuple[RiderClauseLink, ...]:
        if (
            scope.household_space_id != self.context.link.household_space_id
            or rider_id != self.context.link.rider_id
        ):
            return ()
        return (self.context.link,)

    def confirm(
        self, scope: HouseholdScope, link_id: UUID, *, expected_version: int
    ) -> RiderClauseLink:
        if expected_version != self.context.link.version or link_id != self.context.link.id:
            raise ClauseVersionConflict
        validate_rider_clause_link(scope, self.context)
        return replace(
            self.context.link,
            review_state="USER_CONFIRMED",
            version=expected_version + 1,
        )

    def reject(
        self,
        scope: HouseholdScope,
        link_id: UUID,
        *,
        expected_version: int,
        reason_code: str,
    ) -> RiderClauseLink:
        if expected_version != self.context.link.version or link_id != self.context.link.id:
            raise ClauseVersionConflict
        self.last_rejection_reason = reason_code
        return replace(
            self.context.link,
            review_state="rejected",
            applicability_reason_code=reason_code,
            version=expected_version + 1,
        )


def test_service_confirms_with_optimistic_version() -> None:
    context = _context()
    service = RiderClauseLinkService(_FakeLinkRepository(context))

    confirmed = service.confirm_rider_clause_link(
        HouseholdScope(context.policy_household_space_id),
        context.link.id,
        expected_version=1,
    )

    assert confirmed.review_state == "USER_CONFIRMED"
    assert confirmed.version == 2


def test_service_preserves_stale_version_conflict() -> None:
    context = _context()
    service = RiderClauseLinkService(_FakeLinkRepository(context))

    with pytest.raises(ClauseVersionConflict):
        service.confirm_rider_clause_link(
            HouseholdScope(context.policy_household_space_id),
            context.link.id,
            expected_version=2,
        )


@pytest.mark.parametrize("reason", ["USER_REJECTED", "WRONG_CLAUSE", "WRONG_EDITION"])
def test_service_accepts_only_bounded_rejection_reason_codes(reason: str) -> None:
    context = _context()
    repository = _FakeLinkRepository(context)
    service = RiderClauseLinkService(repository)

    rejected = service.reject_rider_clause_link(
        HouseholdScope(context.policy_household_space_id),
        context.link.id,
        expected_version=1,
        reason_code=reason,
    )

    assert rejected.review_state == "rejected"
    assert repository.last_rejection_reason == reason


def test_service_rejects_free_form_reason() -> None:
    context = _context()
    service = RiderClauseLinkService(_FakeLinkRepository(context))

    with pytest.raises(RiderClauseLinkInvalid) as captured:
        service.reject_rider_clause_link(
            HouseholdScope(context.policy_household_space_id),
            context.link.id,
            expected_version=1,
            reason_code="contains private free form text",
        )

    assert captured.value.reason_code == "INVALID_REJECTION_REASON"
