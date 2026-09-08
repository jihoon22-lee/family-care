"""Wholly synthetic insured/contract identity must be proven before ledger projection."""

from dataclasses import replace
from uuid import uuid4

from familycare_worker.policy_source_association import LocalMember, associate_policy_sources

from workers.analyzer.tests.test_document_structure import _block, _build, _extraction, _page


def _member(name: str = "Family Member A") -> LocalMember:
    return LocalMember(uuid4(), name, "synthetic-member-a", 1)


def _source(*pages: str):
    return _build(
        _extraction(*(_page(position, [_block(text)]) for position, text in enumerate(pages, 1)))
    )


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
