"""Synthetic component boundaries and source-addressed document metadata."""

from dataclasses import replace
from uuid import UUID

import pytest
from familycare_worker.document_metadata import (
    DocumentMetadataError,
    analyze_document_metadata,
    metadata_proposal,
)
from familycare_worker.document_structure import (
    DocumentStructure,
    StructureCell,
    build_document_structure,
)


def _structure(*pages: str) -> DocumentStructure:
    return build_document_structure(
        {
            "schema_version": "1",
            "document_version_id": str(UUID(int=201)),
            "content_sha256": "a" * 64,
            "extractor_config_hash": "b" * 64,
            "pages": [
                {
                    "page_number": number,
                    "width_points": 600,
                    "height_points": 800,
                    "blocks": [{"text": text, "reading_order": 0, "bbox": [10, 10, 500, 700]}],
                    "tables": [],
                    "quality": {"classification": "TEXT_SUFFICIENT"},
                }
                for number, text in enumerate(pages, 1)
            ],
            "evidence": [],
        },
        extraction_id=UUID(int=202),
        extraction_revision="synthetic-metadata-v1",
    )


def test_mixed_document_roles_come_from_source_and_preserve_all_spans() -> None:
    source = _structure(
        "보험증권\n보험사: Sample Assurance\n상품명: Sample Policy\n가입금액: 7",
        "보험약관\n보험사: Sample Assurance\n상품명: Sample Policy\n제1조 목적",
        "상품설명서\n상품명: Sample Policy",
        "보험청약서\n상품명: Sample Policy",
    )
    result = analyze_document_metadata(source)
    assert [item.role for item in result.components] == [
        "policy",
        "terms",
        "product_explanation",
        "application",
    ]
    assert [(item.page_start, item.page_end) for item in result.components] == [
        (1, 1),
        (2, 2),
        (3, 3),
        (4, 4),
    ]
    assert result.revision == "document-metadata-v2"
    assert result.unresolved_pages == ()
    nodes = {node.node_id: node for node in source.nodes}
    for component in result.components:
        assert component.authority == "CONTENT_CLASSIFICATION_ONLY"
        for fact in component.facts:
            for span in fact.spans:
                assert nodes[span.node_id].text[span.start : span.end] == span.text
                assert component.page_start <= span.page_number <= component.page_end


def test_edition_date_is_not_an_applicability_boundary_and_two_editions_stay_separate() -> None:
    source = _structure(
        "보험약관\n상품코드: SAMPLE-001\n판본일: 2020.01.01\n제1조 목적",
        "보험약관\n상품코드: SAMPLE-001\n판본일: 2022.01.01\n제1조 목적",
    )
    result = analyze_document_metadata(source)
    assert len(result.components) == 2
    assert [
        next(f.value for f in c.facts if f.field == "edition_date") for c in result.components
    ] == ["2020-01-01", "2022-01-01"]
    assert all(not f.field.startswith("applicability") for c in result.components for f in c.facts)


def test_repeated_headers_merge_only_with_consistent_identity_and_each_page_role_proof() -> None:
    result = analyze_document_metadata(
        _structure(
            "보험약관\n상품코드: SAMPLE-001\n판본일: 2020-01-01\n제1조 목적",
            "보험약관\n상품코드: SAMPLE-001\n제2조 정의",
            "Unclassified content that might belong to another document",
            "보험약관\n상품코드: SAMPLE-001\n제4조 조건",
        )
    )
    assert [(c.page_start, c.page_end) for c in result.components] == [(1, 2), (4, 4)]
    assert result.unresolved_pages == (3,)
    assert {s.page_number for s in result.components[0].role_spans} == {1, 2}


def test_quoted_titles_and_product_examples_do_not_become_certificate_authority() -> None:
    result = analyze_document_metadata(
        _structure(
            "상품설명서\n예시 보험증권\n가입금액: 7\n보험약관을 참고하십시오",
            "This text mentions policy certificate and policy terms in a sentence.",
            "보험증권\n보험약관\n가입금액: 7\n제1조 목적",
        )
    )
    assert [c.role for c in result.components] == ["product_explanation"]
    assert result.unresolved_pages == (2, 3)


@pytest.mark.parametrize("text", ["청구 제출서류\n보험증권\n영수증", "제출 목록\n보험약관"])
def test_document_checklists_do_not_classify_a_page_by_standalone_body_mentions(text: str) -> None:
    result = analyze_document_metadata(_structure(text))
    assert result.components == ()
    assert result.unresolved_pages == (1,)


def test_checklist_heading_in_an_earlier_block_closes_the_title_area() -> None:
    source = _structure("보험증권")
    prefix = replace(
        source.nodes[0], node_id="synthetic-list-prefix", text="청구 제출서류", reading_order=0
    )
    source = replace(source, nodes=(prefix, replace(source.nodes[0], reading_order=1)))
    assert analyze_document_metadata(source).components == ()


def test_conflicting_metadata_is_retained_and_not_chosen_by_order() -> None:
    result = analyze_document_metadata(
        _structure(
            "보험약관\n보험사: Sample A\n보험사: Sample B\n"
            "적용시작일: 2020-01-01\n적용종료일: 2021-12-31"
        )
    )
    component = result.components[0]
    assert "insurer" in component.conflicting_fields
    assert {f.value for f in component.facts if f.field == "insurer"} == {"Sample A", "Sample B"}
    assert next(f.value for f in component.facts if f.field == "applicability_end") == "2021-12-31"


def test_multiple_rider_codes_are_not_a_conflict_and_references_keep_their_semantics() -> None:
    result = analyze_document_metadata(
        _structure(
            "보험약관\n특약코드: SAMPLE-R1\n특약코드: SAMPLE-R2\n"
            "약관코드: SAMPLE-T1\n참조약관코드: SAMPLE-T0"
        )
    )
    component = result.components[0]
    assert component.conflicting_fields == ()
    assert {f.value for f in component.facts if f.field == "rider_code"} == {
        "SAMPLE-R1",
        "SAMPLE-R2",
    }
    assert next(f.value for f in component.facts if f.field == "terms_reference") == "SAMPLE-T0"


@pytest.mark.parametrize("value", ["2020-02-30", "latest", "2020-01-01 or 2022-01-01"])
def test_invalid_dates_remain_an_explicit_unresolved_field(value: str) -> None:
    component = analyze_document_metadata(_structure(f"보험약관\n판본일: {value}")).components[0]
    assert "edition_date" in component.unresolved_fields
    assert not any(f.field == "edition_date" for f in component.facts)


def test_identity_is_deterministic_and_changes_with_source_lineage() -> None:
    source = _structure("보험약관\n상품코드: SAMPLE-001")
    first = analyze_document_metadata(source)
    assert first == analyze_document_metadata(source)
    changed = replace(source, lineage=replace(source.lineage, extraction_id=UUID(int=203)))
    assert first.components[0].identity != analyze_document_metadata(changed).components[0].identity
    assert "Sample" not in repr(first)


def test_ambiguous_native_columns_cannot_produce_metadata() -> None:
    source = _structure("보험약관\n보험사: Sample A")
    source = replace(
        source, nodes=(replace(source.nodes[0], issue_codes=("LINE_COLUMN_CONTEXT_UNRESOLVED",)),)
    )
    assert analyze_document_metadata(source).components == ()


def test_ambiguous_metadata_remains_unresolved_beside_a_valid_title() -> None:
    source = _structure("보험약관")
    ambiguous = replace(
        source.nodes[0],
        node_id="synthetic-ambiguous-line",
        kind="TEXT_LINE",
        text="보험사: Sample A",
        issue_codes=("LINE_COLUMN_CONTEXT_UNRESOLVED",),
    )
    component = analyze_document_metadata(
        replace(source, nodes=(*source.nodes, ambiguous))
    ).components[0]
    assert component.unresolved_fields == ("insurer",)
    assert component.facts == ()


def test_long_title_padding_does_not_overflow_the_metadata_span_contract() -> None:
    component = analyze_document_metadata(
        _structure(" " * 300 + "보험약관" + " " * 300)
    ).components[0]
    assert component.role_spans[0].text == "보험약관"


def test_repeated_metadata_cannot_exceed_the_neutral_contract_span_limit() -> None:
    source = _structure("보험약관\n" + "상품코드: SAMPLE-001\n" * 10001)
    with pytest.raises(DocumentMetadataError):
        metadata_proposal(source, UUID(int=205), "c" * 64)


@pytest.mark.parametrize("column,span,expected", [(1, None, True), (2, None, False), (1, 2, False)])
def test_table_metadata_requires_adjacent_unmerged_cells(
    column: int, span: int | None, expected: bool
) -> None:
    source = _structure("보험약관", "보험약관\n상품코드: SAMPLE-001")
    title = replace(source.nodes[0], bbox=(10, 1, 400, 9))
    row = replace(
        title,
        node_id="synthetic-metadata-row",
        kind="TABLE_ROW",
        bbox=(10, 20, 400, 40),
        text="상품코드\t001-SAMPLE",
        cells=(
            StructureCell(0, 0, "상품코드", None, "synthetic.cells.0", column_span=span),
            StructureCell(0, column, "001-SAMPLE", None, "synthetic.cells.1"),
        ),
    )
    source = replace(source, pages=source.pages[:1], nodes=(title, row))
    component = analyze_document_metadata(source).components[0]
    assert (
        any(f.field == "product_code" and f.value == "001-SAMPLE" for f in component.facts)
        == expected
    )


def test_inverted_applicability_dates_are_a_conflict() -> None:
    component = analyze_document_metadata(
        _structure("보험약관\n적용시작일: 2022-01-01\n적용종료일: 2020-01-01")
    ).components[0]
    assert set(component.conflicting_fields) == {"applicability_start", "applicability_end"}


def test_same_product_does_not_join_separate_certificates() -> None:
    result = analyze_document_metadata(
        _structure("보험증권\n상품코드: SAMPLE-001", "보험증권\n상품코드: SAMPLE-001")
    )
    assert len(result.components) == 2


def test_table_cell_title_and_column_headers_are_not_document_metadata() -> None:
    source = _structure("보험증권")
    raw = source.to_dict()["source_extraction"]
    raw["pages"][0]["blocks"][0]["bbox"] = [10, 40, 100, 60]
    raw["pages"][0]["tables"] = [
        {
            "bbox": [10, 10, 500, 700],
            "cells": [
                {"row_index": 0, "column_index": 0, "text": "상품명", "bbox": [10, 10, 100, 30]},
                {"row_index": 0, "column_index": 1, "text": "보험사", "bbox": [100, 10, 200, 30]},
                {"row_index": 1, "column_index": 0, "text": "보험증권", "bbox": [10, 40, 100, 60]},
                {"row_index": 1, "column_index": 1, "text": "Sample", "bbox": [100, 40, 200, 60]},
            ],
            "metadata_json": {"header_rows": [0]},
        }
    ]
    built = build_document_structure(
        raw, extraction_id=UUID(int=202), extraction_revision="synthetic-metadata-v1"
    )
    assert analyze_document_metadata(built).components == ()
    raw["pages"][0]["blocks"].append(
        {"text": "보험약관", "reading_order": 1, "bbox": [10, 710, 400, 750]}
    )
    built = build_document_structure(
        raw, extraction_id=UUID(int=202), extraction_revision="synthetic-metadata-v1"
    )
    assert analyze_document_metadata(built).components == ()
    # A genuine opening heading precedes the table in both geometry and reading order.
    raw["pages"][0]["blocks"][0]["reading_order"] = 1
    raw["pages"][0]["blocks"][1].update(reading_order=0, bbox=[10, 1, 400, 9])
    built = build_document_structure(
        raw, extraction_id=UUID(int=202), extraction_revision="synthetic-metadata-v1"
    )
    component = analyze_document_metadata(built).components[0]
    assert component.role == "terms"
    assert not component.facts


def test_opening_title_and_native_metadata_table_preserve_resolved_fields() -> None:
    source = _structure("보험약관")
    raw = source.to_dict()["source_extraction"]
    page = raw["pages"][0]
    page["blocks"][0]["bbox"] = [10, 1, 400, 9]
    page["blocks"].extend(
        [
            {"text": "상품코드", "reading_order": 1, "bbox": [10, 20, 100, 40]},
            {"text": "SAMPLE-001", "reading_order": 2, "bbox": [110, 20, 250, 40]},
        ]
    )
    page["tables"] = [
        {
            "bbox": [10, 20, 250, 40],
            "cells": [
                {"row_index": 0, "column_index": 0, "text": "상품코드", "bbox": [10, 20, 100, 40]},
                {
                    "row_index": 0,
                    "column_index": 1,
                    "text": "SAMPLE-001",
                    "bbox": [110, 20, 250, 40],
                },
            ],
        }
    ]
    built = build_document_structure(
        raw, extraction_id=UUID(int=202), extraction_revision="synthetic-metadata-v1"
    )
    component = analyze_document_metadata(built).components[0]
    assert component.facts[0].value == "SAMPLE-001"
    assert component.unresolved_fields == ()


@pytest.mark.parametrize(
    "heading", ["무배당 Sample 가족보험\n보험약관", "무배당 Sample 가족보험 약관"]
)
def test_product_cover_title_does_not_require_a_standalone_first_line(heading: str) -> None:
    source = _structure(heading + "\n보험회사 Sample Assurance\n상품코드 SAMPLE-A\n제1조 목적")
    metadata = analyze_document_metadata(source)
    assert len(metadata.components) == 1
    component = metadata.components[0]
    assert component.role == "terms"
    facts = {fact.field: fact.value for fact in component.facts}
    assert facts["product_name"] == "무배당 Sample 가족보험"
    assert facts["insurer"] == "Sample Assurance"
    assert facts["product_code"] == "SAMPLE-A"
    node = source.nodes[0]
    for fact in component.facts:
        for span in fact.spans:
            assert node.text[span.start : span.end] == span.text


@pytest.mark.parametrize(
    "text",
    [
        "보험금 청구 제출서류\n보험약관",
        "Sample 보험 참고자료\n보험약관",
        "무배당 Sample 보험 예시 약관",
        "보험약관을 읽어 주십시오",
        "참고할 보험약관 목록\nSample 보험 약관",
    ],
)
def test_product_words_in_reference_lists_do_not_open_a_cover_title(text: str) -> None:
    assert analyze_document_metadata(_structure(text)).components == ()


def test_unlabelled_product_body_is_not_cover_metadata() -> None:
    component = analyze_document_metadata(
        _structure("보험약관\n제1조 목적\n무배당 Sample 가족보험\n상품코드 SAMPLE-A")
    ).components[0]
    assert not any(fact.field == "product_name" for fact in component.facts)
    assert "product_code" in component.unresolved_fields


def test_plain_product_label_does_not_become_part_of_the_product_value() -> None:
    component = analyze_document_metadata(
        _structure("보험약관\n상품명 Sample 가족보험")
    ).components[0]
    assert [f.value for f in component.facts if f.field == "product_name"] == ["Sample 가족보험"]
    assert component.conflicting_fields == ()


def _table_cover(*, checklist: bool = False) -> DocumentStructure:
    source = _structure("보험약관")
    raw = source.to_dict()["source_extraction"]
    page = raw["pages"][0]
    page["blocks"] = [
        {"text": "보험약관", "reading_order": 0, "bbox": [10, 20, 150, 40]},
        {"text": "보험회사", "reading_order": 1, "bbox": [10, 60, 110, 80]},
        {"text": "Sample Assurance", "reading_order": 2, "bbox": [120, 60, 350, 80]},
    ]
    cells = [
        {"row_index": 1, "column_index": 0, "text": "보험약관", "bbox": [10, 20, 350, 40]},
        {"row_index": 2, "column_index": 0, "text": "보험회사", "bbox": [10, 60, 110, 80]},
        {"row_index": 2, "column_index": 1, "text": "Sample Assurance", "bbox": [120, 60, 350, 80]},
    ]
    if checklist:
        cells.insert(
            0,
            {"row_index": 0, "column_index": 0, "text": "청구 제출서류", "bbox": [10, 1, 350, 15]},
        )
    page["tables"] = [{"bbox": [10, 1, 350, 80], "cells": cells}]
    return build_document_structure(
        raw, extraction_id=UUID(int=202), extraction_revision="synthetic-table-cover-v1"
    )


def test_a_cover_in_a_single_table_cell_uses_original_cell_geometry() -> None:
    source = _table_cover()
    result = analyze_document_metadata(source)
    assert len(result.components) == 1 and result.components[0].role == "terms"
    assert (
        next(f.value for f in result.components[0].facts if f.field == "insurer")
        == "Sample Assurance"
    )
    span = result.components[0].role_spans[0]
    node = next(node for node in source.nodes if node.node_id == span.node_id)
    assert node.kind == "TABLE_ROW" and node.text[span.start : span.end] == "보험약관"


def test_single_cell_body_titles_cannot_escape_the_preceding_checklist_cell() -> None:
    assert analyze_document_metadata(_table_cover(checklist=True)).components == ()


@pytest.mark.parametrize("table", [False, True])
def test_an_explicit_reference_label_remains_metadata_without_application_authority(
    table: bool,
) -> None:
    if not table:
        component = analyze_document_metadata(
            _structure("보험약관\n참조약관코드 SAMPLE-TERMS")
        ).components[0]
        assert component.unresolved_fields == ()
        assert component.facts[0].field == "terms_reference"
        return
    source = _table_cover()
    nodes = list(source.nodes)
    row = next(node for node in nodes if node.kind == "TABLE_ROW" and len(node.cells) == 2)
    nodes[nodes.index(row)] = replace(
        row,
        text="참조약관코드\tSAMPLE-TERMS",
        cells=(
            replace(row.cells[0], text="참조약관코드"),
            replace(row.cells[1], text="SAMPLE-TERMS"),
        ),
    )
    component = analyze_document_metadata(replace(source, nodes=tuple(nodes))).components[0]
    assert component.unresolved_fields == ()
    assert [(fact.field, fact.value) for fact in component.facts] == [
        ("terms_reference", "SAMPLE-TERMS")
    ]


@pytest.mark.parametrize("header", [False, True])
def test_table_headers_and_checklists_with_a_metadata_label_do_not_classify(header: bool) -> None:
    source = _table_cover(checklist=not header)
    nodes = list(source.nodes)
    if header:
        nodes = [
            replace(node, row_role="header") if node.kind == "TABLE_ROW" else node for node in nodes
        ]
    else:
        row = next(node for node in nodes if node.kind == "TABLE_ROW" and node.row_index == 0)
        updated = replace(
            row,
            text="청구 제출서류\t보험사",
            cells=(
                replace(row.cells[0], bbox=(10, 1, 110, 15)),
                replace(row.cells[0], column_index=1, text="보험사", bbox=(120, 1, 350, 15)),
            ),
        )
        nodes[nodes.index(row)] = updated
    assert analyze_document_metadata(replace(source, nodes=tuple(nodes))).components == ()


def test_plain_label_cannot_hide_a_submission_list_heading() -> None:
    assert (
        analyze_document_metadata(
            _structure("보험사 제출 서류\n보험약관\n상품명 Sample 가족보험")
        ).components
        == ()
    )


def test_table_geometry_cannot_reverse_native_reading_order_across_columns() -> None:
    source = _table_cover()
    table = next(node for node in source.nodes if len(node.cells) == 2)
    caption = replace(
        source.nodes[0],
        text="청구 제출서류",
        bbox=(10, 22, 100, 40),
        schedulable=True,
        reading_order=0,
    )
    title = replace(
        source.nodes[0],
        node_id="synthetic-right-column",
        text="보험약관",
        bbox=(250, 20, 350, 40),
        schedulable=True,
        reading_order=1,
    )
    source = replace(source, nodes=(caption, title, table))
    assert analyze_document_metadata(source).components == ()
