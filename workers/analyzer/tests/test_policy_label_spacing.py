"""Wholly synthetic printed label spacing never rewrites identity values."""

import pytest
from familycare_worker.policy_source_association import associate_policy_sources

from workers.analyzer.tests.test_policy_source_association import _insured_cell, _member, _source


@pytest.mark.parametrize("label", ["피 보 험 자", "피보험자  성명", "피 보 험 자 성 명"])
def test_spaced_insured_table_label_preserves_full_original_anchor(label):
    member = _member()
    source, row = _insured_cell("Family Member A", label=label)
    before = source.to_dict()
    found = associate_policy_sources(source, members=(member,), expected_member_id=member.id)[
        row.node_id
    ]
    assert found.state == "RESOLVED"
    anchor = next(ref for ref in found.anchor_refs if ref.kind == "insured")
    assert row.text[anchor.start : anchor.end] == row.text
    assert source.to_dict() == before


@pytest.mark.parametrize("contract", ["증 권 번 호", "계 약 번 호"])
def test_spaced_line_labels_keep_exact_member_and_contract_values(contract):
    member = _member()
    source = _source(
        f"보험증권 가입금액\n{contract}: synthetic-policy-001\n피 보 험 자: Family Member A"
    )
    before = source.to_dict()
    found = next(
        iter(
            associate_policy_sources(
                source, members=(member,), expected_member_id=member.id
            ).values()
        )
    )
    assert found.state == "RESOLVED"
    assert found.family_member_id == member.id
    assert {ref.kind for ref in found.anchor_refs} == {"contract", "insured"}
    assert source.to_dict() == before


@pytest.mark.parametrize(
    "label", ["주 피 보험 자", "피 보 험 자 계약자", "피 보 험 자 수 익 자", "계약자"]
)
def test_spacing_does_not_invent_an_insured_role_from_other_labels(label):
    member = _member()
    source, row = _insured_cell("Family Member A", label=label)
    found = associate_policy_sources(source, members=(member,), expected_member_id=member.id)[
        row.node_id
    ]
    assert found.state == "UNRESOLVED"


@pytest.mark.parametrize("value", ["FamilyMemberA", "Family Member A Plus", "Family Member B"])
def test_label_spacing_does_not_relax_the_person_value(value):
    member = _member()
    source, row = _insured_cell(value, label="피 보 험 자")
    found = associate_policy_sources(source, members=(member,), expected_member_id=member.id)[
        row.node_id
    ]
    assert found.state != "RESOLVED"


@pytest.mark.parametrize("label", ["증 권 번 호", "계 약 번 호", "피 보 험 자", "피보험자  성명"])
def test_spaced_label_identity_is_removed_before_provider_window_clipping(label):
    from familycare_worker.ai.minimizer import SourceWindowMinimizer, minimize_text

    value = "SYNTHETIC-PRIVATE-VALUE"
    source = f"{label}: {value}\n가입금액: 317 KRW"
    minimized = minimize_text(source, sensitive_terms=())
    assert value not in minimized and "317 KRW" in minimized
    window = SourceWindowMinimizer(source, sensitive_terms=())
    start = source.index(value) + 3
    assert window.window(start, start + 8) == "[REDACTED]"
    assert source == f"{label}: {value}\n가입금액: 317 KRW"


def test_spaced_insured_word_inside_a_sentence_is_not_an_identity_label():
    from familycare_worker.ai.minimizer import minimize_text

    source = "피 보 험 자에게 보험금을 지급합니다."
    assert minimize_text(source, sensitive_terms=()) == source


def test_additional_spaced_contract_label_preserves_a_decisive_conflict():
    member = _member()
    source = _source(
        "보험증권 가입금액\n계약번호: synthetic-policy-001\n"
        "증 권 번 호: synthetic-policy-002\n피보험자: Family Member A"
    )
    before = source.to_dict()
    found = associate_policy_sources(source, members=(member,), expected_member_id=member.id)
    assert {item.state for item in found.values()} == {"AMBIGUOUS"}
    assert source.to_dict() == before
