"""Only proven table relationships carry policy role and local insured scope."""

from uuid import uuid4

import pytest
from familycare_worker.ai.policy_ranges import build_policy_envelopes
from familycare_worker.document_structure import plan_structure_chunks
from familycare_worker.policy_source_association import LocalMember, associate_policy_sources

from workers.analyzer.tests.test_document_structure import (
    _block,
    _build,
    _extraction,
    _page,
    _table,
)


def _continued(*, linked: bool = True, second_text: str = "Synthetic unrelated note"):
    parent = _table([["담보명", "가입금액"], ["Sample Rider", "317원"]], header_rows=[0])
    child = _table(
        [["Another Rider", "619원"]],
        **({"continuation_of": {"page_number": 1, "table_index": 0}} if linked else {}),
    )
    return _build(
        _extraction(
            _page(
                1,
                [
                    _block(
                        "보험증권 가입금액\n증권번호: synthetic-policy-001\n"
                        "피보험자: Family Member A"
                    )
                ],
                tables=[parent],
            ),
            _page(2, [_block(second_text)], tables=[child]),
        )
    )


def test_only_continued_table_nodes_inherit_policy_role() -> None:
    source = _continued()
    plan = plan_structure_chunks(
        source, max_content_chars=4096, max_context_chars=4096, max_chunks=50
    )
    result = build_policy_envelopes(source, plan, sensitive_terms=("Family Member A",))
    roles = {e.node_id: e.source_role for envelope in result.envelopes for e in envelope.evidence}
    assert source.pages[1].role == "unknown"
    row = next(n for n in source.nodes if n.page_number == 2 and n.kind == "TABLE_ROW")
    note = next(n for n in source.nodes if n.page_number == 2 and n.kind == "BLOCK")
    assert roles[row.node_id] == "policy"
    assert roles[note.node_id] == "unknown"
    member = LocalMember(uuid4(), "Family Member A", "synthetic-member-a", 1)
    links = associate_policy_sources(source, members=(member,), expected_member_id=member.id)
    assert links[row.node_id].state == "RESOLVED"
    assert links[note.node_id].state == "UNRESOLVED"
    assert links[row.node_id].contract_scope_id == links[source.nodes[0].node_id].contract_scope_id


@pytest.mark.parametrize(
    "linked,second_text",
    [
        (False, "Synthetic unrelated note"),
        (True, "보험약관 제1조 보장내용\nSynthetic terms example"),
        (True, "피보험자: Family Member B"),
        (True, "증권번호: synthetic-policy-002"),
    ],
)
def test_missing_relationship_or_conflicting_scope_cannot_inherit(
    linked: bool, second_text: str
) -> None:
    source = _continued(linked=linked, second_text=second_text)
    first = LocalMember(uuid4(), "Family Member A", "synthetic-member-a", 1)
    other = LocalMember(uuid4(), "Family Member B", "synthetic-member-b", 1)
    row = next(n for n in source.nodes if n.page_number == 2 and n.kind == "TABLE_ROW")
    links = associate_policy_sources(source, members=(first, other), expected_member_id=first.id)
    assert links[row.node_id].state != "RESOLVED"
