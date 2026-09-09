"""Private catalog page addresses are independent of native PDF intake capacity."""

from dataclasses import replace
from uuid import UUID

import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.guidance.engine import LocalGuidanceEngine
from familycare_api.guidance.models import GuidanceEvidence, GuidancePrivateCertificate
from familycare_api.guidance.private_adapter import adapt_private_guidance
from familycare_api.guidance_evidence.models import GuidanceEvidenceDetail
from pydantic import ValidationError

from apps.api.tests.test_private_knowledge_engine import HOUSEHOLD_ID, _context, _coverage, _event


def test_large_private_page_keeps_local_candidate_amount_and_summary_address():
    coverage = _coverage(1, "100")

    def located(publication):
        return replace(
            publication,
            citations=tuple(
                replace(citation, page_start=901, page_end=902)
                for citation in publication.citations
            ),
        )

    coverage = replace(
        coverage,
        rules=tuple(located(rule) for rule in coverage.rules),
        calculation=located(coverage.calculation),
    )
    guidance = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID), _event(), adapt_private_guidance(_context(coverage))
    )
    assert len(guidance.candidates) == 1
    estimate = guidance.candidates[0].estimate
    assert estimate.kind == "POINT" and estimate.amount == "100"
    reference = estimate.evidence[0]
    assert reference.page_start == 901 and reference.page_end == 902
    summary = GuidanceEvidenceDetail(
        evidence=reference,
        content_kind="SUMMARY",
        document_label="Sample Terms",
        page_start=901,
        page_end=902,
        text="Wholly synthetic source summary.",
    )
    assert summary.page_end == 902 and summary.content_kind == "SUMMARY"


def test_private_certificate_address_is_not_a_native_pdf_capacity_claim():
    value = GuidancePrivateCertificate(
        catalog_import_run_id=UUID(int=1),
        coverage_id=UUID(int=2),
        document_alias="Synthetic certificate",
        page_start=901,
        page_end=902,
    )
    assert value.page_end == 902


def test_native_page_bound_remains_in_runtime_and_neutral_schema():
    invalid = {
        "kind": "OPERATIONAL_EVIDENCE",
        "evidence_id": str(UUID(int=3)),
        "page_start": 501,
        "page_end": 501,
    }
    with pytest.raises(ValidationError):
        GuidanceEvidence.model_validate(invalid)
    private = invalid | {"kind": "TERMS_SECTION", "page_start": 901, "page_end": 902}
    assert GuidanceEvidence.model_validate(private).page_start == 901
    schema = GuidanceEvidence.model_json_schema()
    assert "maximum" not in schema["properties"]["page_start"]
    native = schema["allOf"][0]
    assert native["if"]["properties"]["kind"]["const"] == "OPERATIONAL_EVIDENCE"
    assert native["then"]["properties"]["page_end"]["maximum"] == 500
