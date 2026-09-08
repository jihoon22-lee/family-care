"""Independent source interpretation never promotes a matching number alone."""

import pytest
from familycare_api.terms_knowledge.source_meaning import observe_statement


def test_daily_benefit_basis_and_rounding_are_explicit() -> None:
    assert observe_statement(
        "For each payable admission day, pay the insured amount in KRW; "
        "multiply first, then round the total half up to whole currency units."
    ) == {
        "kind": "calculation",
        "mode": "daily",
        "basis": "insured_amount_per_payable_day",
        "currency": "KRW",
        "rounding": "half_up",
    }
    assert (
        observe_statement(
            "지급 대상 입원일수와 보험가입금액을 곱하여 KRW로 지급하며, "
            "곱한 총액을 통화 정수 단위로 반올림합니다."
        )["basis"]
        == "insured_amount_per_payable_day"
    )


@pytest.mark.parametrize(
    "text",
    [
        "The premium is KRW 50, rounded half up to whole currency units.",
        (
            "For each payable admission day, pay the premium in KRW, "
            "rounded half up to whole currency units."
        ),
        "For each payable admission day, pay the insured amount in KRW.",
        "The benefit is KRW 50, rounded half up to whole currency units, unless excluded.",
        "Example: The benefit is KRW 50, rounded half up to whole currency units.",
        "The benefit is KRW -50, rounded half up to whole currency units.",
        "Exclude the first 2 admission days unless a special exception applies.",
        "The maximum is 10 payable occurrences.",
        "If no records exist, assume 0 prior occurrences.",
        "보험료는 50원입니다.",
    ],
)
def test_partial_numbers_negation_examples_or_unsupported_exceptions_are_not_rules(
    text: str,
) -> None:
    assert observe_statement(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Exclude the first 2 admission days.",
            {"kind": "footnote", "effect": "initial_excluded_days", "days": 2},
        ),
        (
            "최초 입원 2일은 지급일수에서 제외합니다.",
            {"kind": "footnote", "effect": "initial_excluded_days", "days": 2},
        ),
        (
            "The maximum is 10 payable days.",
            {
                "kind": "limit",
                "measure": "payable_days",
                "value": "10",
                "unit": "days",
                "currency": None,
            },
        ),
        (
            "지급일수는 최대 10일입니다.",
            {
                "kind": "limit",
                "measure": "payable_days",
                "value": "10",
                "unit": "days",
                "currency": None,
            },
        ),
        (
            "The maximum benefit is KRW 1000.",
            {
                "kind": "limit",
                "measure": "maximum_amount",
                "value": "1000",
                "unit": "amount",
                "currency": "KRW",
            },
        ),
        (
            "The benefit is KRW 50, rounded half up to whole currency units.",
            {
                "kind": "calculation",
                "mode": "fixed",
                "amount": "50",
                "currency": "KRW",
                "rounding": "half_up",
            },
        ),
        (
            "Pay 0.25 of the insured amount in KRW, rounded down to whole currency units.",
            {
                "kind": "calculation",
                "mode": "insured_ratio",
                "basis": "insured_amount",
                "ratio": "0.25",
                "currency": "KRW",
                "rounding": "down",
            },
        ),
        (
            (
                "Medical event classification uses synthetic-classification "
                "version edition-1: class-a, class-b."
            ),
            {
                "kind": "classification",
                "field": "MedicalEvent.classification",
                "code_system": "synthetic-classification",
                "code_version": "edition-1",
                "codes": ["class-a", "class-b"],
            },
        ),
        (
            "Eligible admission days range from 1 to 10 inclusive.",
            {
                "kind": "condition",
                "rule_kind": "eligibility",
                "field": "MedicalEvent.admission_days",
                "operator": "range",
                "value": [1, 10],
                "unit": "days",
            },
        ),
        (
            "The waiting period is 90 days from contract start.",
            {
                "kind": "condition",
                "rule_kind": "temporal",
                "field": "PolicyContract.contract_start",
                "operator": "days_since",
                "value": 90,
                "unit": "days",
            },
        ),
        (
            "Payment requires fewer than 2 prior occurrences.",
            {
                "kind": "condition",
                "rule_kind": "frequency",
                "field": "ClaimHistory.counted_occurrence",
                "operator": "count_below",
                "value": 2,
                "unit": "occurrences",
            },
        ),
    ],
)
def test_full_statement_roles_preserve_numbers_units_codes_and_versions(
    text: str, expected: dict
) -> None:
    assert observe_statement(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        (
            "For each payable admission day, pay the insured amount in KRW, "
            "rounded half up to whole currency units."
        ),
        "지급 대상 입원일마다 보험가입금액을 KRW로 지급하며, 통화 정수 단위로 반올림합니다.",
    ],
)
def test_ambiguous_or_daily_rounding_does_not_become_final_total_rounding(text):
    assert observe_statement(text) is None


def test_deductible_requires_its_amount_currency_floor_and_calculation_stage():
    assert observe_statement(
        "Deduct KRW 50 from the gross benefit before applying "
        "the amount cap and rounding; floor at zero."
    ) == {
        "kind": "deductible",
        "amount": "50",
        "currency": "KRW",
        "stage": "before_amount_cap_and_rounding",
    }
    assert observe_statement("Deduct KRW 50.") is None
    assert observe_statement("Deduct KRW 50 after applying the cap and rounding.") is None
