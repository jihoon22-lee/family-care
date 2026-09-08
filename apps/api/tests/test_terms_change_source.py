"""Whole synthetic amendment headers identify targets without creating enrollment."""

import hashlib
from dataclasses import replace
from datetime import date
from uuid import UUID

import pytest
from familycare_api.clauses.terms_change_source import ChangeMember, observe_terms_change
from familycare_worker.document_metadata import metadata_proposal

from workers.analyzer.tests.test_document_metadata import _structure

HOUSEHOLD, MEMBER = UUID(int=906), UUID(int=907)
TEXT = "\n".join(
    (
        "계약변경서",
        "계약번호: synthetic-policy-001",
        "피보험자: Family Member A",
        "보험사: Sample Assurance",
        "변경구분: 조건변경",
        "변경방식: 교체",
        "적용범위: 특약",
        "대상특약명: Sample Daily Rider",
        "변경전약관코드: SAMPLE-T-A",
        "변경전판본코드: SAMPLE-E-A",
        "변경후약관코드: SAMPLE-T-B",
        "변경후판본코드: SAMPLE-E-B",
        "변경적용일: 2025-07-01",
        "변경적용종료일: 2025-12-31",
    )
)


def _observe(text=TEXT, *, members=None, mutate=None):
    source = _structure(text)
    component = metadata_proposal(source, UUID(int=908), "c" * 64)["components"][0]
    if mutate:
        mutate(component)
    return observe_terms_change(
        component,
        source.to_dict(),
        metadata_revision="document-metadata-v4",
        household_space_id=HOUSEHOLD,
        family_member_id=MEMBER,
        members=members or (ChangeMember(HOUSEHOLD, MEMBER, "Family Member A", "Member A", 1),),
    )


def test_amendment_header_preserves_target_and_temporal_semantics() -> None:
    observed = _observe()
    assert observed.status == "MATCH"
    assert observed.family_member_id == MEMBER and observed.member_version == 1
    assert observed.contract_number_sha256 == hashlib.sha256(b"synthetic-policy-001").hexdigest()
    assert observed.insurer_key == "sample assurance"
    assert observed.scope_kind == "RIDER" and observed.rider_name_key == "sample daily rider"
    assert observed.operation == "REPLACE" and observed.change_kind == "AMENDMENT"
    assert observed.effective_from == date(2025, 7, 1)
    assert observed.effective_through == date(2025, 12, 31)
    assert observed.previous_terms_code == "sample-t-a"
    assert observed.previous_edition_code == "sample-e-a"
    assert observed.new_terms_code == "sample-t-b" and observed.new_edition_code == "sample-e-b"
    assert "synthetic-policy-001" not in repr(observed)
    assert "Family Member A" not in repr(observed)


@pytest.mark.parametrize("replacement", ["不明", "2025-02-30", ""])
def test_unknown_effective_date_preserves_the_identified_target(replacement: str) -> None:
    observed = _observe(TEXT.replace("2025-07-01", replacement))
    assert observed.status == "UNKNOWN" and observed.effective_from is None
    assert observed.family_member_id == MEMBER and observed.rider_name_key == "sample daily rider"
    assert observed.operation == "REPLACE"


def test_an_explicitly_different_insured_is_not_a_change_for_this_member() -> None:
    members = (
        ChangeMember(HOUSEHOLD, MEMBER, "Family Member A", "Member A", 1),
        ChangeMember(HOUSEHOLD, UUID(int=909), "Family Member B", "Member B", 1),
    )
    observed = _observe(TEXT.replace("Family Member A", "Family Member B"), members=members)
    assert observed.status == "NO_MATCH" and "WRONG_MEMBER" in observed.reason_codes


def test_alias_collision_never_picks_the_first_member() -> None:
    members = (
        ChangeMember(HOUSEHOLD, MEMBER, "Family Member A", "Shared Alias", 1),
        ChangeMember(HOUSEHOLD, UUID(int=909), "Family Member B", "Shared Alias", 1),
    )
    observed = _observe(TEXT.replace("Family Member A", "Shared Alias"), members=members)
    assert observed.status == "UNKNOWN" and observed.family_member_id is None


def test_a_whole_contract_scope_requires_explicit_source_wording() -> None:
    text = TEXT.replace("적용범위: 특약\n대상특약명: Sample Daily Rider", "적용범위: 계약전체")
    assert _observe(text).scope_kind == "CONTRACT"
    observed = _observe(text.replace("적용범위: 계약전체", ""))
    assert observed.status == "UNKNOWN" and observed.scope_kind is None


def test_component_role_tampering_does_not_grant_change_authority() -> None:
    observed = _observe(mutate=lambda component: component.update(role="terms"))
    assert observed.status == "UNKNOWN" and "SOURCE_CLASSIFICATION_INVALID" in observed.reason_codes


def test_multiple_contract_numbers_do_not_become_a_new_enrollment() -> None:
    observed = _observe(TEXT + "\n계약번호: synthetic-policy-002")
    assert observed.status == "UNKNOWN" and observed.contract_number_sha256 is None


def test_an_explicit_empty_end_date_is_not_an_unbounded_amendment() -> None:
    observed = _observe(TEXT.replace("2025-12-31", ""))
    assert observed.status == "UNKNOWN"
    assert "EFFECTIVE_END_UNRESOLVED" in observed.reason_codes


def test_example_table_does_not_supply_the_missing_effective_date() -> None:
    from workers.analyzer.tests.test_terms_body import _table_nodes

    text = TEXT.replace("변경적용일: 2025-07-01\n변경적용종료일: 2025-12-31", "")
    source = _structure(text)
    header, row = _table_nodes("예시")
    header = replace(
        header,
        bbox=(10, 705, 500, 720),
        cells=(replace(header.cells[0], bbox=(10, 705, 500, 720)),),
    )
    row = replace(
        row,
        text="변경적용일\t2025-07-01",
        bbox=(10, 725, 500, 745),
        cells=(
            replace(row.cells[0], text="변경적용일", bbox=(10, 725, 200, 745)),
            replace(row.cells[0], text="2025-07-01", column_index=1, bbox=(205, 725, 500, 745)),
        ),
    )
    source = replace(source, nodes=(*source.nodes, header, row))
    component = metadata_proposal(source, UUID(int=908), "c" * 64)["components"][0]
    observed = observe_terms_change(
        component,
        source.to_dict(),
        metadata_revision="document-metadata-v4",
        household_space_id=HOUSEHOLD,
        family_member_id=MEMBER,
        members=(ChangeMember(HOUSEHOLD, MEMBER, "Family Member A", "Member A", 1),),
    )
    assert observed.status == "UNKNOWN" and observed.effective_from is None


def _observe_structure(source):
    component = metadata_proposal(source, UUID(int=908), "c" * 64)["components"][0]
    return observe_terms_change(
        component,
        source.to_dict(),
        metadata_revision="document-metadata-v4",
        household_space_id=HOUSEHOLD,
        family_member_id=MEMBER,
        members=(ChangeMember(HOUSEHOLD, MEMBER, "Family Member A", "Member A", 1),),
    )


@pytest.mark.parametrize("foreign_box", [(350, 10, 580, 30), (10, 760, 250, 780)])
def test_unrelated_column_or_footer_cannot_supply_effective_date(foreign_box) -> None:
    source = _structure(TEXT.replace("변경적용일: 2025-07-01\n변경적용종료일: 2025-12-31", ""))
    body = replace(source.nodes[0], bbox=(10, 10, 250, 650))
    foreign = replace(
        body,
        node_id="synthetic-foreign-date",
        reading_order=1,
        text="변경적용일: 2025-07-01",
        bbox=foreign_box,
    )
    observed = _observe_structure(replace(source, nodes=(body, foreign)))
    assert observed.status == "UNKNOWN" and observed.effective_from is None


def test_adjacent_native_date_retains_exact_original_span() -> None:
    source = _structure(TEXT.replace("변경적용일: 2025-07-01\n변경적용종료일: 2025-12-31", ""))
    body = replace(source.nodes[0], bbox=(10, 10, 250, 650))
    following = replace(
        body,
        node_id="synthetic-following-date",
        reading_order=1,
        text="변경적용일: 2025-07-01",
        bbox=(10, 655, 250, 675),
    )
    observed = _observe_structure(replace(source, nodes=(body, following)))
    assert observed.status == "MATCH" and observed.effective_from == date(2025, 7, 1)
    field = next(field for field in observed.source_fields if field.name == "effective_from")
    assert field.spans[0]["node_id"] == following.node_id


@pytest.mark.parametrize("bad_box", [(0, 0, 0, 0), (400, 725, 500, 745)])
def test_invalid_or_reversed_table_pair_geometry_cannot_supply_date(bad_box) -> None:
    from workers.analyzer.tests.test_terms_body import _table_nodes

    source = _structure(TEXT.replace("변경적용일: 2025-07-01\n변경적용종료일: 2025-12-31", ""))
    _, row = _table_nodes()
    row = replace(
        row,
        context_node_ids=(),
        text="변경적용일\t2025-07-01",
        bbox=(10, 725, 500, 745),
        cells=(
            replace(row.cells[0], text="변경적용일", bbox=bad_box),
            replace(row.cells[0], text="2025-07-01", column_index=1, bbox=(205, 725, 350, 745)),
        ),
    )
    observed = _observe_structure(replace(source, nodes=(*source.nodes, row)))
    assert observed.status == "UNKNOWN" and observed.effective_from is None


def test_valid_adjacent_table_pair_supplies_its_own_date() -> None:
    from workers.analyzer.tests.test_terms_body import _table_nodes

    source = _structure(TEXT.replace("변경적용일: 2025-07-01\n변경적용종료일: 2025-12-31", ""))
    _, row = _table_nodes()
    row = replace(
        row,
        context_node_ids=(),
        text="변경적용일\t2025-07-01",
        bbox=(10, 725, 500, 745),
        cells=(
            replace(row.cells[0], text="변경적용일", bbox=(10, 725, 200, 745)),
            replace(row.cells[0], text="2025-07-01", column_index=1, bbox=(205, 725, 500, 745)),
        ),
    )
    observed = _observe_structure(replace(source, nodes=(*source.nodes, row)))
    assert observed.status == "MATCH" and observed.effective_from == date(2025, 7, 1)


def test_intervening_unidentified_narrative_cannot_lend_a_date_to_amendment() -> None:
    observed = _observe(
        TEXT.replace("변경적용일:", "다음은 다른 계약에 대한 안내입니다.\n변경적용일:")
    )
    assert observed.status == "UNKNOWN" and observed.effective_from is None


def test_fields_before_the_amendment_title_do_not_establish_the_target() -> None:
    source = _structure(
        TEXT.replace(
            "계약변경서\n계약번호: synthetic-policy-001",
            "계약번호: synthetic-policy-001\n계약변경서",
        )
    )
    assert not metadata_proposal(source, UUID(int=908), "c" * 64)["components"]


def test_thin_reference_heading_between_regions_cannot_lend_date() -> None:
    source = _structure(TEXT.replace("변경적용일: 2025-07-01\n변경적용종료일: 2025-12-31", ""))
    body = replace(source.nodes[0], bbox=(10, 10, 250, 650))
    header = replace(
        body, node_id="synthetic-reference", text="예시", reading_order=1, bbox=(0, 652, 500, 654)
    )
    following = replace(
        body,
        node_id="synthetic-date",
        text="변경적용일: 2025-07-01",
        reading_order=2,
        bbox=(10, 660, 250, 680),
    )
    observed = _observe_structure(replace(source, nodes=(body, header, following)))
    assert observed.status == "UNKNOWN" and observed.effective_from is None


def test_invalid_lineage_cannot_hide_a_conflicting_raw_date() -> None:
    from familycare_worker.document_structure import SourceTextSpan

    source = _structure(TEXT)
    raw = replace(
        source.nodes[0],
        node_id="synthetic-conflicting-date",
        text="변경적용종료일:",
        reading_order=1,
        bbox=(10, 705, 500, 725),
    )
    forged = replace(
        raw,
        node_id="synthetic-forged-line",
        kind="TEXT_LINE",
        bbox=(400, 760, 580, 780),
        source_spans=(
            SourceTextSpan(
                block_node_id=raw.node_id,
                block_start=0,
                block_end=len(raw.text),
                line_start=0,
                line_end=len(raw.text),
            ),
        ),
    )
    observed = _observe_structure(replace(source, nodes=(*source.nodes, raw, forged)))
    assert observed.status == "UNKNOWN"


@pytest.mark.parametrize("header_label,expected", [("예시", "UNKNOWN"), ("변경정보", "MATCH")])
def test_table_value_column_header_controls_its_date(header_label, expected) -> None:
    from workers.analyzer.tests.test_terms_body import _table_nodes

    source = _structure(TEXT.replace("변경적용일: 2025-07-01\n변경적용종료일: 2025-12-31", ""))
    header, row = _table_nodes()
    header = replace(
        header,
        text=f"항목\t{header_label}",
        bbox=(10, 705, 500, 720),
        cells=(
            replace(header.cells[0], text="항목", bbox=(10, 705, 200, 720)),
            replace(header.cells[0], text=header_label, column_index=1, bbox=(205, 705, 500, 720)),
        ),
    )
    row = replace(
        row,
        context_node_ids=(header.node_id,),
        text="변경적용일\t2025-07-01",
        bbox=(10, 725, 500, 745),
        cells=(
            replace(row.cells[0], text="변경적용일", bbox=(10, 725, 200, 745)),
            replace(row.cells[0], text="2025-07-01", column_index=1, bbox=(205, 725, 500, 745)),
        ),
    )
    observed = _observe_structure(replace(source, nodes=(*source.nodes, header, row)))
    assert observed.status == expected
