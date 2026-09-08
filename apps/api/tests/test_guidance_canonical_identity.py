"""Common coverage identity preserves sources and isolates corrected numeric inputs."""

from dataclasses import replace

import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.guidance.engine import LocalGuidanceEngine

from apps.api.tests.test_private_knowledge_engine import (
    HOUSEHOLD_ID,
    _context,
    _coverage,
    _event,
    _id,
)


def _identity(conflicts: tuple[str, ...] = ()):
    from familycare_api.common.coverage_identity import (
        CanonicalCoverageIdentity,
        CanonicalCoverageRef,
    )

    coverage = _coverage(1, "100")
    operational = CanonicalCoverageRef(
        kind="OPERATIONAL_RIDER", contract_id=_id(900), coverage_id=_id(901)
    )
    return CanonicalCoverageIdentity(
        ref=operational,
        source_refs=(
            CanonicalCoverageRef(
                kind="PRIVATE_KNOWLEDGE_COVERAGE",
                contract_id=coverage.knowledge_contract_id,
                coverage_id=coverage.knowledge_coverage_id,
            ),
            operational,
        ),
        authority="PROGRAM_VERIFIED_SOURCE_IDENTITY",
        ledger_version=2,
        verification_digest_sha256="a" * 64,
        field_conflicts=conflicts,
    )


def test_guidance_uses_common_identity_and_retains_both_source_references() -> None:
    identity = _identity()
    context = _context(replace(_coverage(1, "100"), canonical_identity=identity))
    result = LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), _event(), context)
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.ref == identity.ref
    assert candidate.canonical_identity == identity
    assert candidate.estimate.kind == "POINT" and candidate.estimate.amount == "100"
    assert len(candidate.canonical_identity.source_refs) == 2


@pytest.mark.parametrize("field", ["insured_amount", "currency"])
def test_numeric_source_conflict_retains_candidate_and_formula(field: str) -> None:
    identity = _identity((field,))
    context = _context(replace(_coverage(1, "100"), canonical_identity=identity))
    result = LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), _event(), context)
    candidate = result.candidates[0]
    assert candidate.condition_result == "MATCH"
    assert candidate.estimate.kind == "FORMULA"
    assert candidate.estimate.amount is None
    assert "OPERATIONAL_SOURCE_FIELD_CONFLICT" in candidate.reason_codes
    assert context.coverages[0].insured_amount is not None


def test_name_correction_does_not_block_an_independent_calculation() -> None:
    context = _context(
        replace(_coverage(1, "100"), canonical_identity=_identity(("display_name",)))
    )
    result = LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), _event(), context)
    assert result.candidates[0].estimate.amount == "100"


def test_identity_cannot_point_to_another_knowledge_coverage() -> None:
    identity = _identity()
    with pytest.raises(ValueError, match="coverage identity"):
        replace(_coverage(2, "100"), canonical_identity=identity)
