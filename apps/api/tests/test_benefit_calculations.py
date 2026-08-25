"""Decision tables for deterministic, Decimal-only benefit calculations."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from familycare_api.clauses.dsl import RULE_SCHEMA_VERSION
from familycare_api.clauses.rules import CoverageRuleVersion
from familycare_api.common.evidence import EvidenceRef
from familycare_api.decisions.calculation_validation import (
    CalculationValidationError,
    decimal_from_wire,
    round_money,
    validate_receipt_line,
)
from familycare_api.decisions.calculations import (
    Money,
    ReceiptLine,
    calculate_fixed_benefit,
    calculate_indemnity,
    detect_multiple_indemnity_contracts,
    split_confirmed_additional_excluded,
)
from familycare_api.decisions.domain import ClaimCandidate, FactContext, FactValue

NOW = datetime(2026, 8, 25, tzinfo=UTC)


def _line(
    *,
    amount: str = "12500.50",
    currency: str = "KRW",
    category: str = "outpatient",
    coverage_category: str = "covered",
    confirmation_level: str = "user",
) -> ReceiptLine:
    return ReceiptLine(
        line_id=UUID(int=1),
        category=category,  # type: ignore[arg-type]
        coverage_category=coverage_category,  # type: ignore[arg-type]
        amount=Money(Decimal(amount), currency),
        confirmation_level=confirmation_level,  # type: ignore[arg-type]
    )


def _evidence() -> EvidenceRef:
    return EvidenceRef(
        evidence_id=UUID(int=20),
        document_version_id=UUID(int=21),
        extraction_id=UUID(int=22),
        content_sha256="a" * 64,
        physical_page=2,
        bbox=None,
        review_state="USER_CONFIRMED",
    )


def _calculation_rule(
    calculation: dict[str, object],
    *,
    rule_kind: str = "rate_amount",
    input_field_paths: tuple[str, ...] = ("Rider.insured_amount",),
) -> CoverageRuleVersion:
    evidence = _evidence()
    document: dict[str, object] = {
        "schema_version": RULE_SCHEMA_VERSION,
        "rule_kind": rule_kind,
        "required": True,
        "input_field_paths": list(input_field_paths),
        "calculation": calculation,
        "result_reason_code": "SYNTHETIC_CALCULATION",
        "evidence_ids": [str(evidence.evidence_id)],
    }
    return CoverageRuleVersion(
        id=UUID(int=30),
        coverage_rule_id=UUID(int=31),
        candidate_version_id=UUID(int=32),
        version_number=1,
        schema_version=RULE_SCHEMA_VERSION,
        rule_kind=rule_kind,  # type: ignore[arg-type]
        required=True,
        input_field_paths=input_field_paths,
        rule_document=document,
        result_reason_code="SYNTHETIC_CALCULATION",
        review_state="USER_CONFIRMED",
        executable=True,
        generator_version="synthetic-generator-v1",
        verifier_version="synthetic-verifier-v1",
        created_at=NOW,
        published_at=NOW,
        evidence=(evidence,),
    )


def _candidate(
    *, result: str = "MATCH", rider_type: str = "fixed", value: int = 40
) -> ClaimCandidate:
    return ClaimCandidate(
        id=UUID(int=value),
        rider_id=UUID(int=value + 1),
        rider_type=rider_type,
        aggregate_result=result,  # type: ignore[arg-type]
    )


def _indemnity_rule(
    *,
    deductible: str = "10000",
    rate: str = "0.8",
    limit: str = "100000",
) -> CoverageRuleVersion:
    return _calculation_rule(
        {
            "op": "round",
            "args": [
                {
                    "op": "min",
                    "args": [
                        {
                            "op": "multiply",
                            "args": [
                                {
                                    "op": "max",
                                    "args": [
                                        {
                                            "op": "subtract",
                                            "args": [
                                                {"field": "Receipt.confirmed_amount"},
                                                {"value": Decimal(deductible)},
                                            ],
                                        },
                                        {"value": Decimal("0")},
                                    ],
                                },
                                {"value": Decimal(rate)},
                            ],
                        },
                        {"value": Decimal(limit)},
                    ],
                }
            ],
            "rounding": "half_up",
        },
        rule_kind="rate_amount",
        input_field_paths=("Receipt.confirmed_amount",),
    )


def _fixed_context(
    *,
    insured_amount: object = Decimal("200000"),
    confirmation: str = "user",
    currency: str = "KRW",
) -> FactContext:
    return FactContext(
        medical_event={},
        policy={},
        rider={
            "insured_amount": FactValue(
                value=insured_amount,
                confirmation=confirmation,  # type: ignore[arg-type]
                evidence_ids=(),
            ),
            "currency": FactValue(value=currency, confirmation="user", evidence_ids=()),
        },
        claim_history={},
    )


def _error_code(call: object, *args: object, **kwargs: object) -> str:
    with pytest.raises(CalculationValidationError) as raised:
        call(*args, **kwargs)  # type: ignore[operator]
    return raised.value.reason_code


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        ("0", Decimal("0")),
        ("12500.50", Decimal("12500.50")),
        ("999999999999.999999", Decimal("999999999999.999999")),
    ],
)
def test_decimal_wire_values_remain_exact(wire: str, expected: Decimal) -> None:
    assert decimal_from_wire(wire) == expected


@pytest.mark.parametrize(
    ("value", "reason_code"),
    [
        (1, "AMOUNT_NOT_DECIMAL_STRING"),
        (1.1, "AMOUNT_NOT_DECIMAL_STRING"),
        ("NaN", "INVALID_AMOUNT"),
        ("Infinity", "INVALID_AMOUNT"),
        ("-0.01", "INVALID_AMOUNT"),
        ("1.0000001", "AMOUNT_SCALE_EXCEEDED"),
        ("1000000000000", "AMOUNT_PRECISION_EXCEEDED"),
        ("not-a-number", "INVALID_AMOUNT"),
    ],
)
def test_decimal_wire_rejects_unsafe_values(value: object, reason_code: str) -> None:
    assert _error_code(decimal_from_wire, value) == reason_code


@pytest.mark.parametrize("currency", ["krw", "KR", "KRW1", "K-W", ""])
def test_money_requires_three_uppercase_currency_letters(currency: str) -> None:
    assert _error_code(Money, Decimal("1"), currency) == "INVALID_CURRENCY"


@pytest.mark.parametrize(
    ("category", "coverage_category", "confirmation_level", "reason_code"),
    [
        ("travel", "covered", "user", "INVALID_RECEIPT_CATEGORY"),
        ("outpatient", "maybe", "user", "INVALID_COVERAGE_CATEGORY"),
        ("outpatient", "covered", "verified", "INVALID_CONFIRMATION_LEVEL"),
    ],
)
def test_receipt_line_uses_only_bounded_classifications(
    category: str,
    coverage_category: str,
    confirmation_level: str,
    reason_code: str,
) -> None:
    with pytest.raises(CalculationValidationError) as raised:
        validate_receipt_line(
            _line(
                category=category,
                coverage_category=coverage_category,
                confirmation_level=confirmation_level,
            )
        )

    assert raised.value.reason_code == reason_code


def test_receipt_line_validation_accepts_zero_without_treating_it_as_missing() -> None:
    validate_receipt_line(_line(amount="0"))


def test_receipt_line_rejects_fractional_subunits_the_database_cannot_store() -> None:
    assert _error_code(validate_receipt_line, _line(amount="1.001")) == (
        "RECEIPT_AMOUNT_SCALE_EXCEEDED"
    )


def test_round_money_applies_only_the_selected_rounding_boundary() -> None:
    value = Money(Decimal("10.125"), "KRW")

    assert round_money(value, "half_up", scale=2) == Money(Decimal("10.13"), "KRW")
    assert round_money(value, "half_even", scale=2) == Money(Decimal("10.12"), "KRW")
    assert round_money(value, "up", scale=2) == Money(Decimal("10.13"), "KRW")
    assert round_money(value, "down", scale=2) == Money(Decimal("10.12"), "KRW")


def test_round_money_rejects_unknown_rule_without_mutating_amount() -> None:
    value = Money(Decimal("10.125"), "KRW")

    assert _error_code(round_money, value, "bankers", scale=2) == "INVALID_ROUNDING"
    assert value.amount == Decimal("10.125")


def test_fixed_benefit_executes_nested_rate_and_rounding_with_complete_trace() -> None:
    rule = _calculation_rule(
        {
            "op": "round",
            "args": [
                {
                    "op": "multiply",
                    "args": [
                        {"field": "Rider.insured_amount"},
                        {"value": Decimal("0.333333")},
                    ],
                }
            ],
            "rounding": "half_up",
        }
    )

    result = calculate_fixed_benefit(_candidate(), rule, _fixed_context())

    assert result.kind == "fixed"
    assert result.status == "computed"
    assert result.confirmed == Money(Decimal("66667"), "KRW")
    assert [step.operation for step in result.steps] == ["multiply", "round"]
    assert [step.step_number for step in result.steps] == [1, 2]
    assert result.steps[-1].rounding_rule == "half_up"
    assert result.hold_reason_codes == ()


def test_fixed_benefit_supports_a_validated_fixed_literal_formula() -> None:
    rule = _calculation_rule(
        {
            "op": "add",
            "args": [{"value": Decimal("100000")}, {"value": Decimal("0")}],
        },
        rule_kind="fixed_amount",
    )

    result = calculate_fixed_benefit(_candidate(), rule, _fixed_context())

    assert result.status == "computed"
    assert result.confirmed == Money(Decimal("100000"), "KRW")
    assert result.steps[0].reason_code == "FIXED_ADD"


@pytest.mark.parametrize(
    ("candidate", "rule", "facts", "reason_code"),
    [
        (
            _candidate(result="UNKNOWN"),
            _calculation_rule(
                {"op": "add", "args": [{"value": Decimal("1")}, {"value": Decimal("0")}]}
            ),
            _fixed_context(),
            "CANDIDATE_NOT_MATCHED",
        ),
        (
            _candidate(),
            replace(
                _calculation_rule(
                    {
                        "op": "add",
                        "args": [{"value": Decimal("1")}, {"value": Decimal("0")}],
                    }
                ),
                executable=False,
            ),
            _fixed_context(),
            "RULE_NOT_EXECUTABLE",
        ),
        (
            _candidate(),
            _calculation_rule(
                {
                    "op": "multiply",
                    "args": [
                        {"field": "Rider.insured_amount"},
                        {"value": Decimal("0.5")},
                    ],
                }
            ),
            _fixed_context(confirmation="unconfirmed"),
            "MISSING_FIXED_INPUT",
        ),
        (
            _candidate(),
            _calculation_rule(
                {"op": "add", "args": [{"value": Decimal("1")}, {"value": Decimal("0")}]}
            ),
            _fixed_context(currency="krw"),
            "INVALID_CURRENCY",
        ),
    ],
)
def test_fixed_benefit_returns_value_free_unknown_for_unusable_inputs(
    candidate: ClaimCandidate,
    rule: CoverageRuleVersion,
    facts: FactContext,
    reason_code: str,
) -> None:
    result = calculate_fixed_benefit(candidate, rule, facts)

    assert result.status == "unknown"
    assert result.confirmed is None
    assert result.steps == ()
    assert result.hold_reason_codes == (reason_code,)


def test_fixed_benefit_rejects_overflow_instead_of_returning_zero() -> None:
    rule = _calculation_rule(
        {
            "op": "multiply",
            "args": [
                {"field": "Rider.insured_amount"},
                {"value": Decimal("2")},
            ],
        }
    )

    result = calculate_fixed_benefit(
        _candidate(),
        rule,
        _fixed_context(insured_amount=Decimal("999999999999.999999")),
    )

    assert result.status == "unknown"
    assert result.hold_reason_codes == ("AMOUNT_PRECISION_EXCEEDED",)


def test_fixed_benefit_requires_explicit_rounding_before_persistence() -> None:
    rule = _calculation_rule(
        {
            "op": "multiply",
            "args": [
                {"field": "Rider.insured_amount"},
                {"value": Decimal("0.333333")},
            ],
        }
    )

    result = calculate_fixed_benefit(
        _candidate(), rule, _fixed_context(insured_amount=Decimal("1"))
    )

    assert result.status == "unknown"
    assert result.confirmed is None
    assert result.steps == ()
    assert result.hold_reason_codes == ("ROUNDING_RULE_REQUIRED",)


def test_receipt_breakdown_separates_confirmed_additional_and_excluded() -> None:
    lines = (
        _line(amount="50000", coverage_category="covered", confirmation_level="user"),
        replace(
            _line(
                amount="20000",
                coverage_category="possible_excluded",
                confirmation_level="ai_structured",
            ),
            line_id=UUID(int=2),
        ),
        replace(
            _line(amount="5000", coverage_category="excluded", confirmation_level="user"),
            line_id=UUID(int=3),
        ),
        replace(
            _line(amount="3000", coverage_category="covered", confirmation_level="unconfirmed"),
            line_id=UUID(int=4),
        ),
    )

    breakdown = split_confirmed_additional_excluded(lines)

    assert breakdown.confirmed == Money(Decimal("50000"), "KRW")
    assert breakdown.additional == Money(Decimal("23000"), "KRW")
    assert breakdown.excluded == Money(Decimal("5000"), "KRW")


def test_indemnity_applies_deductible_rate_limit_and_rounding() -> None:
    result = calculate_indemnity(
        _candidate(rider_type="indemnity"),
        (_line(amount="200000"),),
        _indemnity_rule(limit="100000"),
        _fixed_context(),
    )

    assert result.kind == "indemnity"
    assert result.status == "computed"
    assert result.confirmed == Money(Decimal("100000"), "KRW")
    assert result.additional == Money(Decimal("0"), "KRW")
    assert result.excluded == Money(Decimal("0"), "KRW")
    assert result.deductible == Money(Decimal("10000"), "KRW")
    assert result.applied_rate == Decimal("0.8")
    assert result.applied_limit == Money(Decimal("100000"), "KRW")
    assert [step.operation for step in result.steps] == [
        "subtract",
        "max",
        "multiply",
        "min",
        "round",
    ]


def test_indemnity_preserves_partial_and_excluded_receipt_amounts() -> None:
    lines = (
        _line(amount="50000"),
        replace(
            _line(amount="20000", coverage_category="possible_excluded"),
            line_id=UUID(int=2),
        ),
        replace(
            _line(amount="5000", coverage_category="excluded"),
            line_id=UUID(int=3),
        ),
    )

    result = calculate_indemnity(
        _candidate(rider_type="indemnity"), lines, _indemnity_rule(), _fixed_context()
    )

    assert result.status == "partial"
    assert result.confirmed == Money(Decimal("32000"), "KRW")
    assert result.additional == Money(Decimal("20000"), "KRW")
    assert result.excluded == Money(Decimal("5000"), "KRW")
    assert result.hold_reason_codes == ("ADDITIONAL_RECEIPT_REVIEW_REQUIRED",)


def test_indemnity_without_receipts_is_unknown_not_zero() -> None:
    result = calculate_indemnity(
        _candidate(rider_type="indemnity"), (), _indemnity_rule(), _fixed_context()
    )

    assert result.status == "unknown"
    assert result.confirmed is None
    assert result.hold_reason_codes == ("RECEIPT_LINES_REQUIRED",)


def test_indemnity_currency_mismatch_is_unknown_not_a_partial_sum() -> None:
    lines = (
        _line(amount="50000", currency="KRW"),
        replace(_line(amount="10", currency="USD"), line_id=UUID(int=2)),
    )

    result = calculate_indemnity(
        _candidate(rider_type="indemnity"), lines, _indemnity_rule(), _fixed_context()
    )

    assert result.status == "unknown"
    assert result.confirmed is None
    assert result.hold_reason_codes == ("CURRENCY_MISMATCH",)


def test_multiple_indemnity_contracts_are_never_summed() -> None:
    first = _candidate(rider_type="indemnity", value=50)
    second = _candidate(rider_type="indemnity", value=60)
    fixed = _candidate(rider_type="fixed", value=70)

    result = detect_multiple_indemnity_contracts((first, fixed, second))

    assert result.allocation == "UNKNOWN"
    assert result.candidate_ids == (first.id, second.id)
    assert result.combined_amount is None


def test_no_match_indemnity_does_not_create_a_multiple_contract_hold() -> None:
    matched = _candidate(rider_type="indemnity", value=80)
    no_match = _candidate(rider_type="indemnity", result="NO_MATCH", value=90)

    result = detect_multiple_indemnity_contracts((matched, no_match))

    assert result.allocation == "SINGLE"
    assert result.candidate_ids == (matched.id,)
