"""Exact synthetic certificate titles retain their evidence only in the new revision."""

from dataclasses import replace

import pytest
from familycare_worker.ai.policy_draft_normalization import (
    CERTIFICATE_TITLE_NORMALIZATION_REVISION,
    POLICY_DRAFT_NORMALIZATION_REVISION,
    PolicyDraftInvalid,
    _supported,
    normalize_policy_draft,
)
from familycare_worker.ai.range_grounding import ground_range_candidate
from familycare_worker.ai.schemas import PolicyCandidate

from workers.analyzer.tests.test_policy_draft_normalization import _batch, _candidate, _envelope


def _verified(source):
    return PolicyCandidate(
        candidate_id=source.candidate_id,
        candidate_kind=source.candidate_kind,
        status="AI_VERIFIED",
        fields=source.fields,
        issue_codes=(),
        provider_request_ids=(),
    )


@pytest.mark.parametrize(
    "text",
    [
        "Sample Plan (A)_보험증권",
        "Sample Plan (A)_보험증권 \t",
        "보험증권\nSample Plan (A)_보험증권\nSample Insurer",
        "상품명: Sample Plan (A)_보험증권\r\n",
    ],
)
def test_exact_line_final_certificate_label_is_opt_in(text):
    envelope = _envelope(text)
    candidate = _verified(_candidate(envelope.evidence[0], product_name="Sample Plan (A)"))
    before = candidate.model_dump_json()
    assert ground_range_candidate(candidate, envelope.evidence).status == "NEEDS_REVIEW"
    result = ground_range_candidate(candidate, envelope.evidence, allow_certificate_title=True)
    assert result == candidate
    assert candidate.model_dump_json() == before


@pytest.mark.parametrize(
    "text",
    [
        "Sample Plan (B)_보험증권",
        "Sample Plan (A)Plus_보험증권",
        "Sample Plan (A)_보험증권Plus",
        "Sample Plan (A)_보험증권_별첨",
        "Sample Plan (A)_보험증권 추가",
        "Sample Plan (A)_보험증권.",
        "Sample Plan (A)_보험증권명",
        "Sample Plan (A)_보험증권_보험증권",
        "OtherSample Plan (A)_보험증권",
        "Other_Sample Plan (A)_보험증권",
        "Sample Plan (A)_\n보험증권",
        "Sample Plan (A)_보험 증권",
    ],
)
def test_title_exception_does_not_accept_variants_prefixes_or_extra_suffixes(text):
    envelope = _envelope(text)
    candidate = _verified(_candidate(envelope.evidence[0], product_name="Sample Plan (A)"))
    result = ground_range_candidate(candidate, envelope.evidence, allow_certificate_title=True)
    assert result.status == "NEEDS_REVIEW"
    assert "INVENTED_FIELD" in result.issue_codes
    assert result.fields == candidate.fields


@pytest.mark.parametrize(
    ("kind", "field"),
    [("policy_contract", "insurer"), ("rider", "rider_name"), ("rider", "product_name")],
)
def test_title_exception_does_not_expand_other_fields_or_rider_authority(kind, field):
    envelope = _envelope("Sample Plan (A)_보험증권")
    candidate = _verified(_candidate(envelope.evidence[0], kind=kind, **{field: "Sample Plan (A)"}))
    result = ground_range_candidate(candidate, envelope.evidence, allow_certificate_title=True)
    assert result.status == "NEEDS_REVIEW"


@pytest.mark.parametrize("status", ["NEEDS_REVIEW", "rejected"])
def test_supported_title_never_promotes_an_unapproved_candidate(status):
    envelope = _envelope("Sample Plan (A)_보험증권")
    candidate = _verified(_candidate(envelope.evidence[0], product_name="Sample Plan (A)"))
    candidate = candidate.model_copy(update={"status": status})
    assert (
        ground_range_candidate(candidate, envelope.evidence, allow_certificate_title=True)
        == candidate
    )


@pytest.mark.parametrize("primary,role", [(False, "policy"), (True, "terms")])
def test_title_still_requires_primary_policy_evidence(primary, role):
    envelope = _envelope("Sample Plan (A)_보험증권")
    candidate = _verified(_candidate(envelope.evidence[0], product_name="Sample Plan (A)"))
    evidence = (
        replace(envelope.evidence[0], primary=primary, source_role=role, document_kind=role),
    )
    result = ground_range_candidate(candidate, evidence, allow_certificate_title=True)
    assert result.status == "NEEDS_REVIEW"


def test_title_in_uncited_evidence_cannot_support_the_product():
    envelope = _envelope("Sample Insurer", "Sample Plan (A)_보험증권")
    candidate = _verified(_candidate(envelope.evidence[0], product_name="Sample Plan (A)"))
    result = ground_range_candidate(candidate, envelope.evidence, allow_certificate_title=True)
    assert result.status == "NEEDS_REVIEW"


def test_v2_normalization_retains_exact_values_and_citations_without_approval():
    envelope = _envelope("Sample Insurer\nSample Plan (A)_보험증권")
    source = _candidate(
        envelope.evidence[0], insurer="Sample Insurer", product_name="Sample Plan (A)"
    )
    original = _batch(envelope, source)
    before = original.model_dump_json()
    legacy = normalize_policy_draft(original, envelope)
    assert legacy.revision == POLICY_DRAFT_NORMALIZATION_REVISION
    assert legacy.partial and legacy.batch.candidates == ()
    assert legacy == normalize_policy_draft(
        original, envelope, revision=POLICY_DRAFT_NORMALIZATION_REVISION
    )
    assert not _supported(source, source.fields[1], envelope, None)
    result = normalize_policy_draft(
        original, envelope, revision=CERTIFICATE_TITLE_NORMALIZATION_REVISION
    )
    assert result.revision == CERTIFICATE_TITLE_NORMALIZATION_REVISION
    assert result.batch == original and result.batch is not original
    assert result.adjustments == () and not result.partial
    assert original.model_dump_json() == before
    assert "status" not in result.batch.candidates[0].model_dump()


@pytest.mark.parametrize("revision", ["", "policy-draft-normalization-v3", "unknown"])
def test_unknown_normalization_revision_is_rejected(revision):
    envelope = _envelope("Sample Insurer\nSample Plan (A)_보험증권")
    source = _candidate(
        envelope.evidence[0], insurer="Sample Insurer", product_name="Sample Plan (A)"
    )
    with pytest.raises(PolicyDraftInvalid):
        normalize_policy_draft(_batch(envelope, source), envelope, revision=revision)


def test_existing_word_boundary_proof_remains_accepted_with_both_revisions():
    envelope = _envelope("Sample Insurer\nSample Plan (A)")
    source = _candidate(
        envelope.evidence[0], insurer="Sample Insurer", product_name="Sample Plan (A)"
    )
    original = _batch(envelope, source)
    for revision in (POLICY_DRAFT_NORMALIZATION_REVISION, CERTIFICATE_TITLE_NORMALIZATION_REVISION):
        result = normalize_policy_draft(original, envelope, revision=revision)
        assert result.batch == original and not result.partial
        assert result.revision == revision
