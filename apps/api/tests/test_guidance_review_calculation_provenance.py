"""Review-scoped calculations retain their actual authority through the shared engine."""

from dataclasses import replace
from uuid import UUID

import pytest
from familycare_api.guidance.calculation_source import CalculationSourceError
from familycare_api.guidance.models import GuidanceSemanticEvidence

from apps.api.tests.test_guidance_calculation_source import evaluate, input_value, semantic

REVIEW_ID = UUID("11111111-2222-4333-8444-555555555555")


def _review(source):
    return replace(
        source,
        citations=tuple(
            replace(
                citation,
                evidence=GuidanceSemanticEvidence.model_validate(
                    citation.evidence.model_dump() | {"review_job_id": REVIEW_ID}
                ),
            )
            for citation in source.citations
        ),
    )


def test_review_correction_runs_existing_decimal_engine_with_review_authority():
    _, current, source = semantic()
    source = _review(source)
    binding, result = evaluate(
        source,
        {
            "Rider.insured_amount": input_value(100),
            "MedicalEvent.admission_days": input_value(5, "DAYS", None),
        },
    )
    assert result.amount == 300
    refs = {(ref.source_kind, str(ref.source_id)) for ref in binding.source.source_refs}
    assert ("GUIDANCE_REVIEW_JOB", str(REVIEW_ID)) in refs
    assert ("GUIDANCE_REVIEW_PUBLICATION", str(current.publication_id)) in refs
    assert not any(kind == "SEMANTIC_PUBLICATION" for kind, _ in refs)


def test_mixed_global_and_review_citation_authority_cannot_produce_amount():
    _, _, source = semantic()
    changed = _review(source)
    assert len(changed.citations) > 1
    mixed = replace(changed, citations=(source.citations[0], *changed.citations[1:]))
    with pytest.raises(CalculationSourceError):
        evaluate(mixed)


def test_review_identity_must_be_nonzero():
    _, _, source = semantic()
    with pytest.raises(ValueError):
        GuidanceSemanticEvidence.model_validate(
            source.citations[0].evidence.model_dump() | {"review_job_id": UUID(int=0)}
        )
