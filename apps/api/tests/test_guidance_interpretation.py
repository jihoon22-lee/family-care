"""Synthetic local statements keep activity, subject, time and monetary roles distinct."""

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import pytest
from familycare_api.guidance.interpretation import (
    InterpretationError,
    guard_normalizer_match,
    interpret_situation,
)


def fact(result, path, *, activity=None):
    found = [
        item
        for item in result.facts
        if item.field_path == path and (activity is None or item.activity == activity)
    ]
    assert len(found) == 1
    return found[0]


@pytest.mark.parametrize(
    "text,path,value,activity",
    [
        ("5일간 입원했습니다.", "MedicalEvent.admission", True, "admission"),
        ("외래 진료를 받았습니다.", "MedicalEvent.outpatient", True, "outpatient"),
        ("통원 치료를 받았어요.", "MedicalEvent.outpatient", True, "outpatient"),
        ("수술을 받았습니다.", "MedicalEvent.performed", True, "surgery"),
        ("I was admitted for 5 days.", "MedicalEvent.admission", True, "admission"),
        ("I received outpatient treatment.", "MedicalEvent.outpatient", True, "outpatient"),
        ("I underwent surgery.", "MedicalEvent.performed", True, "surgery"),
    ],
)
def test_explicit_performed_activity_has_source_bound_fact(text, path, value, activity):
    item = fact(interpret_situation(text), path)
    assert item.value is value and item.state == "CONFIRMED"
    assert item.provenance == "EXPLICIT_LOCAL" and item.activity == activity
    assert item.spans and all(0 <= span.start < span.end <= len(text) for span in item.spans)


@pytest.mark.parametrize(
    "text", ["수술을 받지 않았습니다.", "수술하지 않았어요.", "I did not have surgery."]
)
def test_explicit_negation_is_activity_scoped(text):
    item = fact(interpret_situation(text), "MedicalEvent.performed")
    assert item.state == "CONFIRMED" and item.value is False and item.activity == "surgery"


@pytest.mark.parametrize(
    "text", ["다음 주 수술 예정입니다.", "수술을 받을 예정입니다.", "Surgery is planned."]
)
def test_planned_activity_never_becomes_performed_fact(text):
    item = fact(interpret_situation(text), "MedicalEvent.performed")
    assert item.state == "SCENARIO" and item.value is None
    assert "LOCAL_ACTIVITY_PLANNED" in item.reason_codes


@pytest.mark.parametrize(
    "text",
    [
        "수술 보험금이 궁금합니다.",
        "수술 여부는 모릅니다.",
        "Surgery coverage?",
        "Admission benefit is 100 KRW.",
    ],
)
def test_activity_keyword_alone_is_not_confirmed(text):
    result = interpret_situation(text)
    assert not any(item.state == "CONFIRMED" for item in result.facts)
    assert not any(item.field_path.endswith("code") for item in result.facts)


def test_negated_surgery_does_not_negate_independent_admission_or_its_days():
    result = interpret_situation("수술하지 않았고 5일간 입원했습니다.")
    assert fact(result, "MedicalEvent.performed").value is False
    assert fact(result, "MedicalEvent.performed").activity == "surgery"
    assert fact(result, "MedicalEvent.admission").value is True
    assert fact(result, "MedicalEvent.admission_days").value == 5


def test_planned_surgery_does_not_change_completed_outpatient_activity():
    result = interpret_situation("외래 진료를 받았습니다. 수술은 예정입니다.")
    assert fact(result, "MedicalEvent.outpatient").state == "CONFIRMED"
    assert fact(result, "MedicalEvent.performed").state == "SCENARIO"


def test_conflicting_same_activity_remains_unknown_instead_of_picking_one():
    item = fact(interpret_situation("수술했습니다. 수술하지 않았습니다."), "MedicalEvent.performed")
    assert item.state == "UNKNOWN" and item.value is None and len(item.spans) == 2
    assert "LOCAL_FACT_CONFLICT" in item.reason_codes


def test_subject_comparison_and_inherited_other_subject_do_not_mint_selected_facts():
    result = interpret_situation(
        "Family Member B had surgery. Was admitted for 5 days. "
        "Family Member A received outpatient treatment.",
        selected_subject_terms=("Family Member A",),
        other_subject_terms=("Family Member B",),
    )
    assert fact(result, "MedicalEvent.performed").state == "UNKNOWN"
    assert fact(result, "MedicalEvent.admission").state == "UNKNOWN"
    assert fact(result, "MedicalEvent.admission_days").state == "UNKNOWN"
    assert fact(result, "MedicalEvent.outpatient").state == "CONFIRMED"


def test_unregistered_family_role_is_not_assumed_to_be_selected_person():
    item = fact(interpret_situation("어머니가 수술을 받았습니다."), "MedicalEvent.performed")
    assert item.state == "UNKNOWN" and "LOCAL_SUBJECT_UNRESOLVED" in item.reason_codes


def test_explicit_selected_alias_is_supported_with_korean_case_particle():
    result = interpret_situation(
        "합성대상가가 수술을 받았습니다.", selected_subject_terms=("합성대상가",)
    )
    assert fact(result, "MedicalEvent.performed").state == "CONFIRMED"


def test_two_subjects_in_unsplit_clause_remain_unknown():
    result = interpret_situation(
        "Family Member A compared with Family Member B had surgery.",
        selected_subject_terms=("Family Member A",),
        other_subject_terms=("Family Member B",),
    )
    assert fact(result, "MedicalEvent.performed").state == "UNKNOWN"


def test_other_subject_term_substring_does_not_match_selected_alias():
    result = interpret_situation(
        "Admin AB had surgery.",
        selected_subject_terms=("Admin AB",),
        other_subject_terms=("Admin A",),
    )
    assert fact(result, "MedicalEvent.performed").state == "CONFIRMED"


def test_old_event_is_isolated_from_explicit_current_activity():
    result = interpret_situation("작년에 수술했습니다. 이번에는 3일간 입원했습니다.")
    assert fact(result, "MedicalEvent.performed").state == "UNKNOWN"
    assert fact(result, "MedicalEvent.admission").state == "CONFIRMED"
    assert fact(result, "MedicalEvent.admission_days").value == 3


def test_explicit_event_date_selects_its_clause_without_using_wall_clock():
    text = "2025-01-02에 수술했습니다. 2026-09-08에 3일간 입원했습니다."
    result = interpret_situation(text, event_date=date(2026, 9, 8))
    assert fact(result, "MedicalEvent.performed").state == "UNKNOWN"
    assert fact(result, "MedicalEvent.admission").state == "CONFIRMED"
    assert fact(result, "MedicalEvent.event_date").value == date(2026, 9, 8)


def test_multiple_event_dates_without_selected_date_are_explicitly_unresolved():
    result = interpret_situation("2025-01-02 수술했습니다. 2026-09-08 입원했습니다.")
    assert fact(result, "MedicalEvent.event_date").state == "UNKNOWN"
    assert not any(item.state == "CONFIRMED" for item in result.facts)


@pytest.mark.parametrize("text", ["2026-09-08에 입원했습니다.", "2026년 9월 8일에 입원했습니다."])
def test_unambiguous_full_event_date_is_retained(text):
    assert fact(interpret_situation(text), "MedicalEvent.event_date").value == date(2026, 9, 8)


@pytest.mark.parametrize(
    "text", ["09/08에 입원했습니다.", "03/04/2026에 입원했습니다.", "2026-02-30에 입원했습니다."]
)
def test_ambiguous_or_invalid_dates_do_not_get_default_year_or_order(text):
    result = interpret_situation(text)
    assert not any(
        item.field_path.endswith("date") and item.state == "CONFIRMED" for item in result.facts
    )
    assert any("LOCAL_DATE_UNRESOLVED" in issue.reason_codes for issue in result.issues)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("5일간 입원했습니다.", 5),
        ("입원 기간은 12일입니다.", 12),
        ("I was admitted for 7 days.", 7),
    ],
)
def test_admission_days_need_explicit_activity_and_day_unit(text, expected):
    assert fact(interpret_situation(text), "MedicalEvent.admission_days").value == expected


@pytest.mark.parametrize(
    "text",
    [
        "입원 일당은 5만원입니다.",
        "5일 뒤 입원 예정입니다.",
        "입원한 지 5일인지 모르겠습니다.",
        "입원 기간은 5입니다.",
        "5~10일간 입원했습니다.",
        "-5일간 입원했습니다.",
    ],
)
def test_days_are_not_money_delay_or_uncertain_quantity(text):
    assert not any(
        item.field_path == "MedicalEvent.admission_days" and item.state == "CONFIRMED"
        for item in interpret_situation(text).facts
    )


@pytest.mark.parametrize(
    "text,path,expected,state,currency",
    [
        (
            "진료비로 120,000원을 결제했습니다.",
            "Receipt.billed_amount",
            "120000",
            "CONFIRMED",
            "KRW",
        ),
        ("실제 진료비는 12만원입니다.", "Receipt.billed_amount", "120000", "CONFIRMED", "KRW"),
        (
            "Billed medical cost was USD 125.50.",
            "Receipt.billed_amount",
            "125.50",
            "CONFIRMED",
            "USD",
        ),
        ("예상 진료비는 10만원입니다.", "Receipt.estimated_amount", "100000", "SCENARIO", "KRW"),
        (
            "Estimated medical cost: KRW 100000.",
            "Receipt.estimated_amount",
            "100000",
            "SCENARIO",
            "KRW",
        ),
    ],
)
def test_cost_roles_remain_distinct_from_eligible_expense_and_benefit(
    text, path, expected, state, currency
):
    result = interpret_situation(text)
    item = fact(result, path)
    assert item.value == Decimal(expected) and item.state == state and item.currency == currency
    assert not any(
        item.field_path in {"Receipt.covered_amount", "Rider.insured_amount"}
        for item in result.facts
    )


@pytest.mark.parametrize(
    "text",
    [
        "가입금액은 100만원입니다.",
        "보험료로 10만원을 냈습니다.",
        "보험금 100만원을 받았습니다.",
        "진료비 100입니다.",
        "진료비는 약 10만원에서 20만원입니다.",
    ],
)
def test_policy_money_unqualified_money_and_unsupported_ranges_are_not_billed_cost(text):
    assert not any(
        item.state == "CONFIRMED" and item.field_path.startswith("Receipt.")
        for item in interpret_situation(text).facts
    )


@pytest.mark.parametrize(
    "text,token,state",
    [
        ("수술하지 않았고 입원했습니다.", "수술", "NEGATED"),
        ("수술하지 않았고 입원했습니다.", "입원", "AFFIRMED"),
        ("수술 예정입니다. 외래 진료를 받았습니다.", "수술", "PLANNED"),
        ("수술 예정입니다. 외래 진료를 받았습니다.", "외래", "AFFIRMED"),
        ("No surgery, but admitted for 3 days.", "admitted", "AFFIRMED"),
        ("작년에 수술했습니다. 이번에는 입원했습니다.", "수술", "UNKNOWN"),
    ],
)
def test_normalizer_guard_uses_the_matched_clause(text, token, state):
    start = text.index(token)
    guarded = guard_normalizer_match(text, start, start + len(token))
    assert guarded.state == state and guarded.span.start == start
    assert guarded.span.end == start + len(token)


def test_scope_guard_checks_subject_and_selected_event_date():
    text = "2025-01-02 Family Member B had surgery. 2026-09-08 Family Member A was admitted."
    start = text.index("surgery")
    guarded = guard_normalizer_match(
        text,
        start,
        start + len("surgery"),
        event_date=date(2026, 9, 8),
        selected_subject_terms=("Family Member A",),
        other_subject_terms=("Family Member B",),
    )
    assert guarded.state == "UNKNOWN"


def test_unknown_region_does_not_erase_independent_known_activity():
    result = interpret_situation("수술 여부는 모릅니다. 5일간 입원했습니다.")
    assert fact(result, "MedicalEvent.performed").state == "UNKNOWN"
    assert fact(result, "MedicalEvent.admission").value is True
    assert fact(result, "MedicalEvent.admission_days").value == 5


def test_result_is_immutable_and_repr_does_not_retain_raw_statement_or_aliases():
    text = "Family Member A had surgery. Billed medical cost was KRW 12345."
    result = interpret_situation(text, selected_subject_terms=("Family Member A",))
    guarded = guard_normalizer_match(text, text.index("surgery"), text.index("surgery") + 7)
    assert not hasattr(result, "text") and not hasattr(result, "situation")
    assert "Family Member A" not in repr(result) + repr(guarded)
    assert "12345" not in repr(result.facts) and "surgery" not in repr(result.facts)
    with pytest.raises(FrozenInstanceError):
        result.facts = ()


@pytest.mark.parametrize("invalid", [None, 123, "", "x" * 2001])
def test_invalid_input_is_bounded_and_error_has_no_payload(invalid):
    with pytest.raises(InterpretationError, match="^LOCAL_INTERPRETATION_INPUT_INVALID$"):
        interpret_situation(invalid)


@pytest.mark.parametrize("start,end", [(-1, 2), (0, 0), (0, 2001), (True, 3)])
def test_invalid_normalizer_offsets_are_rejected(start, end):
    with pytest.raises(InterpretationError, match="^LOCAL_INTERPRETATION_INPUT_INVALID$"):
        guard_normalizer_match("수술했습니다.", start, end)


def test_no_claim_count_or_clinical_code_is_invented():
    result = interpret_situation("수술을 받았습니다. 이전 청구 기록은 없습니다.")
    assert not any(
        "ClaimHistory" in item.field_path or "code" in item.field_path for item in result.facts
    )


@pytest.mark.parametrize(
    "text,path",
    [
        ("수술 상담을 받았습니다.", "MedicalEvent.performed"),
        ("외래 안내를 받았습니다.", "MedicalEvent.outpatient"),
        ("I had a consultation about surgery.", "MedicalEvent.performed"),
        ("수술 계획은 없습니다.", "MedicalEvent.performed"),
        ("No surgery is planned.", "MedicalEvent.performed"),
    ],
)
def test_discussion_or_absent_plan_is_not_an_actual_activity_assertion(text, path):
    assert not any(
        item.field_path == path and item.state == "CONFIRMED"
        for item in interpret_situation(text).facts
    )


@pytest.mark.parametrize(
    "text", ["수술 상담 후 입원했습니다.", "Surgery consultation before I was admitted."]
)
def test_another_activitys_performed_verb_does_not_confirm_the_surgery(text):
    result = interpret_situation(text)
    assert fact(result, "MedicalEvent.performed").state == "UNKNOWN"
    assert fact(result, "MedicalEvent.admission").state == "CONFIRMED"
    start = text.index("수술" if "수술" in text else "Surgery")
    end = start + (2 if "수술" in text else 7)
    assert guard_normalizer_match(text, start, end).state == "UNKNOWN"


def test_date_of_planned_activity_stays_a_scenario_date():
    result = interpret_situation("2026-09-08에 수술 예정입니다.")
    item = fact(result, "MedicalEvent.event_date")
    assert item.state == "SCENARIO" and item.value == date(2026, 9, 8)


def test_other_subject_unknown_does_not_erase_selected_subject_positive_same_activity():
    result = interpret_situation(
        "Family Member B had surgery. Family Member A had surgery.",
        selected_subject_terms=("Family Member A",),
        other_subject_terms=("Family Member B",),
    )
    assert fact(result, "MedicalEvent.performed").state == "CONFIRMED"
    assert any("LOCAL_OTHER_SUBJECT" in issue.reason_codes for issue in result.issues)


@pytest.mark.parametrize(
    "text",
    [
        "수술하지 않았다는 뜻은 아닙니다.",
        "It is not true that I did not have surgery.",
        "입원했으면 지급액이 궁금합니다.",
        "수술했나요",
    ],
)
def test_double_negation_and_hypothetical_or_question_form_are_not_confirmed(text):
    assert not any(item.state == "CONFIRMED" for item in interpret_situation(text).facts)


def test_normalizer_does_not_turn_diagnostic_testing_into_confirmed_diagnosis():
    text = "합성질환 검사를 받았습니다."
    assert guard_normalizer_match(text, 0, 4).state == "UNKNOWN"
    affirmed = "합성질환으로 확진됐습니다."
    assert guard_normalizer_match(affirmed, 0, 4).state == "AFFIRMED"


def test_other_dates_do_not_hide_a_valid_conflicting_date_explanation():
    result = interpret_situation("2025-01-02에 수술했습니다.", event_date=date(2026, 9, 8))
    assert not any(item.state == "CONFIRMED" for item in result.facts)
    assert any("LOCAL_OTHER_EVENT_TIME" in issue.reason_codes for issue in result.issues)


@pytest.mark.parametrize(
    "text,path",
    [
        ("진료일은 2026-09-08입니다.", "MedicalEvent.visit_date"),
        ("Visit date: 2026-09-08.", "MedicalEvent.visit_date"),
        ("사건일은 2026-09-08입니다.", "MedicalEvent.event_date"),
        ("2026-9-8에 입원했습니다.", "MedicalEvent.event_date"),
    ],
)
def test_explicit_date_role_is_retained_without_inventing_performed_care(text, path):
    result = interpret_situation(text)
    assert fact(result, path).value == date(2026, 9, 8)
    assert not any(item.field_path == "MedicalEvent.performed" for item in result.facts)


def test_selected_event_date_does_not_replace_an_explicit_visit_date():
    result = interpret_situation("진료일은 2026-09-08입니다.", event_date=date(2026, 9, 1))
    assert fact(result, "MedicalEvent.visit_date").value == date(2026, 9, 8)


def test_planned_duration_keeps_the_number_only_as_a_scenario():
    result = interpret_situation("5일간 입원 예정입니다.")
    item = fact(result, "MedicalEvent.admission_days")
    assert item.value == 5 and item.state == "SCENARIO"


@pytest.mark.parametrize(
    "text",
    [
        "실제 진료비는 1e6원입니다.",
        "Actual medical cost: USD 1e6.",
        "Actual medical cost: USD 100-200.",
    ],
)
def test_unsupported_numeric_syntax_is_not_parsed_as_a_smaller_billed_amount(text):
    item = fact(interpret_situation(text), "Receipt.billed_amount")
    assert item.state == "UNKNOWN" and item.value is None


@pytest.mark.parametrize(
    "text,subject,path",
    [
        ("합성예정가가 입원했습니다.", "합성예정가", "MedicalEvent.admission"),
        ("Admin Planned had surgery.", "Admin Planned", "MedicalEvent.performed"),
    ],
)
def test_subject_alias_words_are_not_activity_modifiers(text, subject, path):
    result = interpret_situation(text, selected_subject_terms=(subject,))
    assert fact(result, path).state == "CONFIRMED"
    start = text.index(subject)
    assert (
        guard_normalizer_match(
            text, start, start + len(subject), selected_subject_terms=(subject,)
        ).state
        == "UNKNOWN"
    )


@pytest.mark.parametrize("text", ["I didn't have surgery.", "수술 안했어요."])
def test_common_explicit_negative_forms_remain_negative_activity(text):
    item = fact(interpret_situation(text), "MedicalEvent.performed")
    assert item.value is False and item.state == "CONFIRMED"


def test_negative_followed_by_independent_admission_in_same_sentence():
    result = interpret_situation("수술하지 않은 후에 입원했습니다.")
    assert fact(result, "MedicalEvent.performed").value is False
    assert fact(result, "MedicalEvent.admission").value is True
