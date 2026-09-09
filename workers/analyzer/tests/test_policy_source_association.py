"""Wholly synthetic insured/contract identity must be proven before ledger projection."""

from dataclasses import replace
from uuid import uuid4

import pytest
from familycare_worker.document_structure import StructureCell
from familycare_worker.policy_source_association import LocalMember, associate_policy_sources

from workers.analyzer.tests.test_document_structure import _block, _build, _extraction, _page


def _member(name: str = "Family Member A") -> LocalMember:
    return LocalMember(uuid4(), name, "synthetic-member-a", 1)


def _source(*pages: str):
    return _build(
        _extraction(*(_page(position, [_block(text)]) for position, text in enumerate(pages, 1)))
    )


def _insured_cell(value: str, *, label: str = "피보험자"):
    source = _source("보험증권 가입금액\n증권번호: synthetic-policy-001")
    original = source.nodes[0]
    row = replace(
        original,
        node_id="d" * 64,
        kind="TABLE_ROW",
        text=f"{label} | {value}",
        bbox=(20, 40, 420, 60),
        row_role="data",
        cells=(
            StructureCell(0, 0, label, (20, 40, 120, 60), "synthetic.cells[0]"),
            StructureCell(0, 1, value, (120, 40, 420, 60), "synthetic.cells[1]"),
        ),
    )
    return replace(
        source,
        nodes=(*source.nodes, row),
        pages=(replace(source.pages[0], node_ids=(original.node_id, row.node_id)),),
    ), row


@pytest.mark.parametrize(
    "qualifier", ["010203-1******", "010203-*******", "2001-02-03", "2000.02.29", "2001/02/03"]
)
def test_recognized_insured_qualifier_keeps_whole_original_cell_anchor(qualifier: str) -> None:
    member = _member()
    value = f"Family Member A ({qualifier})"
    source, row = _insured_cell(value)
    item = associate_policy_sources(source, members=(member,), expected_member_id=member.id)[
        row.node_id
    ]
    assert item.state == "RESOLVED" and item.family_member_id == member.id
    insured = [anchor for anchor in item.anchor_refs if anchor.kind == "insured"]
    assert len(insured) == 1
    assert (insured[0].node_id, insured[0].page, insured[0].start, insured[0].end) == (
        row.node_id,
        1,
        0,
        len(row.text),
    )
    assert row.text == f"피보험자 | {value}"
    assert value not in repr(item.to_dict()) and qualifier not in repr(item.to_dict())


def test_recognized_insured_qualifier_keeps_whole_original_line_anchor() -> None:
    member = _member()
    insured_line = "피보험자: Family Member A (010203-1******)"
    source = _source(f"보험증권 가입금액\n증권번호: synthetic-policy-001\n{insured_line}")
    item = next(
        iter(
            associate_policy_sources(
                source, members=(member,), expected_member_id=member.id
            ).values()
        )
    )
    assert item.state == "RESOLVED"
    anchor = next(anchor for anchor in item.anchor_refs if anchor.kind == "insured")
    assert source.nodes[0].text[anchor.start : anchor.end] == insured_line


@pytest.mark.parametrize(
    "value",
    [
        "Family Member A Plus (010203-1******)",
        "Another Person (010203-1******)",
        "Family Member A and Family Member B (010203-1******)",
        "Family Member A (010203-1******) Family Member B",
        "Family Member A (010203-1******) other text",
        "Family Member A (other person)",
        "Family Member A (010203-1******, other text)",
        "Family Member A (010203-1******) (2001-02-03)",
        "Family Member A ((010203-1******))",
        "Family Member A (010203-1*****)",
        "Family Member A (010203-9******)",
        "Family Member A (010230-1******)",
        "Family Member A (000229-1******)",
        "Family Member A (2001-02-29)",
        "Family Member A (2001-13-03)",
        "Family Member A (2001-02/03)",
        "Family Member A (age 20)",
    ],
)
def test_unrecognized_or_compound_insured_values_do_not_match_a_member(value: str) -> None:
    member = _member()
    source, row = _insured_cell(value)
    item = associate_policy_sources(source, members=(member,), expected_member_id=member.id)[
        row.node_id
    ]
    assert item.state == "UNRESOLVED" and item.family_member_id is None


def test_recognized_qualifier_does_not_hide_wrong_or_ambiguous_member() -> None:
    selected, other = _member(), _member("Family Member B")
    source, row = _insured_cell("Family Member B (010203-1******)")
    result = associate_policy_sources(
        source, members=(selected, other), expected_member_id=selected.id
    )
    assert result[row.node_id].state == "WRONG_MEMBER"
    source, row = _insured_cell("Family Member A (010203-1******)")
    result = associate_policy_sources(
        source, members=(selected, _member()), expected_member_id=selected.id
    )
    assert result[row.node_id].state == "AMBIGUOUS"


def test_qualified_conflicting_insured_anchors_remain_ambiguous() -> None:
    selected, other = _member(), _member("Family Member B")
    source = _source(
        "보험증권 가입금액\n증권번호: synthetic-policy-001\n"
        "피보험자: Family Member A (010203-1******)",
        "보험증권 가입금액\n증권번호: synthetic-policy-001\n피보험자: Family Member B (2001-02-03)",
    )
    result = associate_policy_sources(
        source, members=(selected, other), expected_member_id=selected.id
    )
    assert all(item.state == "AMBIGUOUS" for item in result.values())


@pytest.mark.parametrize("label", ["계약자", "수익자", "메모"])
def test_qualifier_cannot_turn_other_person_labels_into_insured_proof(label: str) -> None:
    member = _member()
    source, row = _insured_cell("Family Member A (010203-1******)", label=label)
    result = associate_policy_sources(source, members=(member,), expected_member_id=member.id)
    assert result[row.node_id].state == "UNRESOLVED"


@pytest.mark.parametrize("fault", ["column_gap", "different_row", "merged_label", "merged_value"])
def test_qualifier_cannot_bypass_unsafe_cell_relationships(fault: str) -> None:
    member = _member()
    source, row = _insured_cell("Family Member A (010203-1******)")
    label, value = row.cells
    if fault == "column_gap":
        value = replace(value, column_index=2)
    elif fault == "different_row":
        value = replace(value, row_index=1)
    elif fault == "merged_label":
        label = replace(label, row_span=2)
    else:
        value = replace(value, column_span=2)
    changed = replace(row, cells=(label, value))
    source = replace(source, nodes=(*source.nodes[:-1], changed))
    result = associate_policy_sources(source, members=(member,), expected_member_id=member.id)
    assert result[row.node_id].state == "UNRESOLVED"


def test_qualified_insured_line_with_ambiguous_columns_is_not_proof() -> None:
    member = _member()
    source, row = _insured_cell("Family Member A (010203-1******)")
    changed = replace(
        row,
        kind="TEXT_LINE",
        cells=(),
        text="피보험자: Family Member A (010203-1******)",
        issue_codes=("LINE_COLUMN_CONTEXT_UNRESOLVED",),
    )
    source = replace(source, nodes=(*source.nodes[:-1], changed))
    result = associate_policy_sources(source, members=(member,), expected_member_id=member.id)
    assert result[row.node_id].state == "UNRESOLVED"


def test_repeated_contract_anchor_links_later_pages_without_transmitting_names() -> None:
    member = _member()
    source = _source(
        "보험증권 가입금액\n증권번호: synthetic-policy-001\n피보험자: Family Member A",
        "보험증권 가입금액\n증권번호: synthetic-policy-001\nSample Rider",
    )
    result = associate_policy_sources(source, members=(member,), expected_member_id=member.id)
    first, second = (result[node.node_id] for node in source.nodes)
    assert first.state == second.state == "RESOLVED"
    assert first.contract_scope_id == second.contract_scope_id
    assert first.family_member_id == second.family_member_id == member.id
    assert first.member_version == member.version
    assert "Family Member A" not in repr(first.to_dict())
    assert "synthetic-policy-001" not in repr(first.to_dict())
    assert second.anchor_refs


def test_batch_selection_policyholder_or_incidental_name_is_not_insured_proof() -> None:
    member = _member()
    for label in ("계약자", "수익자", "메모"):
        source = _source(
            f"보험증권 가입금액\n증권번호: synthetic-policy-001\n{label}: Family Member A"
        )
        item = next(
            iter(
                associate_policy_sources(
                    source, members=(member,), expected_member_id=member.id
                ).values()
            )
        )
        assert item.state == "UNRESOLVED" and item.family_member_id is None


def test_another_insured_cannot_be_assigned_to_selected_member() -> None:
    first, second = _member(), _member("Family Member B")
    source = _source("보험증권 가입금액\n증권번호: synthetic-policy-001\n피보험자: Family Member B")
    item = next(
        iter(
            associate_policy_sources(
                source, members=(first, second), expected_member_id=first.id
            ).values()
        )
    )
    assert item.state == "WRONG_MEMBER" and item.family_member_id is None


def test_duplicate_member_names_are_ambiguous() -> None:
    first, second = _member(), _member()
    source = _source("보험증권 가입금액\n증권번호: synthetic-policy-001\n피보험자: Family Member A")
    item = next(
        iter(
            associate_policy_sources(
                source, members=(first, second), expected_member_id=first.id
            ).values()
        )
    )
    assert item.state == "AMBIGUOUS"


def test_same_contract_with_conflicting_insured_anchors_never_chooses_first() -> None:
    first, second = _member(), _member("Family Member B")
    source = _source(
        "보험증권 가입금액\n증권번호: synthetic-policy-001\n피보험자: Family Member A",
        "보험증권 가입금액\n증권번호: synthetic-policy-001\n피보험자: Family Member B",
    )
    values = associate_policy_sources(
        source, members=(first, second), expected_member_id=first.id
    ).values()
    assert all(item.state == "AMBIGUOUS" for item in values)


def test_separate_contracts_keep_separate_scopes_and_unmarked_page_is_not_inherited() -> None:
    member = _member()
    source = _source(
        "보험증권 가입금액\n증권번호: synthetic-policy-001\n피보험자: Family Member A",
        "보험증권 가입금액\n증권번호: synthetic-policy-002\n피보험자: Family Member A",
        "보험증권 가입금액 Sample Rider",
    )
    values = list(
        associate_policy_sources(source, members=(member,), expected_member_id=member.id).values()
    )
    assert values[0].contract_scope_id != values[1].contract_scope_id
    assert values[2].state == "UNRESOLVED"


def test_prefix_or_unknown_insured_value_is_not_an_exact_member_match() -> None:
    member = _member()
    for value in ("Family Member A Plus", "Unknown Member", "[REDACTED]"):
        source = _source(f"보험증권 가입금액\n증권번호: synthetic-policy-001\n피보험자: {value}")
        item = next(
            iter(
                associate_policy_sources(
                    source, members=(member,), expected_member_id=member.id
                ).values()
            )
        )
        assert item.state == "UNRESOLVED"


def test_role_label_value_cells_use_local_row_relationship() -> None:
    from familycare_worker.document_structure import StructureCell

    member = _member()
    source = _source("보험증권 가입금액\n증권번호: synthetic-policy-001")
    node = source.nodes[0]
    row = replace(
        node,
        node_id="c" * 64,
        kind="TABLE_ROW",
        text="피보험자 | Family Member A",
        cells=tuple(
            StructureCell(0, i, text, None, f"synthetic.cells[{i}]")
            for i, text in enumerate(("피보험자", "Family Member A"))
        ),
    )
    source = replace(
        source,
        nodes=(*source.nodes, row),
        pages=(replace(source.pages[0], node_ids=(node.node_id, row.node_id)),),
    )
    result = associate_policy_sources(source, members=(member,), expected_member_id=member.id)
    assert result[row.node_id].state == "RESOLVED"


def test_terms_example_cannot_supply_insured_authority_to_a_policy_page() -> None:
    member = _member()
    source = _source(
        "보험증권 가입금액\n증권번호: synthetic-policy-001",
        "보험약관 제1조\n증권번호: synthetic-policy-001\n피보험자: Family Member A",
    )
    result = associate_policy_sources(source, members=(member,), expected_member_id=member.id)
    assert result[source.nodes[0].node_id].state == "UNRESOLVED"


def test_sparse_cells_cannot_skip_a_missing_insured_value() -> None:
    from familycare_worker.document_structure import StructureCell

    member = _member()
    source = _source("보험증권 가입금액\n증권번호: synthetic-policy-001")
    node = source.nodes[0]
    row = replace(
        node,
        node_id="c" * 64,
        kind="TABLE_ROW",
        text="피보험자 | Family Member A",
        cells=(
            StructureCell(0, 0, "피보험자", None, "synthetic.cells[0]"),
            StructureCell(0, 2, "Family Member A", None, "synthetic.cells[2]"),
        ),
    )
    source = replace(
        source,
        nodes=(*source.nodes, row),
        pages=(replace(source.pages[0], node_ids=(node.node_id, row.node_id)),),
    )
    result = associate_policy_sources(source, members=(member,), expected_member_id=member.id)
    assert result[row.node_id].state == "UNRESOLVED"
