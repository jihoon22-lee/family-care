"""Fail-closed tests for published CoverageRule execution."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from familycare_api.clauses.dsl import RULE_SCHEMA_VERSION
from familycare_api.clauses.rules import CoverageRuleVersion
from familycare_api.common.evidence import EvidenceRef
from familycare_api.decisions.domain import FactContext, FactValue
from familycare_api.decisions.rule_runtime import RuleRuntimeError, evaluate_rule

NOW = datetime(2026, 8, 25, tzinfo=UTC)
RIDER_ID = UUID(int=10)


def _id(value: int) -> UUID:
    return UUID(int=value)


def _evidence(*, review_state: str = "USER_CONFIRMED") -> EvidenceRef:
    return EvidenceRef(
        evidence_id=_id(20),
        document_version_id=_id(21),
        extraction_id=_id(22),
        content_sha256="a" * 64,
        physical_page=1,
        bbox=(Decimal("1"), Decimal("2"), Decimal("30"), Decimal("40")),
        review_state=review_state,  # type: ignore[arg-type]
    )


def _rule(*, evidence: tuple[EvidenceRef, ...] | None = None) -> CoverageRuleVersion:
    resolved_evidence = evidence or (_evidence(),)
    document: dict[str, object] = {
        "schema_version": RULE_SCHEMA_VERSION,
        "rule_kind": "classification",
        "required": True,
        "input_field_paths": ["MedicalEvent.classification"],
        "expression": {
            "op": "equals",
            "field": "MedicalEvent.classification",
            "value": "injury",
        },
        "result_reason_code": "SYNTHETIC_CLASSIFICATION_MATCH",
        "evidence_ids": [str(item.evidence_id) for item in resolved_evidence],
    }
    return CoverageRuleVersion(
        id=_id(30),
        coverage_rule_id=_id(31),
        candidate_version_id=_id(32),
        version_number=1,
        schema_version=RULE_SCHEMA_VERSION,
        rule_kind="classification",
        required=True,
        input_field_paths=("MedicalEvent.classification",),
        rule_document=document,
        result_reason_code="SYNTHETIC_CLASSIFICATION_MATCH",
        review_state="USER_CONFIRMED",
        executable=True,
        generator_version="synthetic-generator-v1",
        verifier_version="synthetic-verifier-v1",
        created_at=NOW,
        published_at=NOW,
        evidence=resolved_evidence,
    )


def _context(value: object = "injury") -> FactContext:
    return FactContext(
        medical_event={
            "classification": FactValue(value=value, confirmation="user", evidence_ids=())
        },
        policy={},
        rider={},
        claim_history={},
    )


def test_match_uses_published_reason_and_actual_rider_id() -> None:
    evaluation = evaluate_rule(_rule(), _context(), rider_id=RIDER_ID)

    assert evaluation.result == "MATCH"
    assert evaluation.reason_code == "SYNTHETIC_CLASSIFICATION_MATCH"
    assert evaluation.rider_id == RIDER_ID
    assert evaluation.rider_id != evaluation.rule_version_id
    assert evaluation.evidence == _rule().evidence


def test_missing_actual_rider_id_fails_closed() -> None:
    with pytest.raises(RuleRuntimeError, match="RIDER_ID_REQUIRED"):
        evaluate_rule(_rule(), _context())


def test_unconfirmed_rule_evidence_can_never_match() -> None:
    rule = _rule(evidence=(_evidence(review_state="NEEDS_REVIEW"),))

    evaluation = evaluate_rule(rule, _context(), rider_id=RIDER_ID)

    assert evaluation.result == "UNKNOWN"
    assert evaluation.reason_code == "STALE_OR_UNCONFIRMED_EVIDENCE"


def test_invalid_runtime_fact_is_isolated_as_unknown() -> None:
    rule = _rule()
    invalid_document = {
        **dict(rule.rule_document),
        "rule_kind": "eligibility",
        "input_field_paths": ["Rider.insured_amount"],
        "expression": {
            "op": "range",
            "field": "Rider.insured_amount",
            "value": {"min": 1, "max": 2},
            "unit": "amount",
        },
    }
    invalid_rule = replace(
        rule,
        rule_kind="eligibility",
        input_field_paths=("Rider.insured_amount",),
        rule_document=invalid_document,
    )
    context = FactContext(
        medical_event={},
        policy={},
        rider={
            "insured_amount": FactValue(value="not-a-number", confirmation="user", evidence_ids=())
        },
        claim_history={},
    )

    evaluation = evaluate_rule(invalid_rule, context, rider_id=RIDER_ID)

    assert evaluation.result == "UNKNOWN"
    assert evaluation.reason_code == "INVALID_DECIMAL"


def test_missing_fact_preserves_field_path_for_question() -> None:
    evaluation = evaluate_rule(_rule(), FactContext({}, {}, {}, {}), rider_id=RIDER_ID)

    assert evaluation.result == "UNKNOWN"
    assert evaluation.missing_fields == ("MedicalEvent.classification",)
