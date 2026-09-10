"""Source calculation modes preserve costs and event-dependent reductions."""

from decimal import Decimal

import pytest
from familycare_api.clauses.dsl import validate_rule_document
from familycare_api.decisions.domain import FactContext, FactValue
from familycare_api.decisions.knowledge_engine import _CalculationState
from familycare_api.decisions.schemas import MedicalEventCreateRequest
from familycare_api.terms_knowledge.local_candidates import propose_local_candidates
from pydantic import ValidationError

from apps.api.tests.test_terms_local_candidates import _compiled, _snapshot

INDEMNITY = "Reimburse the covered receipt amount in KRW, rounded half up to whole currency units."
DEDUCTIBLE = (
    "Deduct KRW 15 from the gross benefit before applying "
    "the amount cap and rounding; floor at zero."
)
RATIO = "Pay 0.25 of the insured amount in KRW, rounded half up to whole currency units."
REDUCTION = (
    "When the event reduction condition is true, multiply the gross benefit by 0.5; "
    "otherwise keep the gross benefit, before deduction, amount cap and rounding."
)


def source_root(lines):
    snapshot = _snapshot([("Article 1", lines)])
    return _compiled(propose_local_candidates(snapshot), snapshot)["Article 1"]


@pytest.mark.parametrize(("cost", "expected"), [("75", "60"), ("10", "0")])
def test_covered_cost_source_compiles_without_invented_ratio_or_cap(cost, expected):
    root = source_root([INDEMNITY, DEDUCTIBLE])
    assert not root.diagnostics
    assert root.calculation is not None
    validated = validate_rule_document(root.calculation, root.citation_ids)
    assert validated.input_field_paths == ("Receipt.covered_amount",)
    facts = FactContext(
        rider={},
        medical_event={},
        policy={},
        claim_history={},
        receipt={"covered_amount": FactValue(Decimal(cost), "user", ())},
    )
    assert _CalculationState(facts, "KRW").evaluate(validated.calculation) == Decimal(expected)


@pytest.mark.parametrize("value", [True, False, None])
def test_explicit_reduction_fact_preserves_boolean_or_unknown(value):
    request = MedicalEventCreateRequest.model_validate(
        {
            "family_member_id": "00000000-0000-4000-8000-000000000001",
            "mode": "post_treatment",
            "situation": "Synthetic surgery",
            "facts": {"MedicalEvent.reduction_applies": {"value": value, "confirmation": "user"}},
        }
    )
    assert request.facts["MedicalEvent.reduction_applies"].value is value


@pytest.mark.parametrize("value", [0, 1, "true", "false", "1"])
def test_reduction_fact_never_coerces_strings_or_numbers(value):
    with pytest.raises(ValidationError):
        MedicalEventCreateRequest.model_validate(
            {
                "family_member_id": "00000000-0000-4000-8000-000000000001",
                "mode": "post_treatment",
                "situation": "Synthetic surgery",
                "facts": {
                    "MedicalEvent.reduction_applies": {"value": value, "confirmation": "user"}
                },
            }
        )


def test_unsupported_cost_exception_and_currency_conflict_keep_formula_unavailable():
    assert (
        source_root([INDEMNITY + " Unless another exception applies.", DEDUCTIBLE]).calculation
        is None
    )
    assert source_root([INDEMNITY, DEDUCTIBLE.replace("KRW", "USD")]).calculation is None


@pytest.mark.parametrize(("condition", "expected"), [(True, "30"), (False, "60")])
def test_conditional_reduction_keeps_both_branches(condition, expected):
    root = source_root([RATIO, REDUCTION, "The maximum benefit is KRW 80."])
    assert not root.diagnostics
    assert root.calculation is not None
    validated = validate_rule_document(root.calculation, root.citation_ids)
    facts = FactContext(
        rider={"insured_amount": FactValue(Decimal("240"), "user", ())},
        medical_event={"reduction_applies": FactValue(condition, "user", ())},
        policy={},
        claim_history={},
    )
    assert _CalculationState(facts, "KRW").evaluate(validated.calculation) == Decimal(expected)
