"""Source-declared constant amounts do not invent unrelated required facts."""

from dataclasses import replace
from decimal import Decimal

import pytest
from familycare_api.clauses.dsl import RuleValidationError, validate_rule_document
from familycare_api.clauses.rules import validate_publishable_rule
from familycare_api.clauses.schemas import CoverageRuleVersionResponse
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import FactContext
from familycare_api.decisions.knowledge_engine import _CalculationState

from apps.api.tests.test_rule_publication import _context


def _constant_document(context):
    return {
        "schema_version": "coverage-rule-v1",
        "rule_kind": "fixed_amount",
        "required": True,
        "input_field_paths": [],
        "calculation": {"op": "multiply", "args": [{"value": 250}, {"value": 1}]},
        "result_reason_code": "SYNTHETIC_FIXED_AMOUNT",
        "evidence_ids": [str(e.evidence_id) for e in context.candidate_version.evidence],
    }


def test_constant_amount_compiles_and_evaluates_without_any_event_or_rider_facts() -> None:
    context = _context()
    document = _constant_document(context)
    rule = validate_rule_document(document, document["evidence_ids"])
    assert rule.input_field_paths == ()
    assert rule.calculation is not None
    facts = FactContext(rider={}, policy={}, medical_event={}, claim_history={})
    assert _CalculationState(facts, "KRW").evaluate(rule.calculation) == Decimal("250")


def test_empty_dependencies_do_not_hide_a_referenced_insured_amount() -> None:
    context = _context()
    document = _constant_document(context)
    document["calculation"] = {
        "op": "multiply",
        "args": [{"field": "Rider.insured_amount"}, {"value": 1}],
    }
    with pytest.raises(RuleValidationError) as error:
        validate_rule_document(document, document["evidence_ids"])
    assert error.value.reason_code == "INPUT_FIELD_MISMATCH"


def test_constant_rule_survives_domain_publication_and_response_projection() -> None:
    context = _context()
    document = _constant_document(context)
    version = replace(
        context.candidate_version,
        rule_kind="fixed_amount",
        input_field_paths=(),
        rule_document=document,
        result_reason_code="SYNTHETIC_FIXED_AMOUNT",
    )
    published = validate_publishable_rule(
        HouseholdScope(context.rule.household_space_id), replace(context, candidate_version=version)
    )
    assert published.input_field_paths == ()
    assert CoverageRuleVersionResponse.from_domain(version).input_field_paths == ()
