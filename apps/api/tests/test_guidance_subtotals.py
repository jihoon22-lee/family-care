"""Fixed subtotals are explicit hypotheses over one actual event snapshot."""

from dataclasses import replace
from decimal import Decimal, localcontext
from uuid import UUID

import pytest
from familycare_api.common.coverage_identity import CanonicalCoverageIdentity, CanonicalCoverageRef
from familycare_api.common.scope import HouseholdScope
from familycare_api.guidance.engine import LocalGuidanceEngine
from familycare_api.guidance.models import GuidancePayoutCase, GuidanceSourceReference
from familycare_api.guidance.private_adapter import adapt_private_guidance
from familycare_api.guidance.subtotals import fixed_subtotal_projection

from apps.api.tests.test_private_knowledge_engine import (
    HOUSEHOLD_ID,
    _context,
    _coverage,
    _event,
)


def candidate(number, amount="100", *, currency="KRW"):
    context = adapt_private_guidance(_context(_coverage(number, amount, currency=currency)))
    source = replace(
        context.coverages[0],
        current_confirmation_decision=None,
        current_confirmed_status=None,
        status_intervals=(),
    )
    ref = CanonicalCoverageRef(
        kind="OPERATIONAL_RIDER",
        contract_id=UUID(int=10000 + number),
        coverage_id=UUID(int=20000 + number),
    )
    identity = CanonicalCoverageIdentity(
        ref=ref,
        source_refs=(source.ref, ref),
        authority="PROGRAM_VERIFIED_SOURCE_IDENTITY",
        ledger_version=1,
        verification_digest_sha256=f"{number:064x}",
    )
    context = replace(context, coverages=(replace(source, canonical_identity=identity),))
    return (
        LocalGuidanceEngine()
        .evaluate(HouseholdScope(HOUSEHOLD_ID), _event(), context)
        .candidates[0]
    )


def response(*candidates):
    base = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID), _event(), adapt_private_guidance(_context())
    )
    return base.model_copy(update={"candidates": candidates})


def amounts(result):
    return [(s["currency"], s["amount"]) for s in result["fixed_subtotals"]]


def reasons(result):
    return {item["reason_code"] for item in result["subtotal_omissions"]}


def test_two_actual_contracts_keep_scoped_assumptions_and_calculation_references():
    first, second = candidate(1), candidate(2, "200")
    original = response(first, second)
    before = original.model_dump_json()
    result = fixed_subtotal_projection(original)
    assert amounts(result) == [("KRW", "300")]
    subtotal = result["fixed_subtotals"][0]
    assert subtotal["conditional"] is True and subtotal["basis"] == "ASSUMED_COMBINATION"
    assert subtotal["partial"] is False and len(subtotal["items"]) == 2
    assert {item["ref"]["coverage_id"] for item in subtotal["items"]} == {
        str(first.ref.coverage_id),
        str(second.ref.coverage_id),
    }
    assumptions = {a["code"]: a for a in subtotal["scoped_assumptions"]}
    assert len(assumptions["INDEPENDENT_FIXED_PAYMENTS_ASSUMED"]["applies_to"]) == 2
    assert "DOCUMENT_CONTINUITY_ASSUMED" in assumptions
    assert subtotal["items"][0]["trace_reference"]["publication_id"] == str(
        first.estimate.trace.publication_id
    )
    assert original.model_dump_json() == before


def test_document_status_uncertainty_is_scoped_and_does_not_remove_complete_amounts():
    first = candidate(1).model_copy(
        update={
            "freshness": "STATUS_UNRESOLVED",
            "group": "CONDITIONAL",
            "assumptions": ("EVENT_STATUS_UNRESOLVED",),
        }
    )
    result = fixed_subtotal_projection(response(first, candidate(2)))
    assert amounts(result) == [("KRW", "200")]
    assumption = next(
        a
        for a in result["fixed_subtotals"][0]["scoped_assumptions"]
        if a["code"] == "EVENT_STATUS_UNRESOLVED"
    )
    assert len(assumption["applies_to"]) == 1


def test_known_canonical_duplicate_source_is_counted_once():
    first, second = candidate(1), candidate(2, "200")
    private = first.model_copy(update={"ref": first.canonical_identity.source_refs[0]})
    result = fixed_subtotal_projection(response(private, first, second))
    assert amounts(result) == [("KRW", "300")]
    assert len(result["fixed_subtotals"][0]["items"]) == 2


def test_conflicting_duplicate_amounts_are_omitted_without_arbitrary_selection():
    first = candidate(1)
    duplicate = first.model_copy(update={"estimate": candidate(2, "200").estimate})
    result = fixed_subtotal_projection(response(first, duplicate, candidate(3), candidate(4)))
    assert amounts(result) == [("KRW", "200")]
    assert "CANONICAL_DUPLICATE_CONFLICT" in reasons(result)
    assert result["fixed_subtotals"][0]["partial"] is True


def test_same_contract_coverages_cannot_be_assumed_to_have_separate_budgets():
    first, second = candidate(1), candidate(2)
    shared_ref = second.ref.model_copy(update={"contract_id": first.ref.contract_id})
    second = second.model_copy(update={"ref": shared_ref, "canonical_identity": None})
    result = fixed_subtotal_projection(response(first, second, candidate(3), candidate(4)))
    assert amounts(result) == [("KRW", "200")]
    assert "SAME_CONTRACT_COMBINATION_UNRESOLVED" in reasons(result)


def test_private_contract_alias_cannot_evade_same_contract_exclusion():
    first, second = candidate(1), candidate(2)
    private_ref = second.ref.model_copy(
        update={
            "kind": "PRIVATE_KNOWLEDGE_COVERAGE",
            "contract_id": first.canonical_identity.source_refs[0].contract_id,
        }
    )
    second = second.model_copy(update={"ref": private_ref, "canonical_identity": None})
    result = fixed_subtotal_projection(response(first, second, candidate(3), candidate(4)))
    assert amounts(result) == [("KRW", "200")]
    assert "SAME_CONTRACT_COMBINATION_UNRESOLVED" in reasons(result)


def test_unresolved_private_identity_is_not_independence_proof():
    first = candidate(1)
    first = first.model_copy(
        update={"ref": first.canonical_identity.source_refs[0], "canonical_identity": None}
    )
    result = fixed_subtotal_projection(response(first, candidate(2), candidate(3)))
    assert amounts(result) == [("KRW", "200")]
    assert "CANONICAL_CONTRACT_UNRESOLVED" in reasons(result)


def payout_case(value, key):
    return GuidancePayoutCase(
        case_key=key,
        benefit_kind=value.benefit_kind,
        condition_result=value.condition_result,
        reason_codes=value.reason_codes,
        assumptions=value.assumptions,
        conditions=value.conditions,
        relevance=value.relevance,
        estimate=value.estimate,
    )


@pytest.mark.parametrize("relation", ["MUTUALLY_EXCLUSIVE", "UNRESOLVED"])
def test_multiple_cases_never_contribute_an_arbitrary_amount_or_maximum(relation):
    first = candidate(1)
    first = first.model_copy(
        update={
            "cases": (payout_case(first, "case-a"), payout_case(candidate(2, "999"), "case-b")),
            "case_relation": relation,
        }
    )
    result = fixed_subtotal_projection(response(first, candidate(3), candidate(4)))
    assert amounts(result) == [("KRW", "200")]
    assert "MULTIPLE_PAYOUT_CASES" in reasons(result)


def test_single_case_retains_its_identity_in_the_subtotal():
    first = candidate(1)
    first = first.model_copy(update={"cases": (payout_case(first, "case-a"),)})
    result = fixed_subtotal_projection(response(first, candidate(2)))
    assert result["fixed_subtotals"][0]["items"][0]["case_key"] == "case-a"


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ({"condition_result": "UNKNOWN"}, "EVENT_CONDITIONS_UNRESOLVED"),
        ({"benefit_kind": "INDEMNITY"}, "NON_FIXED_BENEFIT"),
    ],
)
def test_unresolved_conditions_and_indemnity_leave_other_fixed_amounts(mutation, reason):
    result = fixed_subtotal_projection(
        response(candidate(1).model_copy(update=mutation), candidate(2), candidate(3))
    )
    assert amounts(result) == [("KRW", "200")]
    assert reason in reasons(result)
    assert result["fixed_subtotals"][0]["partial"] is (reason != "NON_FIXED_BENEFIT")


@pytest.mark.parametrize(
    "kind,basis",
    [
        ("FORMULA", "DOCUMENT_FORMULA"),
        ("RANGE", "SOURCE_ALTERNATIVES"),
        ("UNAVAILABLE", "DOCUMENT_FORMULA"),
        ("POINT", "USER_SCENARIO"),
        ("POINT", "CONFIRMED_COST_SUBSET"),
        ("POINT", "REGISTERED_COSTS"),
    ],
)
def test_only_actual_document_formula_points_are_addends(kind, basis):
    first = candidate(1)
    first = first.model_copy(
        update={"estimate": first.estimate.model_copy(update={"kind": kind, "basis": basis})}
    )
    result = fixed_subtotal_projection(response(first, candidate(2), candidate(3)))
    assert amounts(result) == [("KRW", "200")]
    assert result["subtotal_omissions"]


@pytest.mark.parametrize(
    "trace_change",
    [
        {"status": "PARTIAL"},
        {"value": "999"},
        {"currency": "USD"},
        {"unit": "DAYS"},
        {"missing_paths": ("MedicalEvent.admission_days",)},
    ],
)
def test_incomplete_or_inconsistent_trace_is_not_promoted(trace_change):
    first = candidate(1)
    trace = first.estimate.trace.model_copy(update=trace_change)
    first = first.model_copy(
        update={"estimate": first.estimate.model_copy(update={"trace": trace})}
    )
    result = fixed_subtotal_projection(response(first, candidate(2), candidate(3)))
    assert amounts(result) == [("KRW", "200")]
    assert "CALCULATION_TRACE_UNAVAILABLE" in reasons(result)


def test_two_currencies_are_independent_subtotals_without_conversion():
    result = fixed_subtotal_projection(
        response(
            candidate(1), candidate(2), candidate(3, currency="USD"), candidate(4, currency="USD")
        )
    )
    assert amounts(result) == [("KRW", "200"), ("USD", "200")]
    assert all(not s["partial"] for s in result["fixed_subtotals"])


@pytest.mark.parametrize("values", [(), ("100",), ("0",)])
def test_at_least_two_addends_are_required_and_absence_does_not_become_zero(values):
    result = fixed_subtotal_projection(
        response(*(candidate(i + 1, value) for i, value in enumerate(values)))
    )
    assert result["fixed_subtotals"] == []
    if values:
        assert "INSUFFICIENT_COMPATIBLE_ADDENDS" in reasons(result)


def test_decimal_sum_does_not_depend_on_process_decimal_precision():
    first, second = candidate(1, "0.1"), candidate(2, "0.2")
    with localcontext() as context:
        context.prec = 1
        result = fixed_subtotal_projection(response(first, second))
    assert amounts(result) == [("KRW", "0.3")]
    assert Decimal(result["fixed_subtotals"][0]["amount"]) == Decimal("0.3")


def test_key_is_order_independent_and_changes_with_event_or_formula_versions():
    first, second = candidate(1), candidate(2)
    original = response(first, second)
    key = fixed_subtotal_projection(original)["fixed_subtotals"][0]["subtotal_key"]
    assert (
        fixed_subtotal_projection(response(second, first))["fixed_subtotals"][0]["subtotal_key"]
        == key
    )
    updated = original.model_copy(update={"event_version": original.event_version + 1})
    assert fixed_subtotal_projection(updated)["fixed_subtotals"][0]["subtotal_key"] != key
    changed_trace = first.estimate.trace.model_copy(update={"formula_digest_sha256": "f" * 64})
    changed = first.model_copy(
        update={"estimate": first.estimate.model_copy(update={"trace": changed_trace})}
    )
    assert (
        fixed_subtotal_projection(response(changed, second))["fixed_subtotals"][0]["subtotal_key"]
        != key
    )


@pytest.mark.parametrize(
    "kind,source_id,version",
    [
        ("EVENT_FACT", str(UUID(int=999)), 1),
        ("EVENT_TEXT", str(_event().id), _event().version + 1),
        ("EVENT_SCENARIO", str(_event().id), _event().version),
    ],
)
def test_actual_subtotal_refuses_another_event_version_or_scenario_trace(kind, source_id, version):
    first = candidate(1)
    trace = first.estimate.trace
    ref = GuidanceSourceReference(
        source_kind=kind, source_id=source_id, version=version, digest_sha256="a" * 64
    )
    first = first.model_copy(
        update={
            "estimate": first.estimate.model_copy(
                update={
                    "trace": trace.model_copy(update={"source_refs": (*trace.source_refs, ref)}),
                }
            )
        }
    )
    result = fixed_subtotal_projection(response(first, candidate(2), candidate(3)))
    assert amounts(result) == [("KRW", "200")]
    assert "CALCULATION_CONTEXT_MISMATCH" in reasons(result)


def test_a_single_addend_in_another_currency_is_not_added_or_zero_filled():
    result = fixed_subtotal_projection(
        response(candidate(1), candidate(2), candidate(3, currency="USD"))
    )
    assert amounts(result) == [("KRW", "200")]
    assert result["fixed_subtotals"][0]["partial"] is False
    assert "INSUFFICIENT_COMPATIBLE_ADDENDS" in reasons(result)


def test_unknown_currency_omission_marks_the_existing_sum_partial():
    first = candidate(1)
    first = first.model_copy(
        update={"estimate": first.estimate.model_copy(update={"currency": None})}
    )
    result = fixed_subtotal_projection(response(first, candidate(2), candidate(3)))
    assert amounts(result) == [("KRW", "200")]
    assert result["fixed_subtotals"][0]["partial"] is True


def test_money_output_bound_does_not_round_or_truncate_a_large_sum():
    values = []
    for n in (1, 2):
        value = candidate(n)
        amount = "9" * 80
        values.append(
            value.model_copy(
                update={
                    "estimate": value.estimate.model_copy(
                        update={
                            "amount": amount,
                            "trace": value.estimate.trace.model_copy(update={"value": amount}),
                        }
                    )
                }
            )
        )
    result = fixed_subtotal_projection(response(*values))
    assert result["fixed_subtotals"] == []
    assert reasons(result) == {"SUBTOTAL_AMOUNT_LIMIT_EXCEEDED"}
