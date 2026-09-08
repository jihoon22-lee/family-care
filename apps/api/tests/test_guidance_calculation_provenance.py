"""Calculated amounts retain the actual field and formula source, not invented IDs."""

from dataclasses import replace

from familycare_api.common.scope import HouseholdScope
from familycare_api.guidance.engine import LocalGuidanceEngine
from familycare_api.guidance.private_adapter import adapt_private_guidance

from apps.api.tests.test_guidance_local_event_engine import context
from apps.api.tests.test_private_knowledge_engine import (
    HOUSEHOLD_ID,
    KNOWLEDGE_RUN_ID,
    _context,
    _coverage,
    _event,
)


def test_private_amount_basis_retains_its_actual_catalog_coverage_and_certificate_pages():
    coverage = _coverage(1, "100")
    adapted = adapt_private_guidance(_context(coverage)).coverages[0]
    basis = adapted.contract_amount
    assert basis.amount == "100" and basis.currency == "KRW"
    assert basis.amount_authority == "DOCUMENT_REVIEWED"
    assert {str(ref.source_id) for ref in basis.source_refs} >= {
        str(KNOWLEDGE_RUN_ID),
        str(coverage.knowledge_coverage_id),
    }
    assert basis.evidence[0].document_alias == "Sample Certificate 1"
    assert basis.evidence[0].page_start == basis.evidence[0].page_end == 3
    assert not hasattr(basis.evidence[0], "evidence_id")


def test_semantic_daily_trace_retains_event_version_and_every_original_operand():
    event = replace(_event(), facts={}, situation="5일 입원했습니다.")
    result = LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), event, context())
    estimate = result.candidates[0].estimate
    assert estimate.amount == "300"
    assert estimate.trace is not None
    subtraction = next(step for step in estimate.trace.steps if step.operation == "subtract")
    assert [item.value for item in subtraction.operands] == ["5", "2"]
    assert subtraction.unit == "DAYS" and subtraction.value == "3"
    event_operand = subtraction.operands[0]
    assert event_operand.provenance == "DERIVED_CONFIRMED"
    assert any(
        str(ref.source_id) == str(event.id) and ref.version == event.version
        for ref in event_operand.source_refs
    )
    assert estimate.trace.steps[-1].rounding_rule == "half_up"
    assert estimate.trace.publication_id == context().coverages[0].calculation.publication_id


def test_amount_authority_does_not_confirm_an_unverified_currency():
    ctx = context()
    coverage = ctx.coverages[0]
    ctx = replace(
        ctx,
        coverages=(
            replace(
                coverage,
                contract_amount=coverage.contract_amount.model_copy(
                    update={"currency_authority": "UNCONFIRMED"}
                ),
            ),
        ),
    )
    event = replace(_event(), facts={}, situation="5일 입원했습니다.")
    result = LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), event, ctx)
    assert result.candidates[0].estimate.kind == "FORMULA"
    assert result.candidates[0].estimate.amount is None
