"""Local draft reduction preserves failed facts and cannot grant publication authority."""

import hashlib
import json
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest
from familycare_worker.ai.policy_draft_normalization import (
    PolicyDraftInvalid,
    normalize_policy_draft,
)
from familycare_worker.ai.policy_ranges import (
    RANGE_ENVELOPE_REVISION,
    PolicyRangeEnvelope,
    RangeEvidenceSlice,
)
from familycare_worker.ai.range_grounding import ground_range_candidate
from familycare_worker.ai.range_structurer import PolicyRangeBatch, RangeDisposition
from familycare_worker.ai.schemas import CandidateField, PolicyCandidate, StructurerCandidate

from workers.analyzer.tests.test_policy_table_grounding import _input


def _envelope(*texts: str) -> PolicyRangeEnvelope:
    document = uuid4()
    return _pack(
        tuple(
            RangeEvidenceSlice(
                evidence_id=uuid4(),
                document_version_id=document,
                page=1,
                text=text,
                bbox=None,
                node_id=f"{position + 1:064x}",
                start=0,
                end=len(text),
                primary=True,
                source_role="policy",
                document_kind="policy",
            )
            for position, text in enumerate(texts)
        )
    )


def _pack(evidence: tuple[RangeEvidenceSlice, ...]) -> PolicyRangeEnvelope:
    primary = tuple(item.evidence_id for item in evidence if item.primary)
    chunks = tuple(f"{position + 100:064x}" for position in range(len(primary)))
    identity = json.dumps(
        [RANGE_ENVELOPE_REVISION, chunks, [dict(item.to_provider_payload()) for item in evidence]],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return PolicyRangeEnvelope(hashlib.sha256(identity).hexdigest(), chunks, primary, evidence)


def _candidate(source: RangeEvidenceSlice, *, kind: str = "policy_contract", **values: Any):
    return StructurerCandidate(
        schema_version="1",
        candidate_id=uuid4(),
        candidate_kind=kind,
        fields=tuple(
            CandidateField(field_id=key, value=value, evidence_ids=(source.evidence_id,))
            for key, value in values.items()
        ),
    )


def _batch(envelope, *candidates, assignments=None):
    if assignments is None:
        assignments = {envelope.primary_chunk_ids[0]: tuple(c.candidate_id for c in candidates)}
    return PolicyRangeBatch(
        schema_version="3",
        candidates=candidates,
        ranges=tuple(
            RangeDisposition(
                chunk_id=key,
                candidate_ids=assignments.get(key, ()),
                outcome="CANDIDATES" if assignments.get(key) else "NO_ENROLLMENT_FACTS",
            )
            for key in envelope.primary_chunk_ids
        ),
    )


def _fields(candidate):
    return {field.field_id: field for field in candidate.fields}


def _reasons(result):
    return {item.reason for item in result.adjustments}


def test_optional_date_is_omitted_only_from_separate_draft_with_receipt():
    envelope = _envelope("보험증권 Sample Insurer Sample Plan 계약일: 2025-01-01")
    source = _candidate(
        envelope.evidence[0],
        insurer="Sample Insurer",
        product_name="Sample Plan",
        contract_start="2025-01-01",
    )
    original = _batch(envelope, source)
    before = original.model_dump_json()
    result = normalize_policy_draft(original, envelope)
    assert original.model_dump_json() == before
    assert result.batch is not original and result.partial
    assert set(_fields(result.batch.candidates[0])) == {"insurer", "product_name"}
    assert result.batch.ranges == original.ranges
    assert [(a.field_id, a.reason) for a in result.adjustments] == [
        ("contract_start", "OPTIONAL_FIELD_UNSUPPORTED")
    ]


def test_missing_key_is_exact_name_copy_with_identical_evidence_and_no_approval():
    envelope = _envelope("담보명: Sample Rider | 가입금액 20원")
    source = _candidate(
        envelope.evidence[0], kind="rider", rider_name="Sample Rider", sum_assured=20
    )
    original = _batch(envelope, source)
    result = normalize_policy_draft(original, envelope)
    fields = _fields(result.batch.candidates[0])
    assert fields["rider_key"].value == fields["rider_name"].value == "Sample Rider"
    assert fields["rider_key"].evidence_ids == fields["rider_name"].evidence_ids
    assert "rider_key" not in _fields(source)
    assert "status" not in result.batch.candidates[0].model_dump()
    assert _reasons(result) == {"RIDER_KEY_DERIVED_FROM_NAME"}
    assert not result.partial


@pytest.mark.parametrize("missing", [False, True])
def test_unproven_required_product_is_never_rewritten_or_silently_completed(missing):
    envelope = _envelope("보험증권 Sample Insurer Sample Plan")
    values = {"insurer": "Sample Insurer"}
    if not missing:
        values["product_name"] = "Invented Plan"
    source = _candidate(envelope.evidence[0], **values)
    result = normalize_policy_draft(_batch(envelope, source), envelope)
    assert result.batch.candidates == () and result.partial
    assert result.batch.ranges[0].outcome == "UNRESOLVED"
    assert result.batch.ranges[0].candidate_ids == ()
    assert ("REQUIRED_FIELD_MISSING" if missing else "REQUIRED_FIELD_UNSUPPORTED") in _reasons(
        result
    )
    assert "CANDIDATE_UNREFERENCED" in _reasons(result)
    assert all(a.candidate_id in {None, source.candidate_id} for a in result.adjustments)


def test_existing_unsupported_rider_key_is_not_replaced():
    envelope = _envelope("담보명: Sample Rider 가입금액 20원")
    source = _candidate(
        envelope.evidence[0], kind="rider", rider_name="Sample Rider", rider_key="other"
    )
    result = normalize_policy_draft(_batch(envelope, source), envelope)
    assert result.batch.candidates == ()
    assert "REQUIRED_FIELD_UNSUPPORTED" in _reasons(result)
    assert "RIDER_KEY_DERIVED_FROM_NAME" not in _reasons(result)
    assert _fields(source)["rider_key"].value == "other"


def test_removed_only_primary_proof_makes_that_range_unresolved_without_borrowing():
    envelope = _envelope("보험증권 Sample Insurer Sample Plan", "계약일: 2025-01-01")
    source = _candidate(envelope.evidence[0], insurer="Sample Insurer", product_name="Sample Plan")
    source = source.model_copy(
        update={
            "fields": (
                *source.fields,
                CandidateField(
                    field_id="contract_start",
                    value="2025-01-01",
                    evidence_ids=(envelope.evidence[1].evidence_id,),
                ),
            )
        }
    )
    original = _batch(
        envelope,
        source,
        assignments={key: (source.candidate_id,) for key in envelope.primary_chunk_ids},
    )
    result = normalize_policy_draft(original, envelope)
    assert len(result.batch.candidates) == 1
    assert [item.outcome for item in result.batch.ranges] == ["CANDIDATES", "UNRESOLVED"]
    assert result.batch.ranges[1].candidate_ids == ()
    assert all(
        envelope.evidence[1].evidence_id not in field.evidence_ids
        for field in result.batch.candidates[0].fields
    )
    assert "RANGE_PRIMARY_UNSUPPORTED" in _reasons(result)


def test_losing_one_required_candidate_preserves_range_failure_and_records_good_orphan():
    envelope = _envelope("보험증권 Sample Insurer Sample Plan\n담보명: Sample Rider 가입금액 20원")
    bad = _candidate(envelope.evidence[0], insurer="Sample Insurer", product_name="Invented Plan")
    good = _candidate(envelope.evidence[0], kind="rider", rider_name="Sample Rider")
    result = normalize_policy_draft(_batch(envelope, bad, good), envelope)
    assert result.batch.candidates == ()
    assert result.batch.ranges[0].outcome == "UNRESOLVED"
    assert {a.candidate_id for a in result.adjustments if a.reason == "CANDIDATE_UNREFERENCED"} == {
        bad.candidate_id,
        good.candidate_id,
    }


@pytest.mark.parametrize("outcome", ["NO_ENROLLMENT_FACTS", "UNRESOLVED"])
def test_empty_range_outcomes_are_preserved(outcome):
    envelope = _envelope("보험증권 안내")
    batch = PolicyRangeBatch(
        schema_version="3",
        candidates=(),
        ranges=(
            RangeDisposition(
                chunk_id=envelope.primary_chunk_ids[0],
                outcome=outcome,
                candidate_ids=(),
            ),
        ),
    )
    result = normalize_policy_draft(batch, envelope)
    assert result.batch == batch and result.adjustments == ()
    assert result.partial is (outcome == "UNRESOLVED")


@pytest.mark.parametrize(
    "corruption",
    ["unknown_evidence", "unknown_range", "duplicate_evidence", "cross_source", "changed_envelope"],
)
def test_invalid_scope_fails_closed_with_fixed_error(corruption):
    envelope = _envelope("보험증권 Sample Insurer Sample Plan", "보험증권 안내")
    source = _candidate(envelope.evidence[0], insurer="Sample Insurer", product_name="Sample Plan")
    batch = _batch(envelope, source)
    if corruption == "unknown_evidence":
        source = source.model_copy(
            update={
                "fields": tuple(
                    f.model_copy(update={"evidence_ids": (uuid4(),)}) for f in source.fields
                )
            }
        )
        batch = _batch(envelope, source)
    elif corruption == "unknown_range":
        batch = batch.model_copy(
            update={
                "ranges": (
                    batch.ranges[0].model_copy(update={"chunk_id": "f" * 64}),
                    batch.ranges[1],
                )
            }
        )
    elif corruption == "duplicate_evidence":
        envelope = replace(envelope, evidence=(envelope.evidence[0], envelope.evidence[0]))
    elif corruption == "cross_source":
        envelope = _pack(
            (envelope.evidence[0], replace(envelope.evidence[1], document_version_id=uuid4()))
        )
    else:
        envelope = replace(
            envelope,
            evidence=(replace(envelope.evidence[0], text="Invented Plan"), envelope.evidence[1]),
        )
    with pytest.raises(PolicyDraftInvalid, match="^POLICY_DRAFT_INVALID$"):
        normalize_policy_draft(batch, envelope)


def test_non_policy_primary_cannot_support_candidate_but_is_not_invalid_envelope():
    envelope = _envelope("Sample Insurer Sample Plan")
    envelope = _pack((replace(envelope.evidence[0], source_role="terms", document_kind="terms"),))
    candidate = _candidate(
        envelope.evidence[0], insurer="Sample Insurer", product_name="Sample Plan"
    )
    result = normalize_policy_draft(_batch(envelope, candidate), envelope)
    assert result.batch.candidates == ()
    assert result.batch.ranges[0].outcome == "UNRESOLVED" and result.partial
    assert "REQUIRED_FIELD_UNSUPPORTED" in _reasons(result)


def test_non_policy_empty_ranges_and_unknown_context_do_not_block_valid_policy():
    envelope = _envelope("보험증권 Sample Insurer Sample Plan", "보험약관 안내", "문맥 안내")
    envelope = _pack(
        (
            envelope.evidence[0],
            replace(envelope.evidence[1], source_role="terms", document_kind="terms"),
            replace(
                envelope.evidence[2], primary=False, source_role="unknown", document_kind="terms"
            ),
        )
    )
    candidate = _candidate(
        envelope.evidence[0], insurer="Sample Insurer", product_name="Sample Plan"
    )
    batch = _batch(envelope, candidate)
    result = normalize_policy_draft(batch, envelope)
    assert result.batch == batch and not result.partial and not result.adjustments


def test_supported_optional_fields_and_existing_key_are_unchanged():
    envelope = _envelope("Sample Rider 정액 가입금액 20원 보장개시일 2025-01-01")
    candidate = _candidate(
        envelope.evidence[0],
        kind="rider",
        rider_name="Sample Rider",
        rider_key="sample-rider",
        benefit_type="fixed",
        sum_assured=20,
        currency="KRW",
        coverage_start="2025-01-01",
    )
    original = _batch(envelope, candidate)
    result = normalize_policy_draft(original, envelope)
    assert result.batch == original and not result.partial and not result.adjustments


def test_missing_key_name_requires_enrollment_proof_not_only_incidental_text():
    envelope = _envelope("보험증권 Sample Insurer Sample Plan")
    candidate = _candidate(envelope.evidence[0], kind="rider", rider_name="Sample Plan")
    result = normalize_policy_draft(_batch(envelope, candidate), envelope)
    assert result.batch.candidates == ()
    assert "RIDER_KEY_DERIVED_FROM_NAME" not in _reasons(result)


def test_unsupported_classification_is_removed_without_value_rewriting():
    envelope = _envelope("담보명: Sample Rider 가입금액 20원")
    candidate = _candidate(
        envelope.evidence[0],
        kind="rider",
        rider_name="Sample Rider",
        rider_key="sample-rider",
        benefit_type="fixed",
    )
    result = normalize_policy_draft(_batch(envelope, candidate), envelope)
    assert "benefit_type" not in _fields(result.batch.candidates[0])
    assert _fields(candidate)["benefit_type"].value == "fixed"
    assert result.partial


def test_draft_is_still_rejected_by_existing_verifier_and_validator():
    from familycare_worker.ai.schemas import VerifierDecision
    from familycare_worker.ai.validator import validate_candidate

    envelope = _envelope("담보명: Sample Rider 가입금액 20원")
    candidate = _candidate(envelope.evidence[0], kind="rider", rider_name="Sample Rider")
    normalized = normalize_policy_draft(_batch(envelope, candidate), envelope).batch.candidates[0]
    denied = VerifierDecision(
        schema_version="1",
        candidate_id=candidate.candidate_id,
        decision="rejected",
        evidence_ids=(envelope.evidence[0].evidence_id,),
        issue_codes=("LOW_CONFIDENCE",),
    )
    assert "LOW_CONFIDENCE" in validate_candidate(
        candidate=normalized,
        verifier=denied,
        evidence=envelope.evidence,
        allow_unclassified_enrollment=True,
    )


def test_foreign_local_node_identity_is_not_used_for_table_proof():
    candidate, evidence, nodes, _ = _input()
    envelope = _pack(evidence)
    source = StructurerCandidate(
        schema_version="1",
        candidate_id=candidate.candidate_id,
        candidate_kind="rider",
        fields=candidate.fields,
    )
    row = _fields(source)["rider_name"].evidence_ids[0]
    chunk = envelope.primary_chunk_ids[envelope.primary_evidence_ids.index(row)]
    nodes = {key: dict(value) for key, value in nodes.items()}
    node_id = next(item.node_id for item in evidence if item.evidence_id == row)
    nodes[node_id]["node_id"] = "f" * 64
    with pytest.raises(PolicyDraftInvalid, match="^POLICY_DRAFT_INVALID$"):
        normalize_policy_draft(
            _batch(envelope, source, assignments={chunk: (source.candidate_id,)}),
            envelope,
            local_nodes=nodes,
        )


def test_table_proof_keeps_amount_without_adding_header_citations():
    original, evidence, nodes, header = _input()
    envelope = _pack(evidence)
    source = StructurerCandidate(
        schema_version="1",
        candidate_id=original.candidate_id,
        candidate_kind="rider",
        fields=original.fields,
    )
    row = _fields(source)["rider_name"].evidence_ids[0]
    chunk = envelope.primary_chunk_ids[envelope.primary_evidence_ids.index(row)]
    result = normalize_policy_draft(
        _batch(envelope, source, assignments={chunk: (source.candidate_id,)}),
        envelope,
        local_nodes=nodes,
    )
    assert result.batch.candidates == (source,)
    assert result.adjustments == () and not result.partial
    assert header not in _fields(result.batch.candidates[0])["sum_assured"].evidence_ids
    grounded = ground_range_candidate(
        PolicyCandidate(
            candidate_id=source.candidate_id,
            candidate_kind=source.candidate_kind,
            status="AI_VERIFIED",
            fields=source.fields,
            issue_codes=(),
            provider_request_ids=(),
        ),
        evidence,
        local_nodes=nodes,
    )
    assert grounded.status == "AI_VERIFIED"


def test_other_table_row_amount_is_omitted_instead_of_reassigned():
    original, evidence, nodes, _ = _input(second_amount="40")
    envelope = _pack(evidence)
    rows = [
        item for item in evidence if item.primary and nodes[item.node_id].get("row_role") == "data"
    ]
    source = StructurerCandidate(
        schema_version="1",
        candidate_id=original.candidate_id,
        candidate_kind="rider",
        fields=tuple(
            f.model_copy(update={"value": 400000, "evidence_ids": (rows[1].evidence_id,)})
            if f.field_id == "sum_assured"
            else f
            for f in original.fields
        ),
    )
    chunk = envelope.primary_chunk_ids[envelope.primary_evidence_ids.index(rows[0].evidence_id)]
    result = normalize_policy_draft(
        _batch(envelope, source, assignments={chunk: (source.candidate_id,)}),
        envelope,
        local_nodes=nodes,
    )
    assert "sum_assured" not in _fields(result.batch.candidates[0])
    assert result.partial and "OPTIONAL_FIELD_UNSUPPORTED" in _reasons(result)


def test_explicit_unenrolled_name_is_not_rescued_by_optional_field_removal():
    envelope = _envelope("Sample Rider | 미가입 | 정액 | 가입금액 20원")
    source = _candidate(
        envelope.evidence[0], kind="rider", rider_name="Sample Rider", benefit_type="fixed"
    )
    result = normalize_policy_draft(_batch(envelope, source), envelope)
    assert result.batch.candidates == ()
    assert result.batch.ranges[0].outcome == "UNRESOLVED" and result.partial
