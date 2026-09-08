"""Local observations and reviewed topics never manufacture clinical confirmation."""

from dataclasses import FrozenInstanceError, replace
from datetime import date
from decimal import Decimal

import pytest
from familycare_api.decisions.domain import FactValue
from familycare_api.decisions.knowledge_domain import KnowledgeFactNormalizer
from familycare_api.guidance.event_facts import (
    CodeScope,
    EventFactsError,
    build_event_facts,
    scope_code_facts,
)

from apps.api.tests.test_private_knowledge_facts import _event, _normalizer


def structured(field_id, value, *, source="user", state="confirmed", **metadata):
    return {"field_id": field_id, "value": value, "source": source, "state": state, **metadata}


def test_clear_local_admission_is_reusable_with_offsets_and_original_provenance():
    result = build_event_facts(_event("5일간 입원했습니다."))
    admission = result.context.get("MedicalEvent.admission")
    days = result.context.get("MedicalEvent.admission_days")
    assert admission.value is True and admission.is_trusted
    assert days.value == 5 and days.provenance == "DERIVED_CONFIRMED"
    assert all(key.startswith("local-situation-v1:") for key in days.evidence_keys)
    assert all(item.provenance == "EXPLICIT_LOCAL" for item in result.interpretation.facts)


def test_planned_activity_and_duration_are_separate_from_confirmed_facts():
    result = build_event_facts(_event("5일간 입원 예정입니다."))
    assert not result.context.get("MedicalEvent.admission").is_trusted
    assert result.context.get("MedicalEvent.admission_days").value is None
    assert any(item.value == 5 and item.state == "SCENARIO" for item in result.scenarios)


@pytest.mark.parametrize("activity", [None, "admission", "outpatient"])
def test_surgery_observation_cannot_populate_generic_performed_without_activity_binding(activity):
    result = build_event_facts(_event("수술하지 않았고 입원했습니다."), activity=activity)
    assert result.context.get("MedicalEvent.admission").value is True
    assert not result.context.get("MedicalEvent.performed").is_trusted
    assert "LOCAL_ACTIVITY_BINDING_REQUIRED" in result.reason_codes


def test_explicit_surgery_binding_allows_only_its_performed_observation():
    result = build_event_facts(_event("수술하지 않았고 입원했습니다."), activity="surgery")
    assert result.context.get("MedicalEvent.performed").value is False
    assert result.context.get("MedicalEvent.performed").is_trusted
    assert result.context.get("MedicalEvent.admission").value is True


@pytest.mark.parametrize(
    "field_path",
    ["MedicalEvent.classification", "MedicalEvent.diagnosis_code", "MedicalEvent.procedure_code"],
)
def test_bare_reviewed_clinical_keyword_is_a_topic_not_a_confirmed_code(field_path):
    result = build_event_facts(
        _event("violet delta"),
        (_normalizer("reviewed", ("violet", "delta"), "sample-code", field_path=field_path),),
    )
    fact = result.context.get(field_path)
    assert fact.value is None and not fact.is_trusted
    assert result.topics[0].normalized_value == "sample-code"
    assert result.topics[0].relevance_allowed
    assert result.topics[0].scope_state == "AFFIRMED"


@pytest.mark.parametrize(
    "text,expected_state,allowed",
    [
        ("수술하지 않았습니다.", "NEGATED", False),
        ("수술 예정입니다.", "PLANNED", True),
        ("수술 여부는 모릅니다.", "UNKNOWN", True),
        ("수술 보험금 문의입니다.", "UNKNOWN", False),
        ("어머니가 수술을 받았습니다.", "UNKNOWN", False),
    ],
)
def test_normalizer_topics_keep_scope_and_never_create_positive_codes(
    text, expected_state, allowed
):
    result = build_event_facts(
        _event(text),
        (
            _normalizer(
                "surgery", ("수술",), "procedure-a", field_path="MedicalEvent.procedure_code"
            ),
        ),
    )
    assert result.topics[0].scope_state == expected_state
    assert result.topics[0].relevance_allowed is allowed
    value = result.context.get("MedicalEvent.procedure_code")
    assert value is None or not value.is_trusted


def test_other_family_occurrence_does_not_block_independent_selected_occurrence():
    result = build_event_facts(
        _event("Family Member B had surgery. Family Member A had surgery."),
        (
            _normalizer(
                "surgery", ("surgery",), "procedure-a", field_path="MedicalEvent.procedure_code"
            ),
        ),
        selected_subject_terms=("Family Member A",),
        other_subject_terms=("Family Member B",),
        activity="surgery",
    )
    assert [item.relevance_allowed for item in result.topics] == [False, True]
    assert result.context.get("MedicalEvent.performed").value is True
    assert not result.context.get("MedicalEvent.procedure_code").is_trusted


def test_normalizer_token_offsets_survive_unicode_case_and_korean_case_particles():
    text = "ＶＩＯＬＥＴ\u3000DELTA와 입원했습니다."
    result = build_event_facts(
        _event(text), (_normalizer("reviewed", ("violet", "delta"), "sample-code"),)
    )
    # English tokens do not acquire Korean suffix matching.
    assert result.topics == ()
    text = "샘플처치를 받았습니다."
    result = build_event_facts(
        _event(text), (_normalizer("reviewed", ("샘플처치",), "sample-code"),)
    )
    assert len(result.topics) == 1
    assert text[result.topics[0].span.start : result.topics[0].span.end] == "샘플처치를"
    full_width = build_event_facts(
        _event("ＶＩＯＬＥＴ\u3000DELTA was mentioned"),
        (_normalizer("reviewed", ("violet", "delta"), "sample-code"),),
    )
    assert len(full_width.topics) == 1


def test_normalizer_does_not_match_a_compound_or_alias_as_a_clinical_topic():
    normalizers = (_normalizer("reviewed", ("샘플처치",), "sample-code"),)
    assert not build_event_facts(_event("샘플처치실을 방문했습니다."), normalizers).topics
    result = build_event_facts(
        _event("샘플처치가 입원했습니다."), normalizers, selected_subject_terms=("샘플처치",)
    )
    assert not result.topics[0].relevance_allowed
    assert result.topics[0].reason_codes == ("LOCAL_SUBJECT_REFERENCE",)


def test_equal_priority_topic_conflict_is_audited_without_picking_a_code():
    normalizers = (
        _normalizer("one", ("violet",), "sample-a"),
        _normalizer("two", ("violet",), "sample-b"),
    )
    result = build_event_facts(_event("violet"), normalizers)
    assert result.context.get("MedicalEvent.classification").provenance == "CONFLICTING"
    assert result.context.audit_conflicts == ("MedicalEvent.classification",)
    overridden = build_event_facts(
        replace(_event("violet"), structured_facts=(structured("condition_class", "user-code"),)),
        normalizers,
    )
    assert overridden.context.get("MedicalEvent.classification").value == "user-code"
    assert overridden.context.get("MedicalEvent.classification").provenance == "USER_CONFIRMED"
    assert overridden.context.audit_conflicts == ("MedicalEvent.classification",)


def test_lower_priority_topic_cannot_override_higher_priority_relevance():
    result = build_event_facts(
        _event("violet"),
        (
            _normalizer("lower", ("violet",), "sample-a", priority=10),
            _normalizer("higher", ("violet",), "sample-b", priority=20),
        ),
    )
    assert [(item.normalizer_key, item.relevance_allowed) for item in result.topics] == [
        ("lower", False),
        ("higher", True),
    ]
    assert result.context.audit_conflicts == ()


def test_explicit_structured_fact_overrides_local_reading_and_keeps_conflict_audit():
    event = replace(_event("입원했습니다."), structured_facts=(structured("admission", False),))
    result = build_event_facts(event)
    assert result.context.get("MedicalEvent.admission").value is False
    assert result.context.get("MedicalEvent.admission").provenance == "USER_CONFIRMED"
    assert "MedicalEvent.admission" in result.context.audit_conflicts
    assert event.structured_facts[0]["value"] is False


@pytest.mark.parametrize(
    "source,state,expected",
    [
        ("ai", "confirmed", "AI_SUGGESTED"),
        ("user", "ambiguous", "UNCONFIRMED"),
        ("user", "conflict", "CONFLICTING"),
    ],
)
def test_structured_unconfirmed_or_conflicting_state_is_never_repaired_from_free_text(
    source, state, expected
):
    event = replace(
        _event("입원했습니다."),
        structured_facts=(structured("admission", None, source=source, state=state),),
    )
    result = build_event_facts(event)
    fact = result.context.get("MedicalEvent.admission")
    assert fact.value is None and fact.provenance == expected and not fact.is_trusted


def test_ai_structured_fact_does_not_erase_existing_explicit_user_value():
    event = replace(
        _event("입원했습니다.", facts={"MedicalEvent.admission": FactValue(False, "user", ())}),
        structured_facts=(structured("admission", True, source="ai"),),
    )
    result = build_event_facts(event)
    assert result.context.get("MedicalEvent.admission").value is False
    assert result.context.get("MedicalEvent.admission").provenance == "USER_CONFIRMED"
    assert "MedicalEvent.admission" in result.context.audit_conflicts


def test_no_text_still_preserves_structured_facts_and_selected_dates():
    event = replace(_event(""), structured_facts=(structured("admission", True),))
    result = build_event_facts(event)
    assert result.context.get("MedicalEvent.admission").value is True
    assert result.context.get("MedicalEvent.event_date").value == event.event_date
    assert result.topics == ()


def test_billed_and_estimated_cost_roles_are_never_converted_to_covered_receipt_amounts():
    result = build_event_facts(_event("실제 진료비는 12만원입니다. 예상 진료비는 20만원입니다."))
    assert result.context.get("Receipt.billed_amount").value == Decimal("120000")
    assert any(
        item.field_path == "Receipt.estimated_amount" and item.value == Decimal("200000")
        for item in result.scenarios
    )
    assert result.context.get("Receipt.covered_amount") is None
    assert result.context.get("Receipt.confirmed_amount") is None


def test_code_scope_is_read_from_the_selected_explicit_structured_fact_only():
    expected = CodeScope("MedicalEvent.diagnosis_code", "synthetic-system", "edition-1")
    event = replace(
        _event("입원했습니다."),
        structured_facts=(
            structured(
                "diagnosis_code",
                "class-a",
                code_system=expected.code_system,
                code_version=expected.code_version,
            ),
        ),
    )
    read = build_event_facts(event)
    assert read.code_scopes == (expected,)
    scoped = scope_code_facts(read, (expected,))
    assert scoped.context.get(expected.field_path).value == "class-a"
    assert scoped.context.get(expected.field_path).provenance == "USER_CONFIRMED"
    assert scoped.reason_codes == ()


@pytest.mark.parametrize("missing", ["code_system", "code_version", "both"])
def test_incomplete_input_code_identity_cannot_be_filled_from_required_terms_version(missing):
    metadata = {"code_system": "synthetic-system", "code_version": "edition-1"}
    if missing == "both":
        metadata = {}
    else:
        metadata.pop(missing)
    read = build_event_facts(
        replace(
            _event("입원했습니다."),
            structured_facts=(structured("diagnosis_code", "class-a", **metadata),),
        )
    )
    expected = CodeScope("MedicalEvent.diagnosis_code", "synthetic-system", "edition-1")
    assert read.context.get(expected.field_path).value == "class-a"
    scoped = scope_code_facts(read, (expected,))
    assert scoped.context.get(expected.field_path).value is None
    assert not scoped.context.get(expected.field_path).is_trusted
    assert scoped.context.get("MedicalEvent.admission").value is True
    assert "EVENT_CODE_SCOPE_UNRESOLVED" in scoped.reason_codes
    assert read.context.get(expected.field_path).provenance == "USER_CONFIRMED"


def test_forged_caller_scope_cannot_replace_missing_structured_metadata():
    requested = CodeScope("MedicalEvent.diagnosis_code", "synthetic-system", "edition-1")
    read = build_event_facts(
        replace(_event(""), structured_facts=(structured("diagnosis_code", "class-a"),)),
        code_scopes=(requested,),
    )
    assert read.code_scopes == ()
    assert "EVENT_CODE_SCOPE_UNVERIFIED" in read.reason_codes
    assert scope_code_facts(read, (requested,)).context.get(requested.field_path).value is None


@pytest.mark.parametrize(
    "system,version", [("other-system", "edition-1"), ("synthetic-system", "edition-2")]
)
def test_different_code_system_or_version_never_matches_by_code_string(system, version):
    event = replace(
        _event(""),
        structured_facts=(
            structured(
                "diagnosis_code",
                "class-a",
                code_system="synthetic-system",
                code_version="edition-1",
            ),
        ),
    )
    read = build_event_facts(event)
    result = scope_code_facts(read, (CodeScope("MedicalEvent.diagnosis_code", system, version),))
    assert result.context.get("MedicalEvent.diagnosis_code").value is None
    assert "EVENT_CODE_SCOPE_MISMATCH" in result.reason_codes


def test_discarded_ai_metadata_cannot_qualify_a_different_user_code():
    event = replace(
        _event("", facts={"MedicalEvent.diagnosis_code": FactValue("user-code", "user", ())}),
        structured_facts=(
            structured(
                "diagnosis_code",
                "ai-code",
                source="ai",
                code_system="synthetic-system",
                code_version="edition-1",
            ),
        ),
    )
    read = build_event_facts(event)
    assert read.context.get("MedicalEvent.diagnosis_code").value == "user-code"
    assert not read.code_scopes


def test_conflicting_metadata_is_preserved_as_scope_uncertainty():
    event = replace(
        _event(""),
        structured_facts=tuple(
            structured(
                "diagnosis_code", "class-a", code_system="synthetic-system", code_version=version
            )
            for version in ("edition-1", "edition-2")
        ),
    )
    read = build_event_facts(event)
    assert not read.code_scopes and "EVENT_CODE_SCOPE_UNRESOLVED" in read.reason_codes
    assert (
        scope_code_facts(
            read, (CodeScope("MedicalEvent.diagnosis_code", "synthetic-system", "edition-1"),)
        )
        .context.get("MedicalEvent.diagnosis_code")
        .value
        is None
    )


def test_empty_or_conflicting_required_scope_does_not_leave_unqualified_code_active():
    event = replace(
        _event(""),
        structured_facts=(
            structured(
                "diagnosis_code",
                "class-a",
                code_system="synthetic-system",
                code_version="edition-1",
            ),
        ),
    )
    read = build_event_facts(event)
    for requested in (
        (),
        (
            CodeScope("MedicalEvent.diagnosis_code", "synthetic-system", "edition-1"),
            CodeScope("MedicalEvent.diagnosis_code", "synthetic-system", "edition-2"),
        ),
    ):
        assert (
            scope_code_facts(read, requested).context.get("MedicalEvent.diagnosis_code").value
            is None
        )


def test_result_collections_are_immutable_and_repr_has_no_statement_or_clinical_values():
    read = build_event_facts(
        _event("Family Member A mentioned violet."),
        (_normalizer("reviewed", ("violet",), "synthetic-sensitive-code"),),
        selected_subject_terms=("Family Member A",),
    )
    assert "Family Member A" not in repr(read)
    assert "synthetic-sensitive-code" not in repr(read.topics)
    with pytest.raises(FrozenInstanceError):
        read.topics = ()
    with pytest.raises(TypeError):
        read.context.facts["MedicalEvent.admission"] = None


def test_normalizer_cannot_invent_a_policy_amount_or_claim_count():
    result = build_event_facts(
        _event("violet"),
        (KnowledgeFactNormalizer("bad-field", "Rider.insured_amount", ("violet",), "100", 1),),
    )
    assert result.context.get("Rider.insured_amount") is None
    assert result.context.get("ClaimHistory.counted_occurrence") is None
    assert "LOCAL_NORMALIZER_FIELD_UNSUPPORTED" in result.reason_codes


def test_fixed_input_errors_never_echo_source_values():
    with pytest.raises(EventFactsError, match="^GUIDANCE_EVENT_FACTS_INPUT_INVALID$"):
        build_event_facts(_event("synthetic-private-text"), activity="invented")
    with pytest.raises(EventFactsError, match="^GUIDANCE_EVENT_FACTS_INPUT_INVALID$"):
        CodeScope("Rider.insured_amount", "synthetic-private-system", "edition-1")


def test_confirmed_system_input_is_not_erased_by_an_ai_suggestion():
    event = replace(
        _event(""),
        structured_facts=(
            structured("admission", True, source="system"),
            structured("admission", False, source="ai"),
        ),
    )
    read = build_event_facts(event)
    assert read.context.get("MedicalEvent.admission").value is True
    assert read.context.get("MedicalEvent.admission").provenance == "DERIVED_CONFIRMED"
    assert "MedicalEvent.admission" in read.context.audit_conflicts


@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_user_states_cannot_be_resolved_by_list_order(reverse):
    values = (
        structured("admission", True, state="ambiguous"),
        structured("admission", True, state="confirmed"),
    )
    event = replace(_event(""), structured_facts=tuple(reversed(values)) if reverse else values)
    read = build_event_facts(event)
    assert not read.context.get("MedicalEvent.admission").is_trusted
    assert "MedicalEvent.admission" in read.context.audit_conflicts


def test_malformed_structured_source_fails_closed_without_discarding_other_facts():
    event = replace(
        _event("5일간 입원했습니다."),
        structured_facts=(
            structured("outpatient", True, source={"synthetic-private": "malformed"}),
        ),
    )
    read = build_event_facts(event)
    assert not read.context.get("MedicalEvent.outpatient").is_trusted
    assert read.context.get("MedicalEvent.admission_days").value == 5
    assert "LOCAL_EXPLICIT_FACT_INVALID" in read.reason_codes


def test_guarded_negative_and_other_date_topics_do_not_create_false_conflict_with_user_code():
    event = replace(
        _event(
            "2025-01-02에 violet delta 수술했습니다.",
            facts={
                "MedicalEvent.classification": FactValue("user-code", "user", ()),
            },
        ),
        event_date=date(2026, 6, 1),
    )
    read = build_event_facts(event, (_normalizer("old", ("violet", "delta"), "other-code"),))
    assert not read.topics[0].relevance_allowed
    assert "MedicalEvent.classification" not in read.context.audit_conflicts


def test_local_fact_paths_identify_only_parser_facts_retained_in_effective_context():
    read = build_event_facts(
        replace(_event("5일간 입원했습니다."), structured_facts=(structured("admission", True),))
    )
    assert read.local_fact_paths == ("MedicalEvent.admission_days",)
    assert read.interpretation.facts[0].provenance == "EXPLICIT_LOCAL"
    assert not build_event_facts(_event("5일간 입원 예정입니다.")).local_fact_paths


def test_explicit_evidence_string_cannot_impersonate_a_parser_origin():
    read = build_event_facts(
        replace(
            _event(""),
            structured_facts=(
                structured(
                    "admission", True, source="system", evidence_ids=["local-situation-v1:0:2"]
                ),
            ),
        )
    )
    assert read.context.get("MedicalEvent.admission").is_trusted
    assert not read.local_fact_paths


@pytest.mark.parametrize("value,state", [(False, "confirmed"), (None, "ambiguous")])
def test_explicit_non_admission_blocks_only_locally_derived_admission_days(value, state):
    read = build_event_facts(
        replace(
            _event("5일간 입원했습니다. 외래 진료를 받았습니다."),
            structured_facts=(structured("admission", value, state=state),),
        )
    )
    assert read.context.get("MedicalEvent.admission_days").value is None
    assert "MedicalEvent.admission_days" in read.context.audit_conflicts
    assert read.context.get("MedicalEvent.outpatient").value is True
