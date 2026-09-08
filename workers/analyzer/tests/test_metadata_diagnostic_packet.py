"""Synthetic minimal diagnostic inputs and enum-only outputs."""

import pytest
from familycare_worker.metadata_diagnostic_packet import (
    MetadataBoundaryDiagnostic,
    MetadataDiagnosticError,
    build_diagnostic_packet,
)
from pydantic import ValidationError

_OUTPUT = {
    "reference_kind": "NAVIGATION",
    "body_kind": "TERMS_PROVISION",
    "parser_issue": "OVERBROAD_REFERENCE_CONTEXT",
    "needs_more_context": False,
}


def test_packet_has_only_two_minimized_text_fields() -> None:
    reference = (
        "목차\n피보험자: Family Member A; 이메일: synthetic@example.invalid; "
        "전화: 010-2345-6789; 증권번호: SYNTHETIC-001; 가입금액: 12345"
    )
    packet = build_diagnostic_packet(
        reference,
        "제7조 지급 예시\nFamily Member A의 합성 가입금액 67890원",
        sensitive_terms=("Family Member A",),
    )
    assert set(packet) == {"reference_context", "operative_context"}
    combined = " ".join(packet.values())
    for value in (
        "Family Member A",
        "synthetic@example.invalid",
        "010-2345-6789",
        "SYNTHETIC-001",
        "12345",
        "67890",
    ):
        assert value not in combined
    assert "[REDACTED]" in combined and "[NUMBER]" in combined


def test_urls_and_absolute_paths_are_removed_before_number_masking() -> None:
    for value in (
        "https://synthetic.example.invalid/private?id=987",
        "www.synthetic.example.invalid/private",
        "/synthetic/private/member/document.pdf",
        '"/synthetic/private folder/document.pdf"',
        r"C:\synthetic\private\document.pdf",
        r"\\synthetic-server\private\document.pdf",
    ):
        packet = build_diagnostic_packet("목차\n" + value, "합성 본문", sensitive_terms=())
        assert "synthetic" not in packet["reference_context"]
        assert "[REDACTED]" in packet["reference_context"]


def test_full_source_redaction_precedes_the_output_cutoff() -> None:
    secret = "Synthetic Sensitive Person"
    for source in (
        "가" * 1195 + secret + " 끝",
        "가" * 1195 + "https://synthetic.example.invalid/private/secret",
        "가" * 1190 + " 피보험자: " + secret,
    ):
        packet = build_diagnostic_packet(source, source, sensitive_terms=(secret,))
        assert all(0 < len(value) <= 1200 for value in packet.values())
        assert all("Synthetic" not in value and "https" not in value for value in packet.values())


def test_input_limits_are_independent_of_output_limits() -> None:
    packet = build_diagnostic_packet("가" * 65536, "나" * 65536, sensitive_terms=())
    assert packet == {"reference_context": "가" * 1200, "operative_context": "나" * 1200}
    with pytest.raises(MetadataDiagnosticError, match="^METADATA_DIAGNOSTIC_INVALID$"):
        build_diagnostic_packet("가" * 65537, "합성 본문", sensitive_terms=())


def test_invalid_inputs_raise_only_a_fixed_error() -> None:
    for invalid in (None, 123, "", " \n\t", ["synthetic-private-value"]):
        with pytest.raises(MetadataDiagnosticError, match="^METADATA_DIAGNOSTIC_INVALID$"):
            build_diagnostic_packet(invalid, "합성 본문", sensitive_terms=())
        with pytest.raises(MetadataDiagnosticError, match="^METADATA_DIAGNOSTIC_INVALID$"):
            build_diagnostic_packet("합성 문맥", invalid, sensitive_terms=())
    with pytest.raises(MetadataDiagnosticError, match="^METADATA_DIAGNOSTIC_INVALID$"):
        build_diagnostic_packet("합성 문맥", "합성 본문", sensitive_terms=("x",))


def test_output_is_frozen_and_contains_only_declared_enums() -> None:
    model = MetadataBoundaryDiagnostic.model_validate(_OUTPUT)
    assert model.model_dump() == _OUTPUT
    with pytest.raises(ValidationError):
        model.needs_more_context = True
    for change in (
        {"reference_kind": "Here is an explanation"},
        {"body_kind": "Synthetic source quote"},
        {"parser_issue": "A free-form reason"},
        {"quote": "Synthetic source quote"},
    ):
        with pytest.raises(ValidationError):
            MetadataBoundaryDiagnostic.model_validate({**_OUTPUT, **change})


def test_output_needs_more_context_requires_a_real_boolean() -> None:
    for invalid in ("false", "true", 0, 1, None):
        with pytest.raises(ValidationError):
            MetadataBoundaryDiagnostic.model_validate({**_OUTPUT, "needs_more_context": invalid})


def test_output_schema_forbids_extras_and_requires_every_field() -> None:
    schema = MetadataBoundaryDiagnostic.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"]) == set(_OUTPUT)
    assert schema["properties"]["needs_more_context"]["type"] == "boolean"
    for name in ("reference_kind", "body_kind", "parser_issue"):
        assert schema["properties"][name]["enum"]
    with pytest.raises(ValidationError):
        MetadataBoundaryDiagnostic.model_validate({"reference_kind": "NAVIGATION"})
