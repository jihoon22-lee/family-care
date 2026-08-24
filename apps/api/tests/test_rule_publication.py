"""Decision table for immutable CoverageRule publication."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest
from familycare_api.clauses.dsl import RULE_SCHEMA_VERSION
from familycare_api.clauses.errors import ClauseVersionConflict, CoverageRuleInvalid
from familycare_api.clauses.rules import (
    CoverageRule,
    CoverageRuleService,
    CoverageRuleVersion,
    RulePublicationContext,
    validate_publishable_rule,
)
from familycare_api.common.evidence import EvidenceRef
from familycare_api.common.scope import HouseholdScope

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _id(value: int) -> UUID:
    return UUID(int=value)


def _evidence(value: int, document: int, hash_character: str, page: int) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=_id(value),
        document_version_id=_id(document),
        extraction_id=_id(value + 100),
        content_sha256=hash_character * 64,
        physical_page=page,
        bbox=(Decimal("1"), Decimal("2"), Decimal("30"), Decimal("40")),
        review_state="USER_CONFIRMED",
    )


def _context() -> RulePublicationContext:
    household_id = _id(1)
    policy_evidence = _evidence(20, 21, "a", 1)
    clause_evidence = _evidence(30, 31, "b", 2)
    evidence = (policy_evidence, clause_evidence)
    rule = CoverageRule(
        id=_id(40),
        household_space_id=household_id,
        rider_clause_link_id=_id(41),
        rule_key="synthetic-temporal-rule",
        current_status="generated",
        version=1,
        created_at=NOW,
        updated_at=NOW,
        deleted_at=None,
    )
    document: dict[str, object] = {
        "schema_version": RULE_SCHEMA_VERSION,
        "rule_kind": "temporal",
        "required": True,
        "input_field_paths": ["MedicalEvent.event_date"],
        "expression": {
            "op": "date_between",
            "field": "MedicalEvent.event_date",
            "value": {"start": "2026-01-01", "end": "2026-12-31"},
            "unit": "date",
        },
        "result_reason_code": "SYNTHETIC_TEMPORAL_MATCH",
        "evidence_ids": [str(item.evidence_id) for item in evidence],
    }
    version = CoverageRuleVersion(
        id=_id(50),
        coverage_rule_id=rule.id,
        candidate_version_id=_id(51),
        version_number=1,
        schema_version=RULE_SCHEMA_VERSION,
        rule_kind="temporal",
        required=True,
        input_field_paths=("MedicalEvent.event_date",),
        rule_document=document,
        result_reason_code="SYNTHETIC_TEMPORAL_MATCH",
        review_state="AI_VERIFIED",
        executable=False,
        generator_version="synthetic-generator-v1",
        verifier_version="synthetic-verifier-v1",
        created_at=NOW,
        published_at=None,
        evidence=evidence,
    )
    return RulePublicationContext(
        rule=rule,
        candidate_version=version,
        link_id=rule.rider_clause_link_id,
        link_review_state="USER_CONFIRMED",
        link_evidence_ids=frozenset(item.evidence_id for item in evidence),
        candidate_kind="coverage_rule",
        candidate_aggregate_id=rule.id,
        candidate_review_state="AI_VERIFIED",
        candidate_is_current=True,
        candidate_evidence_ids=frozenset(item.evidence_id for item in evidence),
        evidence_integrity_valid=True,
    )


def _reason(context: RulePublicationContext) -> str:
    with pytest.raises(CoverageRuleInvalid) as captured:
        validate_publishable_rule(HouseholdScope(context.rule.household_space_id), context)
    return captured.value.reason_code


@pytest.mark.parametrize("review_state", ["AI_VERIFIED", "USER_CONFIRMED"])
def test_only_approved_exact_candidate_is_publishable(review_state: str) -> None:
    context = _context()
    candidate = replace(
        context.candidate_version,
        review_state=cast(object, review_state),
    )
    approved = replace(
        context,
        candidate_version=candidate,
        candidate_review_state=cast(object, review_state),
    )

    validated = validate_publishable_rule(HouseholdScope(context.rule.household_space_id), approved)

    assert validated.schema_version == RULE_SCHEMA_VERSION
    assert validated.evidence_ids == tuple(
        str(item.evidence_id) for item in context.candidate_version.evidence
    )


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"candidate_review_state": "NEEDS_REVIEW"}, "RULE_CANDIDATE_NOT_APPROVED"),
        ({"candidate_review_state": "rejected"}, "RULE_CANDIDATE_NOT_APPROVED"),
        ({"candidate_is_current": False}, "RULE_CANDIDATE_STALE"),
        ({"candidate_kind": "rider_clause"}, "RULE_CANDIDATE_MISMATCH"),
        ({"candidate_aggregate_id": _id(999)}, "RULE_CANDIDATE_MISMATCH"),
        ({"link_review_state": "NEEDS_REVIEW"}, "RIDER_CLAUSE_LINK_NOT_APPROVED"),
        ({"link_review_state": "rejected"}, "RIDER_CLAUSE_LINK_NOT_APPROVED"),
        ({"evidence_integrity_valid": False}, "RULE_EVIDENCE_INVALID"),
    ],
)
def test_publication_rejects_unapproved_or_stale_context(
    mutation: dict[str, object], reason: str
) -> None:
    assert _reason(replace(_context(), **mutation)) == reason


def test_needs_review_rule_version_is_never_executable() -> None:
    context = _context()
    candidate = replace(context.candidate_version, review_state="NEEDS_REVIEW")

    assert _reason(replace(context, candidate_version=candidate)) == "RULE_VERSION_NOT_APPROVED"


def test_wrong_schema_and_unsupported_dsl_remain_informational() -> None:
    context = _context()
    wrong_schema = replace(
        context.candidate_version,
        schema_version="coverage-rule-v0",
        rule_document={
            **context.candidate_version.rule_document,
            "schema_version": "coverage-rule-v0",
        },
    )
    unsupported = replace(
        context.candidate_version,
        rule_document={
            **context.candidate_version.rule_document,
            "expression": {"op": "python", "args": ["synthetic"]},
        },
    )

    assert _reason(replace(context, candidate_version=wrong_schema)) == "RULE_DSL_INVALID"
    assert _reason(replace(context, candidate_version=unsupported)) == "RULE_DSL_INVALID"


def test_invented_or_missing_evidence_is_rejected() -> None:
    context = _context()
    invented = _id(888)

    assert (
        _reason(
            replace(
                context,
                candidate_evidence_ids=context.candidate_evidence_ids | {invented},
            )
        )
        == "RULE_EVIDENCE_MISMATCH"
    )
    assert (
        _reason(
            replace(
                context,
                link_evidence_ids=frozenset({context.candidate_version.evidence[0].evidence_id}),
            )
        )
        == "RULE_EVIDENCE_MISMATCH"
    )


def test_cross_household_and_deleted_rule_are_denied() -> None:
    context = _context()
    with pytest.raises(CoverageRuleInvalid) as captured:
        validate_publishable_rule(HouseholdScope(_id(999)), context)
    assert captured.value.reason_code == "RULE_SCOPE_MISMATCH"

    assert (
        _reason(replace(context, rule=replace(context.rule, deleted_at=NOW))) == "RULE_NOT_ACTIVE"
    )


def test_already_executable_version_cannot_be_republished() -> None:
    context = _context()

    assert (
        _reason(
            replace(
                context,
                candidate_version=replace(
                    context.candidate_version,
                    executable=True,
                    published_at=NOW,
                ),
            )
        )
        == "RULE_VERSION_ALREADY_PUBLISHED"
    )


class _FakeRuleRepository:
    def __init__(self, context: RulePublicationContext) -> None:
        self.context = context

    def list_versions(
        self, scope: HouseholdScope, rule_id: UUID
    ) -> tuple[CoverageRuleVersion, ...]:
        if (
            scope.household_space_id != self.context.rule.household_space_id
            or rule_id != self.context.rule.id
        ):
            return ()
        return (self.context.candidate_version,)

    def publish(
        self,
        scope: HouseholdScope,
        rule_id: UUID,
        version_id: UUID,
        *,
        expected_version: int,
    ) -> CoverageRuleVersion:
        if (
            expected_version != self.context.rule.version
            or rule_id != self.context.rule.id
            or version_id != self.context.candidate_version.id
        ):
            raise ClauseVersionConflict
        validate_publishable_rule(scope, self.context)
        return replace(
            self.context.candidate_version,
            id=_id(777),
            version_number=self.context.candidate_version.version_number + 1,
            executable=True,
            published_at=NOW,
        )


def test_service_publishes_a_new_immutable_version() -> None:
    context = _context()
    service = CoverageRuleService(_FakeRuleRepository(context))

    published = service.publish_coverage_rule(
        HouseholdScope(context.rule.household_space_id),
        context.rule.id,
        context.candidate_version.id,
        expected_version=1,
    )

    assert published.id != context.candidate_version.id
    assert published.version_number == 2
    assert published.executable is True
    assert context.candidate_version.executable is False


def test_service_preserves_expected_version_conflict() -> None:
    context = _context()
    service = CoverageRuleService(_FakeRuleRepository(context))

    with pytest.raises(ClauseVersionConflict):
        service.publish_coverage_rule(
            HouseholdScope(context.rule.household_space_id),
            context.rule.id,
            context.candidate_version.id,
            expected_version=2,
        )
