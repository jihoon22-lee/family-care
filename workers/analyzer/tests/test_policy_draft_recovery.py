"""Synthetic source recovery keeps strict identities and needs a new verifier."""

import json
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import familycare_worker.ai.policy_draft_recovery as recovery
import pytest
from familycare_worker.ai.policy_draft_normalization import (
    PolicyDraftInvalid,
    normalize_policy_draft,
)
from familycare_worker.ai.range_structurer import PolicyRangeBatch
from familycare_worker.ai.schemas import CandidateField, StructurerCandidate, VerifierDecision
from familycare_worker.ai.scoped_policy_verifier import scoped_policy_evidence
from familycare_worker.ai.validator import validate_candidate
from pydantic import ValidationError

from workers.analyzer.tests.test_explicit_unit_draft_normalization import _source
from workers.analyzer.tests.test_policy_draft_normalization import (
    _batch,
    _candidate,
    _envelope,
    _fields,
    _pack,
    _reasons,
)
from workers.analyzer.tests.test_policy_table_grounding import _input
from workers.analyzer.tests.test_scoped_policy_verifier import _context


def _text_input(count=3):
    envelope = _envelope(*(f"담보명: Sample Rider {i} 가입금액 20원" for i in range(count)))
    candidates = tuple(
        _candidate(e, kind="rider", rider_name=f"Sample Rider {i}", sum_assured=20)
        for i, e in enumerate(envelope.evidence)
    )
    nodes = {
        e.node_id: dict(
            node_id=e.node_id,
            text=e.text,
            page_number=e.page,
            source_layer="native",
            kind="BLOCK",
            context_node_ids=[],
            issue_codes=[],
        )
        for e in envelope.evidence
    }
    raw = _batch(
        envelope,
        *candidates,
        assignments={
            chunk: (candidate.candidate_id,)
            for chunk, candidate in zip(envelope.primary_chunk_ids, candidates, strict=True)
        },
    ).model_dump(mode="json")
    return raw, envelope, nodes


def _orphan(raw, position):
    identity = raw["candidates"][position]["candidate_id"]
    for item in raw["ranges"]:
        item["candidate_ids"] = [key for key in item["candidate_ids"] if key != identity]
        if not item["candidate_ids"]:
            item["outcome"] = "NO_ENROLLMENT_FACTS"


def _recover(raw, envelope, nodes):
    return recovery.normalize_policy_response(raw, envelope, local_nodes=nodes)


def _table_raw(draft=None, envelope=None, nodes=None):
    if draft is None:
        candidate, evidence, nodes, _ = _input()
        draft = StructurerCandidate(
            schema_version="1",
            candidate_id=candidate.candidate_id,
            candidate_kind="rider",
            fields=candidate.fields,
        )
        envelope = _pack(evidence)
    name_ids = _fields(draft)["rider_name"].evidence_ids
    raw = _batch(
        envelope,
        draft,
        assignments={
            chunk: (draft.candidate_id,)
            for chunk, identity in zip(
                envelope.primary_chunk_ids, envelope.primary_evidence_ids, strict=True
            )
            if identity in name_ids
        },
    ).model_dump(mode="json")
    return raw, envelope, nodes


def test_three_orphans_are_recovered_only_to_their_own_required_primary():
    raw, envelope, nodes = _text_input(8)
    for position in (1, 3, 6):
        _orphan(raw, position)
    before = deepcopy(raw)
    with pytest.raises(ValidationError):
        PolicyRangeBatch.model_validate_json(json.dumps(raw), strict=True)
    result = _recover(raw, envelope, nodes)
    assert raw == before
    assert result.revision == recovery.PROVEN_CONTEXT_NORMALIZATION_REVISION and result.partial
    assert len(result.batch.candidates) == 8
    assert [r.candidate_ids for r in result.batch.ranges] == [
        (c.candidate_id,) for c in result.batch.candidates
    ]
    repaired = [a for a in result.adjustments if a.reason == "CANDIDATE_RANGE_RECONCILED"]
    assert {(str(a.candidate_id), a.chunk_id) for a in repaired} == {
        (raw["candidates"][i]["candidate_id"], envelope.primary_chunk_ids[i]) for i in (1, 3, 6)
    }
    assert all("status" not in c.model_dump() for c in result.batch.candidates)


def test_wrong_pair_is_moved_without_discarding_the_other_valid_candidate():
    raw, envelope, nodes = _text_input(2)
    misplaced = raw["ranges"][0]["candidate_ids"][0]
    raw["ranges"][0].update(outcome="NO_ENROLLMENT_FACTS", candidate_ids=[])
    raw["ranges"][1]["candidate_ids"].append(misplaced)
    result = _recover(raw, envelope, nodes)
    assert len(result.batch.candidates) == 2
    assert [r.candidate_ids for r in result.batch.ranges] == [
        (c.candidate_id,) for c in result.batch.candidates
    ]
    assert {"CANDIDATE_RANGE_RECONCILED", "CANDIDATE_RANGE_UNSUPPORTED"} <= _reasons(result)


def test_valid_existing_multi_primary_mapping_is_not_replaced_with_a_guess():
    envelope = _envelope("상품명: Sample Plan", "보험사: Sample Insurer")
    draft = _candidate(envelope.evidence[0], product_name="Sample Plan")
    draft = draft.model_copy(
        update={
            "fields": (
                *draft.fields,
                CandidateField(
                    field_id="insurer",
                    value="Sample Insurer",
                    evidence_ids=(envelope.evidence[1].evidence_id,),
                ),
            )
        }
    )
    raw = _batch(
        envelope, draft, assignments={k: (draft.candidate_id,) for k in envelope.primary_chunk_ids}
    ).model_dump(mode="json")
    nodes = {
        e.node_id: dict(
            node_id=e.node_id,
            kind="BLOCK",
            source_layer="native",
            page_number=e.page,
            text=e.text,
            context_node_ids=[],
            issue_codes=[],
        )
        for e in envelope.evidence
    }
    result = _recover(raw, envelope, nodes)
    assert [r.model_dump(mode="json") for r in result.batch.ranges] == raw["ranges"]
    assert "CANDIDATE_RANGE_RECONCILED" not in _reasons(result)
    assert not result.partial


def test_valid_existing_candidate_order_within_a_range_is_preserved():
    envelope = _envelope(
        "담보명: Sample Rider A 가입금액 20원\n담보명: Sample Rider B 가입금액 30원"
    )
    evidence = envelope.evidence[0]
    first = _candidate(evidence, kind="rider", rider_name="Sample Rider A", sum_assured=20)
    second = _candidate(evidence, kind="rider", rider_name="Sample Rider B", sum_assured=30)
    raw = _batch(
        envelope,
        first,
        second,
        assignments={envelope.primary_chunk_ids[0]: (second.candidate_id, first.candidate_id)},
    ).model_dump(mode="json")
    nodes = {
        evidence.node_id: dict(
            node_id=evidence.node_id,
            kind="BLOCK",
            source_layer="native",
            page_number=evidence.page,
            text=evidence.text,
            context_node_ids=[],
            issue_codes=[],
        )
    }
    result = _recover(raw, envelope, nodes)
    assert result.batch.ranges[0].candidate_ids == (second.candidate_id, first.candidate_id)
    assert "CANDIDATE_RANGE_RECONCILED" not in _reasons(result)


def test_unit_and_name_recovery_precede_orphan_mapping_proof():
    draft, envelope, nodes = _source(wrong_name="Invented Rider")
    raw, envelope, nodes = _table_raw(draft, envelope, nodes)
    _orphan(raw, 0)
    result = _recover(raw, envelope, nodes)
    assert len(result.batch.candidates) == 1
    fields = _fields(result.batch.candidates[0])
    assert fields["rider_name"].value == "Sample Rider"
    assert fields["sum_assured"].value == 200000
    assert {
        "AMOUNT_SCALED_FROM_EXPLICIT_UNIT",
        "RIDER_NAME_RESTORED_FROM_CITED_ROW",
        "CANDIDATE_RANGE_RECONCILED",
        "FIELD_CONTEXT_EVIDENCE_PROVEN",
    } <= _reasons(result)


def test_unproven_orphan_preserves_its_loss_without_failing_other_ranges():
    raw, envelope, nodes = _text_input(2)
    _orphan(raw, 1)
    next(f for f in raw["candidates"][1]["fields"] if f["field_id"] == "rider_name")["value"] = (
        "Absent Rider"
    )
    result = _recover(raw, envelope, nodes)
    assert [str(c.candidate_id) for c in result.batch.candidates] == [
        raw["candidates"][0]["candidate_id"]
    ]
    assert result.batch.ranges[0].outcome == "CANDIDATES"
    assert (
        result.batch.ranges[1].outcome == "UNRESOLVED" and not result.batch.ranges[1].candidate_ids
    )
    assert result.partial and "CANDIDATE_UNREFERENCED" in _reasons(result)


def test_two_independently_supported_primaries_are_not_guessed_for_an_orphan():
    raw, envelope, nodes = _text_input(3)
    candidate = raw["candidates"][0]
    name = next(f for f in candidate["fields"] if f["field_id"] == "rider_name")
    name["evidence_ids"].append(str(envelope.evidence[1].evidence_id))
    changed = envelope.evidence[1].text.replace("Sample Rider 1", "Sample Rider 0")
    nodes[envelope.evidence[1].node_id]["text"] = changed
    envelope = _pack(
        tuple(replace(e, text=changed) if i == 1 else e for i, e in enumerate(envelope.evidence))
    )
    _orphan(raw, 0)
    removed = raw["candidates"].pop(1)["candidate_id"]
    for r in raw["ranges"]:
        if removed in r["candidate_ids"]:
            r.update(outcome="NO_ENROLLMENT_FACTS", candidate_ids=[])
    result = _recover(raw, envelope, nodes)
    assert len(result.batch.candidates) == 1
    assert str(result.batch.candidates[0].candidate_id) == raw["candidates"][1]["candidate_id"]
    assert [r.outcome for r in result.batch.ranges] == ["UNRESOLVED", "UNRESOLVED", "CANDIDATES"]


@pytest.mark.parametrize("orphan", [False, True])
def test_missing_required_primary_is_a_candidate_loss_not_a_batch_failure(orphan):
    raw, envelope, nodes = _text_input(2)
    if orphan:
        _orphan(raw, 1)
    raw["candidates"][1]["fields"] = [
        f for f in raw["candidates"][1]["fields"] if f["field_id"] != "rider_name"
    ]
    result = _recover(raw, envelope, nodes)
    assert len(result.batch.candidates) == 1 and result.partial
    assert result.batch.ranges[1].outcome == "UNRESOLVED"


@pytest.mark.parametrize(
    "fault",
    [
        "foreign_evidence",
        "unknown_candidate",
        "foreign_chunk",
        "duplicate_candidate",
        "duplicate_field",
        "duplicate_field_evidence",
        "duplicate_range",
        "extra_key",
        "bad_version",
        "nonfinite",
    ],
)
def test_wire_schema_and_identity_failures_still_reject_the_entire_response(fault):
    raw, envelope, nodes = _text_input(2)
    if fault == "foreign_evidence":
        raw["candidates"][0]["fields"][0]["evidence_ids"] = [str(uuid4())]
    elif fault == "unknown_candidate":
        raw["ranges"][0]["candidate_ids"] = [str(uuid4())]
    elif fault == "foreign_chunk":
        raw["ranges"][0]["chunk_id"] = "f" * 64
    elif fault == "duplicate_candidate":
        raw["candidates"].append(deepcopy(raw["candidates"][0]))
    elif fault == "duplicate_field":
        raw["candidates"][0]["fields"].append(deepcopy(raw["candidates"][0]["fields"][0]))
    elif fault == "duplicate_field_evidence":
        ids = raw["candidates"][0]["fields"][0]["evidence_ids"]
        ids.append(ids[0])
    elif fault == "duplicate_range":
        raw["ranges"][1] = deepcopy(raw["ranges"][0])
    elif fault == "extra_key":
        raw["unrecognized"] = True
    elif fault == "bad_version":
        raw["schema_version"] = "2"
    else:
        raw["candidates"][0]["fields"][1]["value"] = float("nan")
    with pytest.raises(PolicyDraftInvalid, match="^POLICY_DRAFT_INVALID$"):
        _recover(raw, envelope, nodes)


def test_grounded_header_is_added_per_field_before_the_unchanged_validator():
    raw, envelope, nodes = _table_raw()
    before = deepcopy(raw)
    result = _recover(raw, envelope, nodes)
    candidate = result.batch.candidates[0]
    header = next(e for e in envelope.evidence if nodes[e.node_id].get("row_role") == "header")
    assert raw == before
    assert "FIELD_CONTEXT_EVIDENCE_PROVEN" in _reasons(result) and result.partial
    old = {f["field_id"]: f for f in raw["candidates"][0]["fields"]}
    for f in candidate.fields:
        assert (
            type(f.value) is type(old[f.field_id]["value"]) and f.value == old[f.field_id]["value"]
        )
    assert header.evidence_id in _fields(candidate)["sum_assured"].evidence_ids
    original = PolicyRangeBatch.model_validate_json(json.dumps(raw), strict=True).candidates[0]
    ids = tuple(dict.fromkeys(key for f in candidate.fields for key in f.evidence_ids))
    fresh = VerifierDecision(
        schema_version="1",
        candidate_id=candidate.candidate_id,
        decision="approved",
        evidence_ids=ids,
        issue_codes=(),
    )
    assert "INVENTED_EVIDENCE" in validate_candidate(
        candidate=original, verifier=fresh, evidence=envelope.evidence
    )
    assert validate_candidate(candidate=candidate, verifier=fresh, evidence=envelope.evidence) == ()
    old_failure = fresh.model_copy(
        update={"decision": "needs_review", "issue_codes": ("INVENTED_EVIDENCE",)}
    )
    assert "INVENTED_EVIDENCE" in validate_candidate(
        candidate=candidate, verifier=old_failure, evidence=envelope.evidence
    )
    assert "status" not in candidate.model_dump()


def test_context_closure_keeps_footnotes_without_adding_them_as_field_proof():
    raw, envelope, nodes = _table_raw()
    header = next(e for e in envelope.evidence if nodes[e.node_id].get("row_role") == "header")
    envelope, footnote = _context(
        envelope, nodes, header.node_id, "Sample coverage limitations apply."
    )
    result = _recover(raw, envelope, nodes)
    selected = scoped_policy_evidence(result.batch, envelope, nodes)
    assert footnote in selected
    assert all(
        footnote.evidence_id not in f.evidence_ids
        for c in result.batch.candidates
        for f in c.fields
    )
    assert all(any(item is original for original in envelope.evidence) for item in selected)


@pytest.mark.parametrize("fault", ["foreign", "sibling", "value", "type"])
def test_program_probe_cannot_add_unrelated_context_or_change_a_field(fault, monkeypatch):
    raw, envelope, nodes = _table_raw()
    previous = recovery.ground_range_candidate
    sibling = next(e for e in envelope.evidence if nodes[e.node_id].get("kind") == "BLOCK")

    def altered(*args, **kwargs):
        result = previous(*args, **kwargs)
        return result.model_copy(
            update={
                "fields": tuple(
                    f.model_copy(update={"value": str(f.value) if fault == "type" else 900000})
                    if f.field_id == "sum_assured" and fault in {"type", "value"}
                    else f.model_copy(
                        update={
                            "evidence_ids": (
                                *f.evidence_ids,
                                uuid4() if fault == "foreign" else sibling.evidence_id,
                            )
                        }
                    )
                    if f.field_id == "sum_assured" and fault in {"foreign", "sibling"}
                    else f
                    for f in result.fields
                )
            }
        )

    monkeypatch.setattr(recovery, "ground_range_candidate", altered)
    result = _recover(raw, envelope, nodes)
    assert "FIELD_CONTEXT_EVIDENCE_PROVEN" not in _reasons(result)
    assert result.batch.candidates[0].model_dump(mode="json") == raw["candidates"][0]


def test_supported_partial_primary_keeps_its_original_span_and_v6_mapping():
    raw, envelope, nodes = _text_input(1)
    original = envelope.evidence[0]
    prefix = "Sample preceding paragraph.\n"
    nodes[original.node_id]["text"] = prefix + original.text + "\nSample following paragraph."
    partial = replace(original, start=len(prefix), end=len(prefix) + len(original.text))
    envelope = _pack((partial,))
    legacy = normalize_policy_draft(
        PolicyRangeBatch.model_validate_json(json.dumps(raw), strict=True),
        envelope,
        local_nodes=nodes,
        revision="policy-draft-normalization-v6",
    )
    result = _recover(raw, envelope, nodes)
    assert len(legacy.batch.candidates) == len(result.batch.candidates) == 1
    assert result.batch == legacy.batch
    assert not result.partial
    assert "CANDIDATE_RANGE_UNSUPPORTED" not in _reasons(result)
    assert envelope.evidence == (partial,)
    assert all(f.evidence_ids == (partial.evidence_id,) for f in result.batch.candidates[0].fields)


@pytest.mark.parametrize("fault", ["missing", "truncated"])
def test_missing_context_retains_loss_and_cannot_approve_an_orphan(fault):
    raw, envelope, nodes = _table_raw()
    _orphan(raw, 0)
    envelope = _pack(
        tuple(
            replace(e, end=e.end - 1) if nodes[e.node_id].get("row_role") == "header" else e
            for e in envelope.evidence
            if fault != "missing" or nodes[e.node_id].get("row_role") != "header"
        )
    )
    result = _recover(raw, envelope, nodes)
    assert result.batch.candidates == () and result.partial
    assert "FIELD_CONTEXT_EVIDENCE_PROVEN" not in _reasons(result)


@pytest.mark.parametrize("revision", [f"policy-draft-normalization-v{i}" for i in range(1, 7)])
def test_legacy_revisions_do_not_add_program_discovered_context_to_the_draft(revision):
    raw, envelope, nodes = _table_raw()
    batch = PolicyRangeBatch.model_validate_json(json.dumps(raw), strict=True)
    result = normalize_policy_draft(batch, envelope, local_nodes=nodes, revision=revision)
    assert all(a.reason != "FIELD_CONTEXT_EVIDENCE_PROVEN" for a in result.adjustments)
    assert result.batch.candidates[0].fields == batch.candidates[0].fields


def test_v7_is_not_silently_admitted_by_the_old_normalizer():
    raw, envelope, nodes = _table_raw()
    with pytest.raises(PolicyDraftInvalid):
        normalize_policy_draft(
            PolicyRangeBatch.model_validate_json(json.dumps(raw), strict=True),
            envelope,
            local_nodes=nodes,
            revision=recovery.PROVEN_CONTEXT_NORMALIZATION_REVISION,
        )
