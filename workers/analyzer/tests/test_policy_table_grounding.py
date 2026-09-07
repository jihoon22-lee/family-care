"""Column-addressed evidence distinguishes insured amount from adjacent premiums."""

from typing import Any
from uuid import uuid4

import pytest
from familycare_worker.ai.policy_ranges import build_policy_envelopes
from familycare_worker.ai.range_grounding import ground_range_candidate
from familycare_worker.ai.schemas import CandidateField, PolicyCandidate
from familycare_worker.document_structure import plan_structure_chunks

from workers.analyzer.tests.test_document_structure import (
    _block,
    _build,
    _extraction,
    _page,
    _table,
)


def _input(
    *,
    amount_header: str = "가입금액(만원)",
    amount_cell: str = "20",
    name_cell: str = "Sample Rider",
    second_amount: str | None = None,
) -> Any:
    source = _build(
        _extraction(
            _page(
                1,
                [_block("보험증권 가입금액")],
                tables=[
                    _table(
                        [
                            ["담보명", amount_header, "보험료", "보장구분", "비고"],
                            [name_cell, amount_cell, "300", "정액", "Sample Rider"],
                            *(
                                [[name_cell, second_amount, "400", "정액", "Sample Rider"]]
                                if second_amount is not None
                                else []
                            ),
                        ],
                        header_rows=[0],
                    )
                ],
            )
        )
    )
    plan = plan_structure_chunks(
        source, max_content_chars=4096, max_context_chars=4096, max_chunks=50
    )
    envelope = build_policy_envelopes(source, plan, sensitive_terms=()).envelopes[0]
    row = next(n for n in source.nodes if n.kind == "TABLE_ROW" and n.row_role == "data")
    header = next(n for n in source.nodes if n.kind == "TABLE_ROW" and n.row_role == "header")
    evidence = next(e for e in envelope.evidence if e.node_id == row.node_id)
    header_evidence = next(e for e in envelope.evidence if e.node_id == header.node_id)
    candidate = PolicyCandidate(
        candidate_id=uuid4(),
        candidate_kind="rider",
        status="AI_VERIFIED",
        fields=tuple(
            CandidateField(field_id=key, value=value, evidence_ids=(evidence.evidence_id,))
            for key, value in {
                "rider_name": "Sample Rider",
                "rider_key": "sample-rider",
                "sum_assured": 200000,
                "currency": "KRW",
                "benefit_type": "fixed",
            }.items()
        ),
        issue_codes=(),
        provider_request_ids=("synthetic-structure", "synthetic-verify"),
    )
    nodes = {node["node_id"]: node for node in source.to_dict()["nodes"]}
    return candidate, envelope.evidence, nodes, header_evidence.evidence_id


def test_header_and_unit_ground_the_matching_cell_and_are_retained_as_evidence() -> None:
    candidate, evidence, nodes, header_id = _input()
    result = ground_range_candidate(candidate, evidence, local_nodes=nodes)
    assert result.status == "AI_VERIFIED"
    amount = next(field for field in result.fields if field.field_id == "sum_assured")
    assert amount.value == 200000 and header_id in amount.evidence_ids


def test_same_name_on_another_row_cannot_supply_this_riders_amount() -> None:
    candidate, evidence, nodes, _ = _input(second_amount="40")
    rows = [e for e in evidence if e.primary and nodes[e.node_id].get("row_role") == "data"]
    assert len(rows) == 2
    candidate = candidate.model_copy(
        update={
            "fields": tuple(
                field.model_copy(update={"value": 400000, "evidence_ids": (rows[1].evidence_id,)})
                if field.field_id == "sum_assured"
                else field
                for field in candidate.fields
            )
        }
    )
    assert ground_range_candidate(candidate, evidence, local_nodes=nodes).status == "NEEDS_REVIEW"


def test_header_row_is_not_an_enrollment_even_when_it_contains_type_words() -> None:
    candidate, evidence, nodes, header_id = _input()
    candidate = candidate.model_copy(
        update={
            "fields": tuple(
                CandidateField(field_id=key, value=value, evidence_ids=(header_id,))
                for key, value in {
                    "rider_name": "담보명",
                    "rider_key": "담보명",
                    "benefit_type": "fixed",
                }.items()
            )
        }
    )
    from dataclasses import replace

    evidence = tuple(
        replace(item, text=item.text + " 정액") if item.evidence_id == header_id else item
        for item in evidence
    )
    assert ground_range_candidate(candidate, evidence, local_nodes=nodes).status == "NEEDS_REVIEW"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"amount_header": "가입금액"},
        {"amount_cell": "20원"},
        {"name_cell": "Another Rider"},
        {"amount_header": "월보험료(만원)"},
    ],
)
def test_missing_unit_conflict_or_wrong_column_never_borrows_a_value(
    kwargs: dict[str, str],
) -> None:
    candidate, evidence, nodes, _ = _input(**kwargs)
    assert ground_range_candidate(candidate, evidence, local_nodes=nodes).status == "NEEDS_REVIEW"


@pytest.mark.parametrize(
    "variant",
    [
        "missing_name",
        "ambiguous_name",
        "conflicting_header",
        "missing_context",
        "spanning_header",
        "multiline_unenrolled",
        "example_footnote",
    ],
)
def test_unsupported_or_contradictory_table_context_cannot_use_text_fallback(variant: str) -> None:
    candidate, evidence, nodes, _ = _input()
    row = next(n for n in nodes.values() if n["kind"] == "TABLE_ROW" and n["row_role"] == "data")
    header = next(
        n for n in nodes.values() if n["kind"] == "TABLE_ROW" and n["row_role"] == "header"
    )
    if variant in {"missing_name", "ambiguous_name"}:
        header["cells"][0]["text"] = "항목" if variant == "missing_name" else "담보명"
        header["cells"][4]["text"] = "담보명" if variant == "ambiguous_name" else "비고"
        row["cells"][0]["text"] = "Another Rider"
        row["text"] = "Another Rider\t20\t300\t정액\tSample Rider fixed sum assured: 200000 KRW"
        evidence = tuple(
            e.__class__(**{**e.__dict__, "text": row["text"]}) if e.node_id == row["node_id"] else e
            for e in evidence
        )
    elif variant in {"conflicting_header", "missing_context"}:
        from copy import deepcopy

        alternate = deepcopy(header)
        alternate["node_id"] = "f" * 64
        alternate["cells"][1]["text"] = "월보험료"
        nodes[alternate["node_id"]] = alternate
        row["context_node_ids"].append(alternate["node_id"])
        original = next(e for e in evidence if e.node_id == header["node_id"])
        from dataclasses import replace

        if variant == "conflicting_header":
            evidence = (
                *evidence,
                replace(original, node_id=alternate["node_id"], evidence_id=uuid4()),
            )
    elif variant == "spanning_header":
        header["cells"][1]["column_span"] = 2
    elif variant == "multiline_unenrolled":
        row["cells"][4]["text"] = "Sample Rider\n미가입"
        row["text"] += "\n미가입"
    else:
        note = next(n for n in nodes.values() if n["kind"] == "BLOCK")
        note["text"] = "이 표는 가입 예시입니다"
        row["context_node_ids"].append(note["node_id"])
    assert ground_range_candidate(candidate, evidence, local_nodes=nodes).status == "NEEDS_REVIEW"
