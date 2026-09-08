"""The public local result keeps compatible sums and omitted amounts explicit."""

from dataclasses import replace
from uuid import uuid4

from familycare_api.common.coverage_identity import CanonicalCoverageIdentity, CanonicalCoverageRef
from familycare_api.common.scope import HouseholdScope
from familycare_api.guidance.engine import LocalGuidanceEngine
from familycare_api.guidance.models import LocalGuidanceResponse
from familycare_api.guidance.private_adapter import adapt_private_guidance

from apps.api.tests.test_private_knowledge_engine import HOUSEHOLD_ID, _context, _coverage, _event


def combined_context(*amounts):
    context = adapt_private_guidance(
        _context(*(_coverage(n, amount) for n, amount in enumerate(amounts, 1)))
    )
    coverages = []
    for n, source in enumerate(context.coverages, 1):
        ref = CanonicalCoverageRef(
            kind="OPERATIONAL_RIDER", contract_id=uuid4(), coverage_id=uuid4()
        )
        identity = CanonicalCoverageIdentity(
            ref=ref,
            source_refs=(source.ref, ref),
            authority="PROGRAM_VERIFIED_SOURCE_IDENTITY",
            ledger_version=1,
            verification_digest_sha256=f"{n:064x}",
        )
        coverages.append(replace(source, canonical_identity=identity))
    return replace(context, coverages=tuple(coverages))


def test_engine_returns_typed_conditional_subtotal_with_saved_trace_references():
    result = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID), _event(), combined_context("100", "200")
    )
    assert len(result.candidates) == 2
    assert len(result.fixed_subtotals) == 1
    subtotal = result.fixed_subtotals[0]
    assert subtotal.amount == "300" and subtotal.currency == "KRW"
    assert subtotal.conditional and not subtotal.partial
    assert subtotal.scenario_key is None and not subtotal.hypotheses
    assert len(subtotal.items) == 2
    assert (
        len(
            next(
                a
                for a in subtotal.scoped_assumptions
                if a.code == "INDEPENDENT_FIXED_PAYMENTS_ASSUMED"
            ).applies_to
        )
        == 2
    )
    restored = LocalGuidanceResponse.model_validate_json(result.model_dump_json())
    assert restored.fixed_subtotals == result.fixed_subtotals


def test_unavailable_amount_remains_visible_beside_partial_fixed_subtotal():
    context = combined_context("100", "200", "300")
    context = replace(
        context, coverages=(*context.coverages[:2], replace(context.coverages[2], calculation=None))
    )
    result = LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), _event(), context)
    assert len(result.candidates) == 3
    assert result.fixed_subtotals[0].amount == "300"
    assert result.fixed_subtotals[0].partial
    assert result.subtotal_omissions[0].ref == context.coverages[2].canonical_identity.ref
    assert result.subtotal_omissions[0].reason_code == "POINT_ESTIMATE_UNAVAILABLE"


def test_subtotal_failure_preserves_individual_candidates(monkeypatch):
    from familycare_api.guidance import subtotals

    def fail(_response):
        raise ArithmeticError("synthetic aggregate failure")

    monkeypatch.setattr(subtotals, "fixed_subtotal_projection", fail)
    result = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID), _event(), combined_context("100", "200")
    )
    assert [candidate.estimate.amount for candidate in result.candidates] == ["100", "200"]
    assert not result.fixed_subtotals
    assert "GUIDANCE_SUBTOTAL_FAILED" in result.support.failure_codes


def test_planned_subtotal_is_typed_and_saved_without_promoting_actual_amounts():
    from apps.api.tests.test_guidance_local_event_engine import context as semantic_context

    semantic = semantic_context().coverages[0]
    context = combined_context("100", "100")
    context = replace(
        context,
        coverages=tuple(
            replace(item, rules=semantic.rules, calculation=semantic.calculation)
            for item in context.coverages
        ),
    )
    event = replace(
        _event(),
        facts={},
        situation="5일 입원 예정입니다.",
        structured_facts=(
            {
                "field_id": "condition_class",
                "value": "class-a",
                "source": "user",
                "state": "confirmed",
                "confidence": "high",
                "evidence_ids": (),
                "code_system": "synthetic-classification",
                "code_version": "edition-1",
            },
        ),
    )
    result = LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), event, context)
    assert all(candidate.estimate.amount is None for candidate in result.candidates)
    assert len(result.fixed_subtotals) == 1
    subtotal = result.fixed_subtotals[0]
    assert subtotal.amount == "600"
    assert subtotal.scenario_key == result.candidates[0].scenarios[0].scenario_key
    assert subtotal.hypotheses == result.candidates[0].scenarios[0].hypotheses
    assert {item.scenario_key for item in subtotal.items} == {subtotal.scenario_key}
    assert "PLANNED_CARE_ASSUMED" in {item.code for item in subtotal.scoped_assumptions}
    restored = LocalGuidanceResponse.model_validate_json(result.model_dump_json())
    assert restored.fixed_subtotals == result.fixed_subtotals
    assert event.facts == {}
