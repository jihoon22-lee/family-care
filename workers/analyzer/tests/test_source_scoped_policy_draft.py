"""Source-scoped drafts separate absent issuer identity from supported policy facts."""

from dataclasses import replace

import pytest
from familycare_worker.ai.policy_draft_normalization import (
    CERTIFICATE_TITLE_NORMALIZATION_REVISION,
    SOURCE_SCOPED_NORMALIZATION_REVISION,
    normalize_policy_draft,
)
from familycare_worker.ai.policy_pipeline import verify_structured_policy_batch
from familycare_worker.ai.provider import ProviderResponse
from familycare_worker.ai.range_grounding import ground_range_candidate
from familycare_worker.ai.schemas import PolicyCandidate

from workers.analyzer.tests.test_policy_draft_normalization import (
    _batch,
    _candidate,
    _envelope,
    _pack,
)


def _draft(envelope, source):
    return normalize_policy_draft(
        _batch(envelope, source), envelope, revision=SOURCE_SCOPED_NORMALIZATION_REVISION
    )


@pytest.mark.parametrize("present", [True, False])
def test_unconfirmed_issuer_is_omitted_only_from_new_draft_with_loss_receipt(present):
    envelope = _envelope("Sample Plan_보험증권\n보호제도 예시: Sample Insurer")
    values = {"product_name": "Sample Plan"}
    if present:
        values["insurer"] = "Sample Insurer"
    source = _candidate(envelope.evidence[0], **values)
    before = source.model_dump_json()
    result = _draft(envelope, source)
    assert result.partial and result.revision == SOURCE_SCOPED_NORMALIZATION_REVISION
    assert result.batch.candidates[0].fields == (source.fields[0],)
    assert [(a.reason, a.field_id) for a in result.adjustments] == [
        ("ISSUER_UNCONFIRMED", "insurer")
    ]
    assert source.model_dump_json() == before
    assert "status" not in result.batch.candidates[0].model_dump()
    legacy = normalize_policy_draft(
        _batch(envelope, source), envelope, revision=CERTIFICATE_TITLE_NORMALIZATION_REVISION
    )
    assert bool(legacy.batch.candidates) == present
    if present:
        assert legacy.batch.candidates[0] == source


@pytest.mark.parametrize("label", ["보험사", "보험회사", "발급기관", "Insurer", "Issuer"])
def test_explicit_issuer_stays_in_new_draft_without_invented_unknown_value(label):
    envelope = _envelope(f"{label}: Sample Insurer\nSample Plan_보험증권")
    source = _candidate(envelope.evidence[0], insurer="Sample Insurer", product_name="Sample Plan")
    result = _draft(envelope, source)
    assert result.batch.candidates == (source,)
    assert result.adjustments == () and not result.partial


@pytest.mark.parametrize(
    "text",
    [
        "다른 보험사: Sample Insurer",
        "보험사: Sample Insurer Plus",
        "예시 보험사: Sample Insurer",
        "보험사: Other Insurer\nSample Insurer",
    ],
)
def test_incidental_or_other_company_is_never_issuer_grounding(text):
    envelope = _envelope(text + "\nSample Plan_보험증권")
    source = _candidate(envelope.evidence[0], insurer="Sample Insurer", product_name="Sample Plan")
    result = _draft(envelope, source)
    assert [f.field_id for f in result.batch.candidates[0].fields] == ["product_name"]
    candidate = PolicyCandidate(
        candidate_id=source.candidate_id,
        candidate_kind="policy_contract",
        fields=source.fields,
        status="AI_VERIFIED",
        issue_codes=(),
        provider_request_ids=(),
    )
    assert (
        ground_range_candidate(
            candidate, envelope.evidence, allow_certificate_title=True, require_issuer_context=True
        ).status
        == "NEEDS_REVIEW"
    )


@pytest.mark.parametrize("fault", ["product", "terms", "context"])
def test_optional_issuer_does_not_relax_product_or_primary_policy_source(fault):
    envelope = _envelope("Sample Plan_보험증권")
    if fault == "terms":
        envelope = _pack(
            (replace(envelope.evidence[0], source_role="terms", document_kind="terms"),)
        )
    elif fault == "context":
        other = replace(envelope.evidence[0], node_id="f" * 64, primary=False)
        from uuid import uuid4

        envelope = _pack(
            (replace(envelope.evidence[0], text="No product", end=10, evidence_id=uuid4()), other)
        )
    source = _candidate(
        envelope.evidence[-1], product_name="Wrong Plan" if fault == "product" else "Sample Plan"
    )
    result = _draft(envelope, source)
    assert result.batch.candidates == () and result.partial
    assert result.batch.ranges[0].outcome == "UNRESOLVED"


@pytest.mark.parametrize(
    "allow,decision,expected",
    [
        (False, "approved", "NEEDS_REVIEW"),
        (True, "approved", "AI_VERIFIED"),
        (True, "needs_review", "NEEDS_REVIEW"),
        (True, "rejected", "rejected"),
    ],
)
def test_reduced_parent_requires_fresh_independent_approval_and_explicit_new_mode(
    allow, decision, expected
):
    envelope = _envelope("Sample Plan_보험증권")
    source = _candidate(envelope.evidence[0], product_name="Sample Plan")
    calls = []

    class Provider:
        def complete(self, **kwargs):
            calls.append(kwargs)
            assert kwargs["input_payload"]["candidates"] == [source.model_dump(mode="json")]
            return ProviderResponse(
                payload={
                    "schema_version": "2",
                    "decisions": [
                        {
                            "schema_version": "1",
                            "candidate_id": str(source.candidate_id),
                            "decision": decision,
                            "evidence_ids": [str(envelope.evidence[0].evidence_id)],
                            "issue_codes": [],
                        }
                    ],
                },
                request_id="synthetic-fresh-verifier",
            )

    result = verify_structured_policy_batch(
        candidates=(source,),
        structurer_request_id="synthetic-retained-response",
        evidence=envelope.evidence,
        provider=Provider(),
        verifier_model="synthetic-model",
        allow_unclassified_enrollment=True,
        allow_unconfirmed_insurer=allow,
    )
    assert len(calls) == 1 and result.candidates[0].status == expected
    assert result.candidates[0].provider_request_ids == (
        "synthetic-retained-response",
        "synthetic-fresh-verifier",
    )
    assert result.candidates[0].fields == source.fields
