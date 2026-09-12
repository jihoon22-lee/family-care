"""Opt-in primary headers remain context through repeated grounding and scoping."""

from dataclasses import replace

import pytest
from familycare_worker.ai.range_grounding import ground_range_candidate
from familycare_worker.ai.schemas import StructurerCandidate
from familycare_worker.ai.scoped_policy_verifier import scoped_policy_evidence

from workers.analyzer.tests.test_policy_draft_normalization import _batch, _pack
from workers.analyzer.tests.test_policy_table_grounding import _input
from workers.analyzer.tests.test_scoped_policy_verifier import _context


def _fixture(**kwargs):
    candidate, evidence, nodes, header_id = _input(**kwargs)
    evidence = tuple(
        replace(item, primary=True) if item.evidence_id == header_id else item for item in evidence
    )
    return candidate, evidence, nodes, header_id


def _ground(candidate, evidence, nodes, *, enabled=False):
    return ground_range_candidate(
        candidate, evidence, local_nodes=nodes, allow_primary_header_context=enabled
    )


def test_primary_header_is_idempotent_only_in_the_opted_in_grounding_revision():
    candidate, evidence, nodes, header_id = _fixture()
    original = candidate.model_dump_json()
    first = _ground(candidate, evidence, nodes)
    assert first.status == "AI_VERIFIED"
    assert header_id in next(f.evidence_ids for f in first.fields if f.field_id == "rider_name")
    second = _ground(first, evidence, nodes, enabled=True)
    third = _ground(second, evidence, nodes, enabled=True)
    assert first == second == third
    assert [(type(f.value), f.value, f.evidence_ids) for f in second.fields] == [
        (type(f.value), f.value, f.evidence_ids) for f in first.fields
    ]
    assert candidate.model_dump_json() == original


def test_default_and_disabled_flag_preserve_the_historical_failure():
    candidate, evidence, nodes, _ = _fixture()
    first = _ground(candidate, evidence, nodes)
    default = ground_range_candidate(first, evidence, local_nodes=nodes)
    disabled = _ground(first, evidence, nodes, enabled=False)
    assert default.model_dump_json() == disabled.model_dump_json()
    assert default.status == "NEEDS_REVIEW" and default.issue_codes == ("INVENTED_FIELD",)
    assert default.fields == first.fields
    # A new source-proof option cannot promote an already failed old decision.
    assert _ground(default, evidence, nodes, enabled=True) == default


@pytest.mark.parametrize("enabled", [False, True])
def test_header_only_name_is_still_not_an_enrollment(enabled):
    candidate, evidence, nodes, header_id = _fixture()
    candidate = candidate.model_copy(
        update={
            "fields": tuple(
                f.model_copy(update={"value": "담보명", "evidence_ids": (header_id,)})
                for f in candidate.fields
                if f.field_id in {"rider_name", "rider_key"}
            )
        }
    )
    assert _ground(candidate, evidence, nodes, enabled=enabled).status == "NEEDS_REVIEW"


def test_header_context_does_not_merge_two_data_rows_with_the_same_name_and_amount():
    candidate, evidence, nodes, _ = _fixture(second_amount="20")
    first = _ground(candidate, evidence, nodes)
    rows = [item for item in evidence if nodes[item.node_id].get("row_role") == "data"]
    assert len(rows) == 2
    mixed = first.model_copy(
        update={
            "fields": tuple(
                f.model_copy(update={"evidence_ids": (*f.evidence_ids, rows[1].evidence_id)})
                if f.field_id == "rider_name"
                else f
                for f in first.fields
            )
        }
    )
    assert _ground(mixed, evidence, nodes, enabled=True).status == "NEEDS_REVIEW"


def _scope_fixture():
    candidate, evidence, nodes, header_id = _fixture()
    grounded = _ground(candidate, evidence, nodes)
    draft = StructurerCandidate(
        schema_version="1",
        candidate_id=grounded.candidate_id,
        candidate_kind=grounded.candidate_kind,
        fields=grounded.fields,
    )
    envelope = _pack(evidence)
    batch = _batch(envelope, draft)
    return batch, envelope, nodes, header_id


def test_scoping_can_replay_context_added_draft_without_false_full_envelope_fallback():
    batch, envelope, nodes, header_id = _scope_fixture()
    selected = scoped_policy_evidence(batch, envelope, nodes, allow_primary_header_context=True)
    assert len(selected) == 2 < len(envelope.evidence)
    assert header_id in {item.evidence_id for item in selected}
    assert {item.evidence_id for item in selected} == {
        identity for field in batch.candidates[0].fields for identity in field.evidence_ids
    }
    assert all(any(item is original for original in envelope.evidence) for item in selected)
    assert scoped_policy_evidence(batch, envelope, nodes) is envelope.evidence
    assert (
        scoped_policy_evidence(batch, envelope, nodes, allow_primary_header_context=False)
        is envelope.evidence
    )


@pytest.mark.parametrize("fault", ["missing_evidence", "truncated_context", "ocr_context"])
def test_primary_header_option_does_not_weaken_actual_context_closure(fault):
    batch, envelope, nodes, header_id = _scope_fixture()
    header_node = next(item.node_id for item in envelope.evidence if item.evidence_id == header_id)
    envelope, footnote = _context(
        envelope, nodes, header_node, "Sample coverage restrictions apply."
    )
    complete = scoped_policy_evidence(batch, envelope, nodes, allow_primary_header_context=True)
    assert footnote in complete and len(complete) < len(envelope.evidence)
    if fault == "missing_evidence":
        envelope = _pack(tuple(item for item in envelope.evidence if item is not footnote))
    elif fault == "truncated_context":
        envelope = _pack(
            tuple(
                replace(item, end=item.end - 1) if item is footnote else item
                for item in envelope.evidence
            )
        )
    else:
        nodes[footnote.node_id]["source_layer"] = "ocr"
    assert (
        scoped_policy_evidence(batch, envelope, nodes, allow_primary_header_context=True)
        is envelope.evidence
    )
