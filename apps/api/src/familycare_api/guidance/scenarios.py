"""Explicit planned-care hypotheses reuse arithmetic without changing event facts."""

import hashlib
import json
from decimal import Decimal
from typing import cast

from familycare_api.decisions.domain import MedicalEvent
from familycare_api.decisions.knowledge_domain import KnowledgeFactContext
from familycare_api.guidance.calculation_runtime import CalculationInput, CalculationSourceRef
from familycare_api.guidance.domain import GuidanceCoverageInput
from familycare_api.guidance.estimates import estimate_coverage
from familycare_api.guidance.event_facts import EventFactRead
from familycare_api.guidance.interpretation import INTERPRETATION_REVISION
from familycare_api.guidance.models import GuidanceEventSpan, GuidanceHypothesis, GuidanceScenario
from familycare_api.guidance.trace_projection import source_reference


def planned_care_scenarios(
    event: MedicalEvent,
    facts: KnowledgeFactContext,
    coverage: GuidanceCoverageInput,
    event_read: EventFactRead,
    *,
    assumptions: list[str],
) -> tuple[GuidanceScenario, ...]:
    path = "MedicalEvent.admission_days"
    if coverage.calculation is None or coverage.benefit_type != "FIXED":
        return ()
    actual = facts.get(path)
    admission = facts.get("MedicalEvent.admission")
    if (
        actual is not None
        and (actual.value is not None or actual.provenance == "CONFLICTING")
        or admission is not None
        and admission.value is False
        or path in facts.audit_conflicts
    ):
        return ()
    days = tuple(
        item
        for item in event_read.scenarios
        if item.field_path == path
        and item.activity == "admission"
        and type(item.value) is int
        and 1 <= item.value <= 36500
        and item.spans
        and "LOCAL_ACTIVITY_PLANNED" in item.reason_codes
    )
    if len(days) != 1:
        return ()
    day = days[0]
    day_value = cast(int, day.value)
    admission_plan = next(
        (
            item
            for item in event_read.scenarios
            if item.field_path == "MedicalEvent.admission"
            and item.activity == "admission"
            and item.spans
            and "LOCAL_ACTIVITY_PLANNED" in item.reason_codes
        ),
        None,
    )
    if admission_plan is None:
        return ()
    payload = {
        "event_id": str(event.id),
        "event_version": event.version,
        "text_sha256": hashlib.sha256(event.situation.encode()).hexdigest(),
        "parser_revision": INTERPRETATION_REVISION,
        "hypotheses": [("MedicalEvent.admission", True), (path, day.value)],
        "spans": [(s.start, s.end) for item in (admission_plan, day) for s in item.spans],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    ref = CalculationSourceRef("EVENT_SCENARIO", event.id, event.version, digest)
    inputs = {
        path: CalculationInput(
            Decimal(day_value),
            "DAYS",
            None,
            "SCENARIO_ASSUMPTION",
            (ref,),
        )
    }
    estimate = estimate_coverage(
        event,
        facts,
        coverage,
        event_read,
        conditions="UNKNOWN",
        assumptions=[*assumptions, "PLANNED_CARE_ASSUMED"],
        scenario_inputs=inputs,
    )
    # A hypothesis unrelated to this formula must not label an ordinary amount
    # as a scenario. The base estimate already preserves unavailable expressions.
    if estimate.trace is None or not any(
        operand.field_path == path and operand.provenance == "SCENARIO_ASSUMPTION"
        for step in estimate.trace.steps
        for operand in step.operands
    ):
        return ()
    hypotheses = tuple(
        GuidanceHypothesis(
            field_path=item.field_path,
            value=value,
            spans=tuple(GuidanceEventSpan(start=s.start, end=s.end) for s in item.spans),
            source_refs=(source_reference(ref),),
        )
        for item, value in ((admission_plan, True), (day, day_value))
    )
    return (
        GuidanceScenario(
            scenario_key=digest,
            kind="PLANNED_CARE",
            hypotheses=hypotheses,
            estimate=estimate.model_copy(update={"basis": "USER_SCENARIO"}),
        ),
    )
