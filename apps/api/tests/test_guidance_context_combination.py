"""Each source set remains represented in a combined decision revision."""

from dataclasses import replace
from uuid import UUID

import pytest
from familycare_api.guidance.domain import GuidanceContext
from familycare_api.guidance.models import GuidanceVersions
from familycare_api.guidance.repository import combine_guidance_contexts


def _source_variants(*, related_private=False):
    from familycare_api.guidance.domain import GuidancePayoutCaseInput
    from familycare_api.guidance.private_adapter import adapt_private_guidance

    from apps.api.tests.test_guidance_canonical_identity import _identity
    from apps.api.tests.test_guidance_local_event_engine import context
    from apps.api.tests.test_private_knowledge_engine import _context, _coverage, _expression_rule

    identity = _identity()
    private_source = _coverage(1, "100")
    if related_private:
        private_source = replace(
            private_source,
            rules=(
                _expression_rule(
                    111,
                    kind="eligibility",
                    expression={
                        "op": "range",
                        "field": "MedicalEvent.admission_days",
                        "value": {"min": 1, "max": 36500},
                        "unit": "days",
                    },
                    input_field_paths=("MedicalEvent.admission_days",),
                    reason_code="SYNTHETIC_ADMISSION",
                ),
            ),
        )
    private = adapt_private_guidance(
        _context(
            replace(
                private_source,
                canonical_identity=identity,
                insured_amount=None,
                certificate_amount_decision="UNKNOWN",
                certificate_amount_evidence_state="UNAVAILABLE",
                status_intervals=(),
            )
        )
    )
    original = context()
    source = original.coverages[0]
    operational = replace(
        original,
        coverages=(
            replace(
                source,
                ref=identity.ref,
                canonical_identity=identity,
                rules=(),
                calculation=None,
                cases=(
                    GuidancePayoutCaseInput(
                        case_key="synthetic-original-daily",
                        rules=source.rules,
                        calculation=source.calculation,
                        benefit_type="FIXED",
                    ),
                ),
            ),
        ),
    )
    return operational, private


def _evaluate_source_context(context, *, confirmed_classification=False):
    from familycare_api.common.scope import HouseholdScope
    from familycare_api.guidance.engine import LocalGuidanceEngine

    from apps.api.tests.test_private_knowledge_engine import HOUSEHOLD_ID, _event

    return LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID),
        replace(
            _event(),
            facts=_event().facts if confirmed_classification else {},
            situation="5일 입원했습니다.",
        ),
        context,
    )


def test_nonempty_unrelated_private_rule_does_not_hide_original_operational_300():
    operational, private = _source_variants()
    assert _evaluate_source_context(operational).candidates[0].estimate.amount == "300"
    assert not _evaluate_source_context(private).candidates
    combined = _evaluate_source_context(combine_guidance_contexts(operational, private))
    assert len(combined.candidates) == 1
    assert combined.candidates[0].estimate.amount == "300"
    assert combined.support.total_coverages == 1


def test_private_required_mismatch_does_not_become_an_operational_condition():
    operational, private = _source_variants()
    source = private.coverages[0]
    rule = source.rules[0]
    private = replace(
        private,
        coverages=(
            replace(
                source,
                rules=(
                    replace(
                        rule,
                        rule_document={
                            **rule.rule_document,
                            "expression": {
                                "op": "equals",
                                "field": "MedicalEvent.classification",
                                "value": "other_category",
                            },
                        },
                    ),
                ),
            ),
        ),
    )
    assert not _evaluate_source_context(private, confirmed_classification=True).candidates
    combined = _evaluate_source_context(
        combine_guidance_contexts(operational, private),
        confirmed_classification=True,
    )
    assert len(combined.candidates) == 1
    assert combined.candidates[0].estimate.amount == "300"


def test_two_applicable_sources_remain_distinct_without_summing_or_replacing_either():
    from familycare_api.guidance.private_adapter import adapt_private_guidance

    from apps.api.tests.test_private_knowledge_engine import _context, _coverage

    operational, private = _source_variants(related_private=True)
    amount_source = adapt_private_guidance(_context(_coverage(1, "100"))).coverages[0]
    private = replace(
        private,
        coverages=(
            replace(
                private.coverages[0],
                insured_amount=amount_source.insured_amount,
                certificate_amount_decision=amount_source.certificate_amount_decision,
                certificate_amount_evidence_state=amount_source.certificate_amount_evidence_state,
                contract_amount=amount_source.contract_amount,
            ),
        ),
    )
    combined = _evaluate_source_context(combine_guidance_contexts(operational, private))
    candidate = combined.candidates[0]
    assert len(combined.candidates) == 1 and len(candidate.cases) == 2
    assert {case.estimate.amount for case in candidate.cases} == {"100", "300"}
    assert len({case.case_key for case in candidate.cases}) == 2
    assert len({case.estimate.trace.publication_id for case in candidate.cases}) == 2
    assert candidate.estimate.amount is candidate.estimate.lower is candidate.estimate.upper is None
    assert candidate.case_relation == "UNRESOLVED"


@pytest.mark.parametrize("field", ["insured_amount", "currency"])
def test_canonical_numeric_conflict_remains_on_every_source_case(field):
    from apps.api.tests.test_guidance_canonical_identity import _identity

    operational, private = _source_variants(related_private=True)
    conflict = _identity((field,))
    operational = replace(
        operational,
        coverages=(
            replace(
                operational.coverages[0],
                canonical_identity=conflict,
            ),
        ),
    )
    private = replace(
        private,
        coverages=(
            replace(
                private.coverages[0],
                canonical_identity=conflict,
            ),
        ),
    )
    candidate = _evaluate_source_context(
        combine_guidance_contexts(operational, private)
    ).candidates[0]
    assert len(candidate.cases) == 2
    assert all(
        case.estimate.kind == "FORMULA" and case.estimate.amount is None for case in candidate.cases
    )
    assert candidate.canonical_identity == conflict


def test_related_partial_private_keeps_its_own_amount_status_and_original_operational_case():
    operational, private = _source_variants(related_private=True)
    combined = _evaluate_source_context(combine_guidance_contexts(operational, private))
    assert len(combined.candidates) == 1
    candidate = combined.candidates[0]
    assert len(candidate.cases) == 2
    cases = {case.source_ref.kind: case for case in candidate.cases}
    original, partial = cases["OPERATIONAL_RIDER"], cases["PRIVATE_KNOWLEDGE_COVERAGE"]
    assert original.estimate.amount == "300"
    assert partial.estimate.kind == "FORMULA" and partial.estimate.amount is None
    assert original.contract_amount.amount == "100"
    assert partial.contract_amount.amount is None
    assert original.freshness == "CONFIRMED_AT_EVENT"
    assert partial.freshness == "DOCUMENT_CONTINUITY"
    assert "DOCUMENT_CONTINUITY_ASSUMED" in partial.assumptions
    assert "DOCUMENT_CONTINUITY_ASSUMED" not in original.assumptions
    assert candidate.contract_amount is None
    assert candidate.freshness == "STATUS_UNRESOLVED"
    assert candidate.estimate.amount is None
    assert candidate.case_relation == "UNRESOLVED"
    assert combined.support.total_coverages == 1
    assert all(e.kind == "SEMANTIC_CITATION" for e in original.estimate.evidence)
    assert all(e.kind == "TERMS_SECTION" for e in partial.estimate.evidence)


def test_operational_revision_is_retained_when_a_private_context_exists():
    private = GuidanceContext(
        household_space_id=UUID(int=1),
        family_member_id=UUID(int=2),
        coverages=(),
        versions=GuidanceVersions(status_digest="a" * 64),
    )
    operational = replace(private, versions=GuidanceVersions(status_digest="b" * 64))
    first = combine_guidance_contexts(operational, private)
    changed = replace(operational, versions=GuidanceVersions(status_digest="c" * 64))
    second = combine_guidance_contexts(changed, private)
    assert first.versions.status_digest != second.versions.status_digest
    assert combine_guidance_contexts(operational, private).versions == first.versions


def test_partial_private_source_does_not_hide_usable_operational_coverage():
    from familycare_api.guidance.private_adapter import adapt_private_guidance

    from apps.api.tests.test_guidance_canonical_identity import _identity
    from apps.api.tests.test_private_knowledge_engine import _context, _coverage

    complete = adapt_private_guidance(
        _context(replace(_coverage(1, "100"), canonical_identity=_identity()))
    )
    coverage = complete.coverages[0]
    operational = replace(
        complete, coverages=(replace(coverage, ref=coverage.canonical_identity.ref),)
    )
    private = replace(complete, coverages=(replace(coverage, rules=(), calculation=None),))
    result = combine_guidance_contexts(operational, private)
    assert len(result.coverages) == 2
    evaluated = _evaluate_source_context(result)
    # Inputs retain both source records even when only one can produce a candidate.
    assert all(
        source.canonical_identity == coverage.canonical_identity for source in result.coverages
    )
    assert evaluated.support.total_coverages == 1
