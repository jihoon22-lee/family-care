"""Field scoping preserves source closure and cannot create approval or raw input."""

from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest
from familycare_worker.ai.schemas import StructurerCandidate
from familycare_worker.ai.scoped_policy_verifier import (
    FIELD_SCOPE_REVISION,
    scoped_policy_evidence,
)

from workers.analyzer.tests.test_policy_draft_normalization import _batch, _pack
from workers.analyzer.tests.test_policy_table_grounding import _input


def _fixture():
    candidate, evidence, nodes, header_id = _input()
    draft = StructurerCandidate(
        candidate_id=candidate.candidate_id,
        candidate_kind=candidate.candidate_kind,
        fields=candidate.fields,
    )
    envelope = _pack(evidence)
    return _batch(envelope, draft), envelope, nodes, header_id


def _context(envelope, nodes, parent, text, *, provider_text=None):
    node_id = uuid4().hex * 2
    evidence = replace(
        envelope.evidence[0],
        evidence_id=uuid4(),
        node_id=node_id,
        text=text if provider_text is None else provider_text,
        start=0,
        end=len(text),
        primary=False,
    )
    nodes[node_id] = {
        "node_id": node_id,
        "kind": "BLOCK",
        "source_layer": "native",
        "page_number": evidence.page,
        "text": text,
        "context_node_ids": [],
        "issue_codes": [],
    }
    nodes[parent]["context_node_ids"] = [*nodes[parent].get("context_node_ids", ()), node_id]
    return _pack((*envelope.evidence, evidence)), evidence


def test_field_scope_discovers_header_and_keeps_the_original_candidate_unapproved():
    batch, envelope, nodes, header_id = _fixture()
    before = deepcopy((batch, envelope, nodes))
    selected = scoped_policy_evidence(batch, envelope, nodes)
    name_ids = {key for field in batch.candidates[0].fields for key in field.evidence_ids}
    assert FIELD_SCOPE_REVISION == "cited-fields-v1"
    assert {e.evidence_id for e in selected} == name_ids | {header_id}
    assert len(selected) == 2 < len(envelope.evidence)
    assert all(any(e is original for original in envelope.evidence) for e in selected)
    assert (batch, envelope, nodes) == before
    assert "status" not in batch.candidates[0].model_dump()


def test_recursive_footnote_exception_context_and_minimized_text_are_preserved():
    batch, envelope, nodes, header_id = _fixture()
    header_node = next(e.node_id for e in envelope.evidence if e.evidence_id == header_id)
    envelope, footnote = _context(envelope, nodes, header_node, "Coverage exceptions apply.")
    envelope, exception = _context(
        envelope, nodes, footnote.node_id, "Synthetic private context", provider_text="[REDACTED]"
    )
    selected = scoped_policy_evidence(batch, envelope, nodes)
    assert footnote in selected and exception in selected
    assert next(e for e in selected if e.evidence_id == exception.evidence_id).text == "[REDACTED]"
    assert "Synthetic private context" not in " ".join(e.text for e in selected)
    assert len(selected) == len(envelope.evidence) - 1


def test_complete_context_cycle_terminates_without_hiding_any_context():
    batch, envelope, nodes, header_id = _fixture()
    header_node = next(e.node_id for e in envelope.evidence if e.evidence_id == header_id)
    row = next(n for n in nodes.values() if n.get("row_role") == "data")
    envelope, footnote = _context(envelope, nodes, header_node, "See original row conditions.")
    nodes[footnote.node_id]["context_node_ids"] = [row["node_id"]]
    selected = scoped_policy_evidence(batch, envelope, nodes)
    assert footnote in selected and header_id in {e.evidence_id for e in selected}
    assert len(selected) == len(envelope.evidence) - 1


@pytest.mark.parametrize(
    "fault",
    [
        "no_nodes",
        "missing_node",
        "missing_evidence",
        "truncated_context",
        "uncertain_context",
        "unknown_role",
        "ocr_context",
        "malformed_context_list",
        "wrong_page",
        "conflicting_amount",
        "unknown_field_evidence",
    ],
)
def test_incomplete_or_ambiguous_scope_returns_the_full_original_envelope(fault):
    batch, envelope, nodes, header_id = _fixture()
    header_node = next(e.node_id for e in envelope.evidence if e.evidence_id == header_id)
    envelope, footnote = _context(envelope, nodes, header_node, "Exceptions remain relevant.")
    if fault == "no_nodes":
        nodes = None
    elif fault == "missing_node":
        del nodes[footnote.node_id]
    elif fault == "missing_evidence":
        envelope = _pack(
            tuple(e for e in envelope.evidence if e.evidence_id != footnote.evidence_id)
        )
    elif fault == "truncated_context":
        envelope = _pack(
            tuple(replace(e, end=e.end - 1) if e == footnote else e for e in envelope.evidence)
        )
    elif fault == "uncertain_context":
        nodes[footnote.node_id]["issue_codes"] = ["CONTEXT_REFERENCE_UNRESOLVED"]
    elif fault == "unknown_role":
        envelope = _pack(
            tuple(
                replace(e, document_kind="terms", source_role="unknown") if e == footnote else e
                for e in envelope.evidence
            )
        )
    elif fault == "ocr_context":
        nodes[footnote.node_id]["source_layer"] = "ocr"
    elif fault == "malformed_context_list":
        nodes[footnote.node_id]["context_node_ids"] = "missing-node"
    elif fault == "wrong_page":
        nodes[footnote.node_id]["page_number"] += 1
    elif fault in {"conflicting_amount", "unknown_field_evidence"}:
        original = batch.candidates[0]
        fields = tuple(
            f.model_copy(update={"value": 999})
            if fault == "conflicting_amount" and f.field_id == "sum_assured"
            else f.model_copy(update={"evidence_ids": (uuid4(),)})
            if fault == "unknown_field_evidence" and f.field_id == "currency"
            else f
            for f in original.fields
        )
        batch = batch.model_copy(
            update={"candidates": (original.model_copy(update={"fields": fields}),)}
        )
    assert scoped_policy_evidence(batch, envelope, nodes) is envelope.evidence


def test_all_fields_and_multiple_candidates_contribute_to_scope():
    candidate, evidence, nodes, header_id = _input(second_amount="30")
    rows = [e for e in evidence if nodes[e.node_id].get("row_role") == "data"]
    first = StructurerCandidate(
        candidate_id=candidate.candidate_id, candidate_kind="rider", fields=candidate.fields
    )
    second = StructurerCandidate(
        candidate_id=uuid4(),
        candidate_kind="rider",
        fields=tuple(
            field.model_copy(
                update={
                    "evidence_ids": (rows[1].evidence_id,),
                    **({"value": 300000} if field.field_id == "sum_assured" else {}),
                }
            )
            for field in candidate.fields
        ),
    )
    envelope = _pack(evidence)
    batch = _batch(envelope, first, second)
    selected = scoped_policy_evidence(batch, envelope, nodes)
    expected = {key for c in batch.candidates for f in c.fields for key in f.evidence_ids}
    assert {e.evidence_id for e in selected} == expected | {header_id}
    assert len(selected) == 3 < len(envelope.evidence)
