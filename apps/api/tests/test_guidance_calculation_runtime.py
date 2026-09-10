"""Source-preserving Decimal traces never manufacture missing money or authority."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext
from uuid import UUID

import pytest
from familycare_api.clauses.dsl import CompiledCalculation, validate_calculation
from familycare_api.guidance.calculation_runtime import (
    CalculationInput,
    CalculationSource,
    CalculationSourceRef,
    CalculationUnitHint,
    evaluate_calculation,
)

REF = CalculationSourceRef("SYNTHETIC_SOURCE", UUID(int=1, version=4), version=3)
SOURCE = CalculationSource(UUID(int=2, version=4), "synthetic-v1", "a" * 64, (REF,))


def field(value, unit="MONEY", *, currency="KRW", provenance="USER_CONFIRMED", stale=False):
    return CalculationInput(
        None if value is None else Decimal(value), unit, currency, provenance, (REF,), stale
    )


def hint(unit, currency=None):
    return CalculationUnitHint(unit, currency, (REF,))


def formula(operator, *operands, rounding=None):
    node = {"op": operator, "args": list(operands)}
    if rounding is not None:
        node["rounding"] = rounding
    return node


def literal(value):
    return {"value": Decimal(value)}


def daily():
    return validate_calculation(
        formula(
            "multiply",
            {"field": "Rider.insured_amount"},
            formula(
                "max",
                formula("subtract", {"field": "MedicalEvent.admission_days"}, literal("2")),
                literal("0"),
            ),
        )
    )


def daily_hints():
    return {
        "/calculation": hint("MONEY", "KRW"),
        "/calculation/args/1/args/0/args/1": hint("DAYS"),
        "/calculation/args/1/args/1": hint("DAYS"),
    }


def test_daily_trace_preserves_all_operands_units_source_and_ast_paths():
    result = evaluate_calculation(
        daily(),
        {
            "Rider.insured_amount": field("100", provenance="PROGRAM_VERIFIED"),
            "MedicalEvent.admission_days": field(
                "5", "DAYS", currency=None, provenance="DERIVED_CONFIRMED"
            ),
        },
        source=SOURCE,
        unit_hints=daily_hints(),
    )
    assert result.status == "COMPLETE" and result.amount == 300
    assert result.value == 300 and result.unit == "MONEY" and result.currency == "KRW"
    assert [step.operation for step in result.steps] == ["subtract", "max", "multiply"]
    subtract, maximum, multiply = result.steps
    assert subtract.value == 3 and subtract.unit == maximum.unit == "DAYS"
    assert [operand.value for operand in subtract.operands] == [5, 2]
    assert subtract.operands[0].field_path == "MedicalEvent.admission_days"
    assert subtract.operands[0].provenance == "DERIVED_CONFIRMED"
    assert subtract.operands[0].source_refs == (REF,)
    assert multiply.operands[1].child_path == "/calculation/args/1"
    assert multiply.operands[0].unit == "MONEY" and multiply.operands[1].unit == "DAYS"
    assert result.source is SOURCE and result.missing_paths == ()
    assert "synthetic-v1" not in repr(result)
    with pytest.raises(FrozenInstanceError):
        result.value = Decimal(999)


def test_negative_intermediate_is_retained_before_explicit_nonnegative_clamp():
    result = evaluate_calculation(
        daily(),
        {
            "Rider.insured_amount": field("100"),
            "MedicalEvent.admission_days": field("1", "DAYS", currency=None),
        },
        source=SOURCE,
        unit_hints=daily_hints(),
    )
    assert result.amount == 0
    assert result.steps[0].value == -1 and result.steps[1].value == 0
    negative = evaluate_calculation(
        validate_calculation(formula("subtract", {"field": "Rider.insured_amount"}, literal("2"))),
        {"Rider.insured_amount": field("1")},
        source=SOURCE,
        unit_hints={"/calculation/args/1": hint("MONEY", "KRW")},
    )
    assert negative.status == "FAILED" and negative.amount is None
    assert "CALCULATION_NEGATIVE_RESULT" in negative.reason_codes
    assert negative.steps[-1].value == -1


@pytest.mark.parametrize(
    "rounding,expected", [("half_up", 1), ("half_even", 0), ("up", 1), ("down", 0)]
)
def test_explicit_rounding_only_occurs_at_the_original_ast_node(rounding, expected):
    node = validate_calculation(formula("round", literal("0.5"), rounding=rounding))
    result = evaluate_calculation(
        node, {}, source=SOURCE, unit_hints={"/calculation/args/0": hint("MONEY", "KRW")}
    )
    assert result.amount == expected
    assert result.steps[-1].rounding_rule == rounding
    assert result.steps[-1].operands[0].value == Decimal("0.5")
    grouped = evaluate_calculation(
        validate_calculation(
            formula("round", formula("add", literal("0.5"), literal("0.5")), rounding="half_up")
        ),
        {},
        source=SOURCE,
    )
    separate = evaluate_calculation(
        validate_calculation(
            formula(
                "add",
                formula("round", literal("0.5"), rounding="half_up"),
                formula("round", literal("0.5"), rounding="half_up"),
            )
        ),
        {},
        source=SOURCE,
    )
    assert grouped.value == 1 and separate.value == 2
    assert grouped.amount is separate.amount is None


@pytest.mark.parametrize("fault", ["missing", "stale", "ai", "unknown", "conflicting", "no_source"])
def test_untrusted_or_missing_input_is_never_substituted_with_zero(fault):
    value = field("5", "DAYS", currency=None)
    if fault == "missing":
        value = replace(value, value=None)
    elif fault == "stale":
        value = replace(value, stale=True)
    elif fault == "ai":
        value = replace(value, provenance="AI_SUGGESTED")
    elif fault == "unknown":
        value = replace(value, provenance="UNKNOWN")
    elif fault == "conflicting":
        value = replace(value, provenance="CONFLICTING")
    else:
        value = replace(value, source_refs=())
    result = evaluate_calculation(
        daily(),
        {"Rider.insured_amount": field("100"), "MedicalEvent.admission_days": value},
        source=SOURCE,
        unit_hints=daily_hints(),
    )
    assert result.value is result.amount is None
    assert result.missing_paths == ("MedicalEvent.admission_days",)
    assert result.steps[0].operands[0].value is None
    assert result.steps[0].operands[0].provenance == value.provenance


def test_partial_add_preserves_complete_child_trace_without_total_or_lower_bound():
    node = validate_calculation(
        formula(
            "add",
            formula("multiply", {"field": "Rider.insured_amount"}, literal("2")),
            {"field": "Receipt.covered_amount"},
        )
    )
    result = evaluate_calculation(node, {"Rider.insured_amount": field("100")}, source=SOURCE)
    assert result.status == "PARTIAL" and result.value is result.amount is None
    assert result.steps[0].value == 200
    assert result.steps[-1].operands[0].child_path == "/calculation/args/0"
    assert result.steps[-1].operands[1].field_path == "Receipt.covered_amount"
    assert result.missing_paths == ("Receipt.covered_amount",)
    assert len(result.completed_addends) == 1
    part = result.completed_addends[0]
    assert part.parent_path == "/calculation" and part.path == "/calculation/args/0"
    assert part.value == 200 and part.unit == "MONEY"
    assert not hasattr(result, "lower") and not hasattr(result, "partial_amount")


def test_missing_shared_deductible_or_limit_does_not_become_partial_payout():
    node = validate_calculation(
        formula(
            "round",
            formula(
                "min",
                formula(
                    "multiply",
                    formula(
                        "max",
                        formula("subtract", {"field": "Receipt.covered_amount"}, literal("10")),
                        literal("0"),
                    ),
                    literal("0.8"),
                ),
                {"field": "Rider.insured_amount"},
            ),
            rounding="half_up",
        )
    )
    result = evaluate_calculation(
        node,
        {"Receipt.covered_amount": field("50")},
        source=SOURCE,
        unit_hints={
            "/calculation/args/0/args/0/args/0/args/0/args/1": hint("MONEY", "KRW"),
            "/calculation/args/0/args/0/args/0/args/1": hint("MONEY", "KRW"),
        },
    )
    assert result.value is result.amount is None
    assert result.completed_addends == ()
    assert any(step.value == 32 for step in result.steps)
    assert result.missing_paths == ("Rider.insured_amount",)


def test_zero_is_real_input_and_different_currencies_cannot_produce_money():
    node = validate_calculation(
        formula("add", {"field": "Rider.insured_amount"}, {"field": "Receipt.covered_amount"})
    )
    same = evaluate_calculation(
        node,
        {"Rider.insured_amount": field("0"), "Receipt.covered_amount": field("0")},
        source=SOURCE,
    )
    assert same.status == "COMPLETE" and same.amount == 0
    mixed = evaluate_calculation(
        node,
        {
            "Rider.insured_amount": field("100"),
            "Receipt.covered_amount": field("10", currency="USD"),
        },
        source=SOURCE,
    )
    assert mixed.amount is None and mixed.status == "FAILED"
    assert "CALCULATION_CURRENCY_MISMATCH" in mixed.reason_codes


def test_untyped_literals_and_unknown_units_do_not_turn_into_money():
    node = validate_calculation(formula("add", literal("100"), literal("0")))
    untyped = evaluate_calculation(node, {}, source=SOURCE)
    assert untyped.value == 100 and untyped.unit == "NUMBER" and untyped.amount is None
    typed = evaluate_calculation(
        node,
        {},
        source=SOURCE,
        unit_hints={
            "/calculation/args/0": hint("MONEY", "KRW"),
            "/calculation/args/1": hint("MONEY", "KRW"),
        },
    )
    assert typed.amount == 100
    absent_basis = evaluate_calculation(
        daily(),
        {
            "Rider.insured_amount": field("100"),
            "MedicalEvent.admission_days": field("5", "DAYS", currency=None),
        },
        source=SOURCE,
    )
    assert absent_basis.amount is None
    assert absent_basis.unit == "UNKNOWN"


def test_context_is_fixed_and_implicit_precision_loss_is_a_stable_failure():
    node = validate_calculation(formula("multiply", literal("123456789.123456"), literal("2")))
    baseline = evaluate_calculation(node, {}, source=SOURCE)
    with localcontext() as ambient:
        ambient.prec = 6
        actual = evaluate_calculation(node, {}, source=SOURCE)
        assert ambient.prec == 6
    assert actual == baseline and actual.value == Decimal("246913578.246912")
    huge_precision = validate_calculation(formula("add", literal("1"), literal("1e-80")))
    failed = evaluate_calculation(huge_precision, {}, source=SOURCE)
    assert failed.status == "FAILED" and failed.value is None
    assert any(
        code in failed.reason_codes
        for code in ("CALCULATION_IMPLICIT_ROUNDING", "CALCULATION_NUMERIC_LIMIT")
    )


def test_tree_and_magnitude_limits_are_fixed_and_value_free():
    oversized = CompiledCalculation("add", tuple(Decimal(1) for _ in range(300)), None)
    result = evaluate_calculation(oversized, {}, source=SOURCE)
    assert result.status == "FAILED" and "CALCULATION_LIMIT_EXCEEDED" in result.reason_codes
    enormous = evaluate_calculation(
        validate_calculation(formula("multiply", literal("1e50"), literal("1e50"))),
        {},
        source=SOURCE,
    )
    assert enormous.status == "FAILED" and enormous.amount is None
    assert "CALCULATION_NUMERIC_LIMIT" in enormous.reason_codes


def test_source_revision_digest_and_real_refs_survive_success_and_failure():
    node = validate_calculation(formula("add", {"field": "Rider.insured_amount"}, literal("0")))
    changed = replace(
        SOURCE,
        publication_id=UUID(int=9, version=4),
        revision="synthetic-v2",
        digest_sha256="b" * 64,
    )
    result = evaluate_calculation(node, {}, source=changed)
    assert result.source == changed and result.source.publication_id != SOURCE.publication_id
    assert result.source.source_refs == (REF,)
    assert result.runtime_revision == "guidance-calculation-v3"


@pytest.mark.parametrize("fault", ["currency", "stale", "ai", "unknown_unit"])
def test_source_unit_hint_cannot_override_currency_input_trust_or_unknown_units(fault):
    node = validate_calculation(
        formula("add", {"field": "Rider.insured_amount"}, {"field": "Receipt.covered_amount"})
    )
    right = field("20")
    if fault == "currency":
        right = replace(right, currency="USD")
    elif fault == "stale":
        right = replace(right, stale=True)
    elif fault == "ai":
        right = replace(right, provenance="AI_SUGGESTED")
    else:
        right = replace(right, unit="UNKNOWN", currency=None)
    result = evaluate_calculation(
        node,
        {"Rider.insured_amount": field("100"), "Receipt.covered_amount": right},
        source=SOURCE,
        unit_hints={"/calculation": hint("MONEY", "KRW")},
    )
    assert result.amount is None
    operand = result.steps[-1].operands[1]
    assert operand.provenance == right.provenance and operand.stale == right.stale
    assert operand.supplied_value == 20
    assert operand.source_refs == right.source_refs
    if fault in {"stale", "ai"}:
        assert operand.value is None


@pytest.mark.parametrize("provenance", ["USER_CONFIRMED", "AI_SUGGESTED"])
def test_out_of_range_source_values_are_not_retained_as_unbounded_trace_numbers(provenance):
    node = validate_calculation(formula("add", {"field": "Rider.insured_amount"}, literal("0")))
    result = evaluate_calculation(
        node, {"Rider.insured_amount": field("1e100000", provenance=provenance)}, source=SOURCE
    )
    assert result.status == "FAILED" and result.amount is None
    assert result.steps[-1].operands[0].value is None
    assert result.steps[-1].operands[0].supplied_value is None
    assert "CALCULATION_NUMERIC_LIMIT" in result.reason_codes


def test_hint_requires_actual_source_refs_and_known_dimensions_cannot_be_overridden():
    with pytest.raises(ValueError, match="^CALCULATION_UNIT_HINT_INVALID$"):
        CalculationUnitHint("MONEY", "KRW", ())
    node = validate_calculation(
        formula("add", {"field": "Rider.insured_amount"}, {"field": "MedicalEvent.admission_days"})
    )
    result = evaluate_calculation(
        node,
        {
            "Rider.insured_amount": field("100"),
            "MedicalEvent.admission_days": field("5", "DAYS", currency=None),
        },
        source=SOURCE,
        unit_hints={"/calculation": hint("MONEY", "KRW")},
    )
    assert result.status == "FAILED" and result.amount is None
    assert "CALCULATION_UNIT_MISMATCH" in result.reason_codes


def test_unit_hint_must_reference_the_supplied_calculation_source_version():
    other = replace(REF, version=4)
    result = evaluate_calculation(
        validate_calculation(formula("add", literal("100"), literal("0"))),
        {},
        source=SOURCE,
        unit_hints={"/calculation": CalculationUnitHint("MONEY", "KRW", (other,))},
    )
    assert result.status == "FAILED" and result.amount is None
    assert result.reason_codes == ("CALCULATION_UNIT_HINT_SOURCE_MISMATCH",)


SCENARIO_FIELD = "MedicalEvent.admission_days"
SCENARIO_REF = CalculationSourceRef(
    "EVENT_SCENARIO", UUID(int=30, version=4), version=7, digest_sha256="c" * 64
)


def scenario_day():
    return CalculationInput(Decimal(5), "DAYS", None, "SCENARIO_ASSUMPTION", (SCENARIO_REF,), False)


def test_scenario_arithmetic_requires_explicit_exact_opt_in_on_every_call():
    assumption = scenario_day()
    inputs = {"Rider.insured_amount": field("100"), SCENARIO_FIELD: assumption}
    before = evaluate_calculation(daily(), inputs, source=SOURCE, unit_hints=daily_hints())
    assert before.amount is None and "CALCULATION_INPUT_UNTRUSTED" in before.reason_codes
    opted_in = evaluate_calculation(
        daily(),
        inputs,
        source=SOURCE,
        unit_hints=daily_hints(),
        scenario_inputs={SCENARIO_FIELD: assumption},
    )
    assert opted_in.status == "COMPLETE" and opted_in.amount == 300
    operand = opted_in.steps[0].operands[0]
    assert operand.provenance == "SCENARIO_ASSUMPTION" and operand.source_refs == (SCENARIO_REF,)
    assert "CALCULATION_SCENARIO_ASSUMPTION" in opted_in.reason_codes
    assert assumption.provenance == "SCENARIO_ASSUMPTION" and not assumption.stale
    after = evaluate_calculation(daily(), inputs, source=SOURCE, unit_hints=daily_hints())
    assert after == before


@pytest.mark.parametrize(
    "mismatch",
    ["value", "unit", "currency", "provenance", "ref", "version", "digest", "stale", "field"],
)
def test_scenario_acceptance_does_not_apply_to_a_different_input(mismatch):
    assumption = scenario_day()
    changes = {
        "value": {"value": Decimal(6)},
        "unit": {"unit": "COUNT"},
        "currency": {"unit": "MONEY", "currency": "USD"},
        "provenance": {"provenance": "USER_CONFIRMED"},
        "ref": {"source_refs": (replace(SCENARIO_REF, source_id=UUID(int=31, version=4)),)},
        "version": {"source_refs": (replace(SCENARIO_REF, version=8),)},
        "digest": {"source_refs": (replace(SCENARIO_REF, digest_sha256="d" * 64),)},
        "stale": {"stale": True},
        "field": {},
    }
    accepted = replace(assumption, **changes[mismatch])
    result = evaluate_calculation(
        daily(),
        {"Rider.insured_amount": field("100"), SCENARIO_FIELD: assumption},
        source=SOURCE,
        unit_hints=daily_hints(),
        scenario_inputs={
            "MedicalEvent.admission" if mismatch == "field" else SCENARIO_FIELD: accepted
        },
    )
    assert result.amount is None and SCENARIO_FIELD in result.missing_paths
    assert result.steps[0].operands[0].provenance == "SCENARIO_ASSUMPTION"


@pytest.mark.parametrize(
    "fault",
    [
        "kind",
        "string_id",
        "no_version",
        "string_version",
        "no_digest",
        "mixed_versions",
        "no_refs",
        "stale",
        "ai",
    ],
)
def test_matching_opt_in_cannot_make_invalid_scenario_source_or_stale_input_usable(fault):
    assumption = scenario_day()
    changes = {
        "kind": {"source_refs": (replace(SCENARIO_REF, source_kind="EVENT_FACT"),)},
        "string_id": {"source_refs": (replace(SCENARIO_REF, source_id="synthetic-event-key"),)},
        "no_version": {"source_refs": (replace(SCENARIO_REF, version=None),)},
        "string_version": {"source_refs": (replace(SCENARIO_REF, version="7"),)},
        "no_digest": {"source_refs": (replace(SCENARIO_REF, digest_sha256=None),)},
        "mixed_versions": {"source_refs": (SCENARIO_REF, replace(SCENARIO_REF, version=8))},
        "no_refs": {"source_refs": ()},
        "stale": {"stale": True},
        "ai": {"provenance": "AI_SUGGESTED"},
    }
    assumption = replace(assumption, **changes[fault])
    result = evaluate_calculation(
        daily(),
        {"Rider.insured_amount": field("100"), SCENARIO_FIELD: assumption},
        source=SOURCE,
        unit_hints=daily_hints(),
        scenario_inputs={SCENARIO_FIELD: assumption},
    )
    assert result.amount is None and SCENARIO_FIELD in result.missing_paths
    assert "CALCULATION_INPUT_UNTRUSTED" in result.reason_codes


@pytest.mark.parametrize(
    "path,unit,currency",
    [
        ("Rider.insured_amount", "MONEY", "KRW"),
        ("Receipt.covered_amount", "MONEY", "KRW"),
        ("ClaimHistory.counted_occurrence", "COUNT", None),
    ],
)
def test_scenario_permission_does_not_extend_to_amounts_receipts_or_claim_counts(
    path, unit, currency
):
    assumption = replace(scenario_day(), unit=unit, currency=currency)
    node = validate_calculation(formula("multiply", {"field": path}, literal("2")))
    result = evaluate_calculation(
        node, {path: assumption}, source=SOURCE, scenario_inputs={path: assumption}
    )
    assert result.value is result.amount is None and path in result.missing_paths


@pytest.mark.parametrize(
    "value", [None, Decimal("NaN"), Decimal("-1"), Decimal("5.5"), Decimal("36501")]
)
def test_scenario_admission_days_must_be_a_valid_explicit_day_count(value):
    assumption = replace(scenario_day(), value=value)
    result = evaluate_calculation(
        daily(),
        {"Rider.insured_amount": field("100"), SCENARIO_FIELD: assumption},
        source=SOURCE,
        unit_hints=daily_hints(),
        scenario_inputs={SCENARIO_FIELD: assumption},
    )
    assert result.amount is None


def test_scenario_opt_in_never_supplies_an_absent_input_value():
    result = evaluate_calculation(
        daily(),
        {"Rider.insured_amount": field("100")},
        source=SOURCE,
        unit_hints=daily_hints(),
        scenario_inputs={SCENARIO_FIELD: scenario_day()},
    )
    assert result.amount is None and SCENARIO_FIELD in result.missing_paths
