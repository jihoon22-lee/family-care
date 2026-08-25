"""Focused HTTP schema tests for receipt inputs and calculation results."""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID

import pytest
from familycare_api.decisions.calculation_schemas import (
    BenefitCalculationResponse,
    CalculationStepResponse,
    ReceiptLineCreateRequest,
    ReceiptLineDeleteRequest,
    ReceiptLineResponse,
    ReceiptLineUpdateRequest,
)
from familycare_api.decisions.calculations import (
    BenefitCalculationResult,
    CalculationStep,
    Money,
    ReceiptLine,
)
from pydantic import ValidationError

LINE_ID = UUID("00000000-0000-4000-8000-000000000101")
CALCULATION_ID = UUID("00000000-0000-4000-8000-000000000201")
CLAIM_CANDIDATE_ID = UUID("00000000-0000-4000-8000-000000000301")
RULE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000401")


def _receipt_fields() -> dict[str, object]:
    return {
        "category": "outpatient",
        "coverage_category": "covered",
        "amount": "12500.50",
        "currency": "KRW",
        "confirmation_level": "user",
        "note_code": "USER_ENTERED",
    }


def test_receipt_create_is_frozen_strict_and_keeps_amount_as_wire_string() -> None:
    request = ReceiptLineCreateRequest(**_receipt_fields())

    assert request.amount == "12500.50"
    assert json.loads(request.model_dump_json())["amount"] == "12500.50"
    assert request.model_config["extra"] == "forbid"
    assert request.model_config["frozen"] is True

    with pytest.raises(ValidationError):
        ReceiptLineCreateRequest(**{**_receipt_fields(), "amount": Decimal("12500.50")})
    with pytest.raises(ValidationError):
        ReceiptLineCreateRequest(**{**_receipt_fields(), "currency": "krw"})
    with pytest.raises(ValidationError):
        ReceiptLineCreateRequest(**{**_receipt_fields(), "amount": "12500.001"})


@pytest.mark.parametrize(
    "field",
    [
        "household_space_id",
        "confirmed_amount",
        "applied_rate",
        "rule_version_id",
        "file_path",
        "note",
        "raw_notes",
    ],
)
def test_receipt_create_rejects_scope_authority_and_private_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        ReceiptLineCreateRequest(**{**_receipt_fields(), field: "synthetic-value"})


def test_receipt_update_and_delete_require_positive_expected_version() -> None:
    update = ReceiptLineUpdateRequest(expected_version=2, amount="13000.00")
    delete = ReceiptLineDeleteRequest(expected_version=2)

    assert update.expected_version == 2
    assert delete.expected_version == 2

    with pytest.raises(ValidationError):
        ReceiptLineUpdateRequest(amount="13000.00")
    with pytest.raises(ValidationError):
        ReceiptLineUpdateRequest(expected_version=2)
    with pytest.raises(ValidationError):
        ReceiptLineDeleteRequest(expected_version=0)


def test_receipt_response_serializes_domain_decimal_as_string_without_scope() -> None:
    line = ReceiptLine(
        line_id=LINE_ID,
        category="outpatient",
        coverage_category="covered",
        amount=Money(Decimal("12500.50"), "KRW"),
        confirmation_level="user",
        note_code="USER_ENTERED",
        version=3,
    )

    response = ReceiptLineResponse.from_domain(line)
    payload = json.loads(response.model_dump_json())

    assert response.id == LINE_ID
    assert payload["amount"] == "12500.50"
    assert payload["currency"] == "KRW"
    assert payload["version"] == 3
    assert "household_space_id" not in payload
    assert "file_path" not in payload
    assert "raw_notes" not in payload


def test_calculation_response_serializes_all_money_and_steps_as_wire_strings() -> None:
    result = BenefitCalculationResult(
        kind="indemnity",
        status="partial",
        confirmed=Money(Decimal("1000.25"), "KRW"),
        additional=Money(Decimal("250.00"), "KRW"),
        excluded=Money(Decimal("10.00"), "KRW"),
        deductible=Money(Decimal("100.00"), "KRW"),
        applied_rate=Decimal("0.8"),
        applied_limit=Money(Decimal("5000.00"), "KRW"),
        steps=(
            CalculationStep(
                step_number=1,
                operation="subtract",
                input_amount=Money(Decimal("1100.25"), "KRW"),
                output_amount=Money(Decimal("1000.25"), "KRW"),
                rounding_rule="half_up",
                reason_code="INDEMNITY_DEDUCTIBLE",
            ),
        ),
        hold_reason_codes=("ADDITIONAL_CONFIRMATION_REQUIRED",),
    )

    response = BenefitCalculationResponse.from_result(
        result,
        calculation_id=CALCULATION_ID,
        claim_candidate_id=CLAIM_CANDIDATE_ID,
        rule_version_id=RULE_VERSION_ID,
        engine_version="calculation-engine-v1",
        evidence_ids=(RULE_VERSION_ID,),
        version=1,
    )
    payload = json.loads(response.model_dump_json())

    assert payload["schema_version"] == "1"
    assert payload["confirmed"] == {"amount": "1000.25", "currency": "KRW"}
    assert payload["applied_rate"] == "0.8"
    assert payload["steps"][0]["input_amount"] == {"amount": "1100.25", "currency": "KRW"}
    assert payload["hold_reason_codes"] == ["ADDITIONAL_CONFIRMATION_REQUIRED"]
    assert "household_space_id" not in payload
    assert "file_path" not in payload
    assert "raw_notes" not in payload


def test_calculation_step_response_is_strict_and_bounded() -> None:
    step = CalculationStepResponse(
        step_number=1,
        operation="add",
        input_amount=None,
        output_amount={"amount": "10.00", "currency": "KRW"},
        rounding_rule=None,
        reason_code="FIXED_ADD",
    )

    assert step.output_amount is not None
    assert step.output_amount.amount == "10.00"

    with pytest.raises(ValidationError):
        CalculationStepResponse(
            step_number=1,
            operation="add",
            input_amount=None,
            output_amount={"amount": "-1.00", "currency": "KRW"},
            rounding_rule=None,
            reason_code="FIXED_ADD",
        )
    with pytest.raises(ValidationError):
        CalculationStepResponse(
            step_number=1,
            operation="add",
            input_amount=None,
            output_amount={"amount": "10.00", "currency": "krw"},
            rounding_rule=None,
            reason_code="FIXED_ADD",
        )


def test_calculation_response_requires_persisted_rule_and_evidence_lineage() -> None:
    with pytest.raises(ValidationError):
        BenefitCalculationResponse(kind="fixed", status="unknown")
