"""Privacy regression checks for receipt inputs and calculation traces."""

from __future__ import annotations

import inspect

from familycare_api.decisions import (
    calculation_repository,
    calculation_schemas,
    calculation_service,
    calculations,
)

_FORBIDDEN_FIELDS = {
    "absolute_path",
    "diagnosis",
    "diagnosis_text",
    "document_text",
    "file_id",
    "file_path",
    "medical_text",
    "ocr_text",
    "password",
    "pdf_bytes",
    "raw_note",
    "raw_text",
    "source_path",
}


def test_receipt_requests_and_calculation_responses_have_no_private_fields() -> None:
    models = (
        calculation_schemas.ReceiptLineCreateRequest,
        calculation_schemas.ReceiptLineUpdateRequest,
        calculation_schemas.ReceiptLineDeleteRequest,
        calculation_schemas.ReceiptLineResponse,
        calculation_schemas.ReceiptLinesResponse,
        calculation_schemas.BenefitCalculationResponse,
    )

    for model in models:
        assert not _FORBIDDEN_FIELDS & set(model.model_fields)


def test_calculation_runtime_does_not_log_or_open_receipt_documents() -> None:
    source = "\n".join(
        inspect.getsource(module)
        for module in (
            calculation_repository,
            calculation_service,
            calculations,
        )
    ).lower()

    assert "logger." not in source
    assert "logging." not in source
    assert "open(" not in source
    assert "pdf_bytes" not in source
    assert "ocr_text" not in source
    assert "diagnosis_text" not in source


def test_receipt_note_is_a_bounded_reason_code_not_free_text() -> None:
    field = calculation_schemas.ReceiptLineCreateRequest.model_fields["note_code"]
    schema = calculation_schemas.ReceiptLineCreateRequest.model_json_schema()

    assert field.annotation is not str
    note_schema = schema["properties"]["note_code"]
    assert "anyOf" in note_schema
    assert any(item.get("pattern") == r"^[A-Z][A-Z0-9_]{0,63}$" for item in note_schema["anyOf"])
