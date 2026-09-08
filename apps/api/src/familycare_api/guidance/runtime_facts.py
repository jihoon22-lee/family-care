"""Keep the new guidance trust policy separate from historical evaluator adapters."""

from collections.abc import Mapping
from dataclasses import replace

from familycare_api.decisions.domain import FactContext, FactValue
from familycare_api.decisions.knowledge_domain import KnowledgeFactContext
from familycare_api.decisions.knowledge_engine import _legacy_fact_context
from familycare_api.guidance.domain import GuidanceCoverageInput


def runtime_fact_context(
    facts: KnowledgeFactContext, coverage: GuidanceCoverageInput
) -> FactContext:
    # The historical adapter accepts ai_structured. New guidance retains AI
    # suggestions as unconfirmed inputs until a separate source/user validates them.
    context = _legacy_fact_context(facts, coverage)

    def trusted(values: Mapping[str, FactValue]) -> dict[str, FactValue]:
        return {
            path: replace(value, confirmation="unconfirmed")
            if value.confirmation == "ai_structured"
            else value
            for path, value in values.items()
        }

    medical = trusted(context.medical_event)
    event_date = medical.get("MedicalEvent.event_date")
    return replace(
        context,
        medical_event=medical,
        policy=trusted(context.policy),
        rider=trusted(context.rider),
        claim_history=trusted(context.claim_history),
        receipt=trusted(context.receipt),
        as_of_date=(
            context.as_of_date
            if event_date is not None
            and event_date.confirmation == "user"
            and not event_date.evidence_stale
            else None
        ),
    )
