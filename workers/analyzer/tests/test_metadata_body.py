"""Source spans establish body ranges without repeated cover labels."""

import json
from dataclasses import replace
from uuid import UUID

from familycare_worker.document_metadata import analyze_document_metadata, metadata_proposal

from workers.analyzer.tests.test_document_metadata import _structure

FIRST = "제7조 (가상 지급 조건)\n회사는 보험수익자에게 보험금을 지급합니다."
SECOND = "제8조 (가상 제외 조건)\n회사는 보험금을 지급하지 않습니다."


def test_body_provisions_continue_without_repeated_role_or_product_labels() -> None:
    source = _structure(FIRST, SECOND)
    result = analyze_document_metadata(source)
    assert result.revision == "document-metadata-v3"
    assert [(c.role, c.page_start, c.page_end) for c in result.components] == [("terms", 1, 2)]
    payload = metadata_proposal(source, UUID(int=905), "c" * 64)
    evidence = payload["components"][0]["range_evidence"]
    assert [item["basis"] for item in evidence] == ["CONTRACTUAL_PROVISIONS"] * 2
    assert [item["previous_page"] for item in evidence] == [None, 1]
    assert [item["article_numbers"] for item in evidence] == [[7], [8]]
    assert result.unresolved_pages == ()


def test_verified_cover_metadata_can_accompany_its_first_body_article() -> None:
    result = analyze_document_metadata(
        _structure(
            "보험약관\n보험사: Sample Assurance\n상품코드: SYNTHETIC-A",
            "제1조 (계약 당사자)\n회사는 계약자와 이 보험계약을 체결합니다.",
        )
    )
    assert len(result.components) == 1
    assert result.components[0].page_end == 2
    assert {fact.field for fact in result.components[0].facts} == {"insurer", "product_code"}


def test_other_document_boundary_and_unresolved_gap_are_not_absorbed() -> None:
    result = analyze_document_metadata(_structure(FIRST, "Synthetic unreadable gap", SECOND))
    assert [(c.page_start, c.page_end) for c in result.components] == [(1, 1), (3, 3)]
    assert result.unresolved_pages == (2,)
    result = analyze_document_metadata(_structure(FIRST, "상품설명서\n" + SECOND))
    assert [(c.role, c.page_start, c.page_end) for c in result.components] == [
        ("terms", 1, 1),
        ("product_explanation", 2, 2),
    ]


def test_restarted_articles_do_not_merge_two_terms_documents() -> None:
    result = analyze_document_metadata(_structure(FIRST, SECOND.replace("제8조", "제1조")))
    assert [(c.page_start, c.page_end) for c in result.components] == [(1, 1), (2, 2)]


def test_example_context_remains_in_force_on_the_following_page() -> None:
    result = analyze_document_metadata(
        _structure(
            FIRST + "\n다음은 약관을 설명하기 위한 예시입니다.",
            SECOND,
        )
    )
    assert [(c.page_start, c.page_end) for c in result.components] == [(1, 1)]
    assert result.unresolved_pages == (2,)


def test_formal_terms_boundary_can_end_preceding_explanatory_context() -> None:
    result = analyze_document_metadata(
        _structure(
            "상품설명서\n예시",
            SECOND,
            "보험약관\n상품코드: SYNTHETIC-A",
            "제1조 (계약 당사자)\n회사는 계약자와 이 보험계약을 체결합니다.",
        )
    )
    assert [(c.role, c.page_start, c.page_end) for c in result.components] == [
        ("product_explanation", 1, 1),
        ("terms", 3, 4),
    ]


def test_large_terms_source_keeps_bounded_metadata_witnesses_and_full_ir() -> None:
    page = "\n".join(
        f"제{number}조 (합성 지급 조건)\n회사는 보험수익자에게 보험금을 지급합니다."
        for number in range(1, 65)
    )
    source = _structure(*([page] * 500))
    payload = metadata_proposal(source, UUID(int=905), "c" * 64)
    assert len(payload["components"]) == 500
    assert payload["unresolved_pages"] == []
    assert len(json.dumps(payload, ensure_ascii=False).encode()) < 2 * 1024 * 1024
    assert len(source.pages) == 500
    assert all(len(item["role_spans"]) == 2 for item in payload["components"])


def test_unresolved_external_table_headers_do_not_create_unverifiable_metadata() -> None:
    from workers.analyzer.tests.test_terms_body import _table_nodes

    source = _structure(FIRST)
    _, body = _table_nodes()
    body = replace(
        body,
        context_node_ids=("synthetic-external-header",),
        continuation_of="synthetic-external-table",
    )
    result = analyze_document_metadata(replace(source, nodes=(body,)))
    assert result.components == ()
    assert result.unresolved_pages == (1,)
