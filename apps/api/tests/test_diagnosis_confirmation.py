"""Explicit confirmation is distinct from a diagnosis topic, plan or another person."""

from dataclasses import replace
from datetime import date

import pytest
from familycare_api.decisions.schemas import MedicalEventUpdateRequest
from familycare_api.guidance.event_facts import build_event_facts
from familycare_api.guidance.interpretation import interpret_situation
from pydantic import ValidationError

from apps.api.tests.test_private_knowledge_facts import _event


@pytest.mark.parametrize(
    "text",
    [
        "진단받았습니다. 확정진단은 받지 않았습니다.",
        "확정진단을 받았습니다. 확정진단은 받지 않았습니다.",
    ],
)
def test_joined_confirmation_denial_prevents_an_earlier_positive_from_surviving(text):
    read = build_event_facts(_event(situation=text), ())
    fact = read.context.get("MedicalEvent.diagnosis_confirmed")
    assert fact is not None and fact.value is None


def _confirmation(text: str, **kwargs):
    result = interpret_situation(text, **kwargs)
    found = [item for item in result.facts if item.field_path == "MedicalEvent.diagnosis_confirmed"]
    assert len(found) == 1
    return found[0]


@pytest.mark.parametrize(
    "text",
    [
        "진단받았습니다.",
        "확정 진단을 받았습니다.",
        "확정진단을 받았습니다.",
        "확진되었습니다.",
        "I was diagnosed.",
        "The diagnosis was confirmed.",
    ],
)
def test_explicit_diagnosis_confirmation_has_original_spans(text):
    fact = _confirmation(text)
    assert fact.value is True and fact.state == "CONFIRMED"
    assert fact.activity is None and fact.provenance == "EXPLICIT_LOCAL"
    assert fact.spans and all(0 <= span.start < span.end <= len(text) for span in fact.spans)
    context = build_event_facts(_event(text)).context
    assert context.get("MedicalEvent.diagnosis_confirmed").value is True
    assert context.get("MedicalEvent.diagnosis_confirmed").is_trusted
    assert context.get("MedicalEvent.diagnosis_code") is None


@pytest.mark.parametrize(
    "text",
    [
        "진단받지 않았습니다.",
        "확정진단은 받지 않았습니다.",
        "확진되지 않았습니다.",
        "The diagnosis was not confirmed.",
    ],
)
def test_explicit_nonconfirmation_is_false(text):
    fact = _confirmation(text)
    assert fact.value is False and fact.state == "CONFIRMED"


@pytest.mark.parametrize(
    "text",
    [
        "진단 여부는 모르겠습니다.",
        "확진 가능성이 있습니다.",
        "진단 예정입니다.",
        "진단 보험금 문의입니다.",
        "진단 상담을 받았습니다.",
        "Diagnosis is planned.",
        "I may have been diagnosed.",
    ],
)
def test_uncertain_planned_or_discussed_diagnosis_never_becomes_confirmed(text):
    fact = _confirmation(text)
    assert fact.value is None and fact.state in {"UNKNOWN", "SCENARIO"}


def test_diagnosis_contradiction_remains_unknown():
    fact = _confirmation("진단받았습니다. 진단받지 않았습니다.")
    assert fact.value is None and fact.state == "UNKNOWN"


def test_diagnosis_does_not_cross_other_member_or_event_date():
    other = _confirmation(
        "Family Member B 진단받았습니다.",
        selected_subject_terms=("Family Member A",),
        other_subject_terms=("Family Member B",),
    )
    assert other.value is None and other.state == "UNKNOWN"
    historical = _confirmation("2026-01-01 진단받았습니다.", event_date=date(2026, 6, 1))
    assert historical.value is None and historical.state == "UNKNOWN"


def test_user_confirmation_override_is_preserved_over_local_text():
    event = replace(
        _event("진단받았습니다."),
        structured_facts=(
            {
                "field_id": "diagnosis_confirmed",
                "value": False,
                "source": "user",
                "state": "confirmed",
            },
        ),
    )
    value = build_event_facts(event).context.get("MedicalEvent.diagnosis_confirmed")
    assert value.value is False and value.is_trusted


@pytest.mark.parametrize("value", [True, False, None])
def test_confirmation_can_be_submitted_as_a_boolean_or_cleared(value):
    update = MedicalEventUpdateRequest.model_validate(
        {
            "expected_version": 1,
            "structured_facts": [{"field_id": "diagnosis_confirmed", "value": value}],
        }
    )
    assert update.structured_facts[0].value is value


@pytest.mark.parametrize("value", ["true", "false", 1, 0])
def test_confirmation_rejects_coercible_non_boolean_values(value):
    with pytest.raises(ValidationError):
        MedicalEventUpdateRequest.model_validate(
            {
                "expected_version": 1,
                "structured_facts": [{"field_id": "diagnosis_confirmed", "value": value}],
            }
        )


@pytest.mark.parametrize(
    "text",
    [
        "진단서 발급을 받지 않았습니다.",
        "진단 검사를 받지 않았습니다.",
        "진단 상담을 받지 않았습니다.",
    ],
)
def test_negated_related_service_is_not_a_denied_diagnosis(text):
    result = interpret_situation(text)
    assert not any(
        fact.field_path == "MedicalEvent.diagnosis_confirmed" and fact.state == "CONFIRMED"
        for fact in result.facts
    )


def test_ai_proposal_does_not_become_user_confirmed_diagnosis():
    event = replace(
        _event("진단 여부는 모릅니다."),
        structured_facts=(
            {
                "field_id": "diagnosis_confirmed",
                "value": True,
                "source": "ai",
                "state": "confirmed",
            },
        ),
    )
    fact = build_event_facts(event).context.get("MedicalEvent.diagnosis_confirmed")
    assert not fact.is_trusted
