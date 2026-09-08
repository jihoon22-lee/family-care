"""Planned inputs produce explicit hypotheses alongside unchanged actual facts."""

from dataclasses import replace

import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import FactValue
from familycare_api.guidance.engine import LocalGuidanceEngine
from familycare_api.guidance.event_facts import build_event_facts
from familycare_api.guidance.interpretation import interpret_situation

from apps.api.tests.test_guidance_local_event_engine import context
from apps.api.tests.test_private_knowledge_engine import HOUSEHOLD_ID, _event


def test_cancelled_plan_is_neither_actual_admission_nor_an_open_hypothesis():
    result = interpret_situation("5일 입원 계획은 취소했습니다.")
    assert all(item.state == "UNKNOWN" for item in result.facts)


def test_planned_five_days_has_separate_300_scenario_without_confirming_admission():
    event = replace(_event(), facts={}, situation="5일 입원 예정입니다.")
    original = build_event_facts(event)
    result = LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), event, context())
    candidate = result.candidates[0]
    assert candidate.condition_result == "UNKNOWN" and candidate.group == "CONDITIONAL"
    assert candidate.estimate.kind == "FORMULA" and candidate.estimate.amount is None
    scenario = candidate.scenarios[0]
    assert scenario.kind == "PLANNED_CARE" and scenario.estimate.amount == "300"
    assert scenario.estimate.basis == "USER_SCENARIO"
    assert "PLANNED_CARE_ASSUMED" in scenario.estimate.assumptions
    assert {h.field_path: h.value for h in scenario.hypotheses} == {
        "MedicalEvent.admission": True,
        "MedicalEvent.admission_days": 5,
    }
    day = next(h for h in scenario.hypotheses if h.field_path == "MedicalEvent.admission_days")
    assert day.source_refs[0].source_id == str(event.id)
    assert day.source_refs[0].version == event.version
    assert day.spans and event.situation[day.spans[0].start : day.spans[0].end] == "5일 입원"
    operand = next(
        operand
        for step in scenario.estimate.trace.steps
        for operand in step.operands
        if operand.field_path == "MedicalEvent.admission_days"
    )
    assert operand.value == "5" and operand.provenance == "SCENARIO_ASSUMPTION"
    assert operand.source_refs == day.source_refs
    assert original.context.get("MedicalEvent.admission_days").value is None
    assert build_event_facts(event) == original and event.facts == {}


@pytest.mark.parametrize(
    "text",
    [
        "5일 입원했습니다.",
        "5일 입원 계획은 취소했습니다.",
        "어머니는 5일 입원 예정입니다. 저는 외래 진료를 받았습니다.",
    ],
)
def test_actual_cancelled_or_other_subject_days_do_not_create_a_planned_scenario(text):
    event = replace(_event(), facts={}, situation=text)
    result = LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), event, context())
    assert all(not candidate.scenarios for candidate in result.candidates)


def test_explicit_actual_days_are_never_overwritten_by_the_plan():
    event = replace(
        _event(),
        situation="5일 입원 예정입니다.",
        facts={
            "MedicalEvent.admission_days": FactValue(3, "user", ()),
        },
    )
    result = LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), event, context())
    assert result.candidates[0].estimate.amount == "100"
    assert not result.candidates[0].scenarios


def test_scenario_identity_changes_with_event_version_and_hypothesis_value():
    event = replace(_event(), facts={}, situation="5일 입원 예정입니다.")

    def scenario(item):
        return (
            LocalGuidanceEngine()
            .evaluate(HouseholdScope(HOUSEHOLD_ID), item, context())
            .candidates[0]
            .scenarios[0]
        )

    first = scenario(event)
    changed = scenario(replace(event, version=event.version + 1, situation="6일 입원 예정입니다."))
    assert changed.scenario_key != first.scenario_key
    assert changed.estimate.amount == "400" and first.estimate.amount == "300"
    assert changed.hypotheses[1].source_refs[0].version == event.version + 1
