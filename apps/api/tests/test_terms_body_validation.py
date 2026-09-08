"""API checks body semantics and source layout without trusting the Worker proposal."""

from copy import deepcopy
from dataclasses import asdict

import pytest
from familycare_api.insurance_documents.terms_body_validation import body_evidence

from workers.analyzer.tests.test_document_metadata import _structure


def _nodes(text: str):
    return _structure(text).to_dict()["nodes"]


def test_unlabelled_contractual_provisions_have_exact_source_evidence() -> None:
    text = (
        "제7조 (가상 지급 조건)\n회사는 보험수익자에게 보험금을 지급합니다.\n"
        "제8조 (가상 제외 조건)\n회사는 계약자가 고의로 발생시킨 손해에 보험금을 지급하지 않습니다."
    )
    nodes = _nodes(text)
    result = body_evidence(1, nodes)
    assert result is not None
    numbers, spans, sequence_verified = result
    assert numbers == (7, 8)
    assert len(spans) == 2
    assert sequence_verified
    for span in spans:
        source = next(node for node in nodes if node["node_id"] == span["node_id"])
        assert source["text"][span["start"] : span["end"]] == span["text"]


@pytest.mark.parametrize(
    "prefix",
    [
        "상품설명서",
        "다음은 약관을 설명하기 위한 예시입니다.",
        "약관 인용 예문",
        "목차",
        "제출 서류 목록",
        "Product brochure",
        "Table of contents",
        "Example clauses",
    ],
)
def test_the_same_clauses_in_explanatory_context_do_not_supply_role_authority(prefix: str) -> None:
    text = prefix + "\n제7조 (가상 지급 조건)\n회사는 보험수익자에게 보험금을 지급합니다."
    assert body_evidence(1, _nodes(text)) is None


@pytest.mark.parametrize(
    "body",
    [
        "보험금 지급 조건을 설명합니다.",
        "‘회사는 보험수익자에게 보험금을 지급합니다.’",
    ],
)
def test_article_heading_and_mention_or_quote_are_insufficient(body: str) -> None:
    assert body_evidence(1, _nodes("제7조 (가상 지급 조건)\n" + body)) is None


def test_operational_claim_document_language_is_not_a_brochure_marker() -> None:
    nodes = _nodes("제7조 (가상 지급 조건)\n회사는 보험금 청구서류를 확인하고 보험금을 지급합니다.")
    assert body_evidence(1, nodes)


def test_column_warning_does_not_override_proven_single_block_body() -> None:
    nodes = _nodes("제7조 (가상 지급 조건)\n회사는 보험수익자에게 보험금을 지급합니다.")
    ambiguous = deepcopy(nodes)
    ambiguous[0]["issue_codes"] = ["LINE_COLUMN_CONTEXT_UNRESOLVED"]
    assert body_evidence(1, ambiguous) is not None


def test_wrapped_relationship_retains_the_necessary_original_lines() -> None:
    nodes = _nodes(
        "제7조 가상 지급 조건\n이는 합성 조건입니다.\n회사는 보험수익자에게\n보험금을 지급합니다."
    )
    result = body_evidence(1, nodes)
    assert result is not None
    assert result[0] == (7,)
    assert len(result[1]) == 3


def test_internal_example_does_not_become_another_provision() -> None:
    nodes = _nodes(
        "제7조 (가상 지급 조건)\n회사는 보험수익자에게 보험금을 지급합니다.\n예시\n"
        "제8조 (가상 제외 조건)\n회사는 보험금을 지급하지 않습니다."
    )
    result = body_evidence(1, nodes)
    assert result is not None and result[0] == (7,)


@pytest.mark.parametrize("change", [None, "header_only", "header_below", "multiple_cells"])
def test_table_body_must_preserve_cell_and_header_context(change: str | None) -> None:
    from workers.analyzer.tests.test_terms_body import _table_nodes

    nodes = [asdict(node) for node in _table_nodes()]
    if change == "header_only":
        nodes = [nodes[0]]
        nodes[0]["text"] = "제7조 (가상 지급 조건)\n회사는 보험수익자에게 보험금을 지급합니다."
        nodes[0]["cells"][0]["text"] = nodes[0]["text"]
    elif change == "header_below":
        nodes[0]["bbox"] = (10, 110, 500, 120)
        nodes[0]["cells"][0]["bbox"] = nodes[0]["bbox"]
    elif change == "multiple_cells":
        nodes[1]["cells"] = (*nodes[1]["cells"], deepcopy(nodes[1]["cells"][0]))
    result = body_evidence(1, nodes)
    assert (result is not None) is (change is None)


def test_native_line_word_geometry_must_agree_with_original_source_order() -> None:
    from workers.analyzer.tests.test_document_structure import _build, _extraction, _page
    from workers.analyzer.tests.test_document_text_lines import _words

    source = _build(
        _extraction(
            _page(
                1,
                _words(
                    [
                        "제7조 (가상 지급 조건)",
                        "회사는 보험수익자에게 보험금을 지급합니다.",
                    ]
                ),
            )
        )
    ).to_dict()
    nodes = source["nodes"]
    assert body_evidence(1, nodes)
    line = next(node for node in nodes if node["kind"] == "TEXT_LINE")
    first, second = [
        next(node for node in nodes if node["node_id"] == span["block_node_id"])
        for span in line["source_spans"][:2]
    ]
    first["bbox"], second["bbox"] = second["bbox"], first["bbox"]
    assert body_evidence(1, nodes) is None


_PAYMENT = "제7조 (가상 지급 조건)\n회사는 보험수익자에게 보험금을 지급합니다."
_EXCLUSION = "제8조 (가상 제외 조건)\n회사는 보험금을 지급하지 않습니다."


def _columns(left: str, right: str):
    from workers.analyzer.tests.test_document_structure import _build, _extraction, _page
    from workers.analyzer.tests.test_document_text_lines import _words

    blocks = _words(left.splitlines())
    other = _words(right.splitlines())
    for block in other:
        block["reading_order"] += len(blocks)
        box = block["bbox"]
        block["bbox"] = [box[0] + 310, box[1], box[2] + 310, box[3]]
    return _build(_extraction(_page(1, [*blocks, *other]))).to_dict()["nodes"]


def test_independent_columns_prove_content_without_article_sequence_or_source_mutation() -> None:
    nodes = _columns(_PAYMENT, _EXCLUSION)
    before = deepcopy(nodes)
    result = body_evidence(1, nodes)
    assert result is not None
    assert result[0] == (7, 8)
    assert not result[2]
    assert len(result[1]) == 2
    assert nodes == before
    for span in result[1]:
        source = next(node for node in nodes if node["node_id"] == span["node_id"])
        assert source["text"][span["start"] : span["end"]] == span["text"]


def test_subject_and_predicate_in_different_columns_never_form_a_body() -> None:
    assert (
        body_evidence(
            1, _columns("제7조 (가상 지급 조건)\n회사는 보험수익자에게", "보험금을 지급합니다.")
        )
        is None
    )


@pytest.mark.parametrize("kind", ["side", "footer", "table"])
def test_unrelated_unresolved_node_does_not_erase_proved_body(kind: str) -> None:
    nodes = _columns(_PAYMENT, "합성 참고 문구")
    extra = deepcopy(nodes[0])
    extra.update(
        node_id="synthetic-unresolved",
        text="합성 부가 정보",
        source_spans=[],
        kind="TABLE_ROW" if kind == "table" else "BLOCK",
        schedulable=False,
        bbox=[400, 700, 500, 715] if kind == "footer" else [450, 100, 550, 120],
        issue_codes=["TABLE_LAYOUT_UNRESOLVED"],
    )
    nodes.append(extra)
    assert body_evidence(1, nodes) is not None


def test_reference_context_is_scoped_to_its_column() -> None:
    result = body_evidence(1, _columns("예시\n" + _PAYMENT, _EXCLUSION))
    assert result is not None and result[0] == (8,)


def test_page_heading_reference_context_blocks_both_columns() -> None:
    nodes = _columns(_PAYMENT, _EXCLUSION)
    prefix = deepcopy(nodes[0])
    prefix.update(
        node_id="synthetic-reference",
        kind="BLOCK",
        source_spans=[],
        text="상품설명서",
        bbox=[10, 1, 550, 11],
        reading_order=-1,
        schedulable=True,
        issue_codes=[],
    )
    nodes.append(prefix)
    assert body_evidence(1, nodes) is None


@pytest.mark.parametrize("change", ["horizontal", "vertical", "invalid_line"])
def test_body_continuation_requires_local_flow_proof(change: str) -> None:
    nodes = _columns("제7조 (가상 지급 조건)\n회사는 보험수익자에게\n보험금을 지급합니다.", "")
    target = next(
        node
        for node in nodes
        if node["kind"] == "TEXT_LINE" and node["text"] == "보험금을 지급합니다."
    )
    if change == "invalid_line":
        target["bbox"] = [1, 1, 2, 2]
    else:
        shift_x, shift_y = (200, 0) if change == "horizontal" else (0, 200)
        ids = {target["node_id"], *(span["block_node_id"] for span in target["source_spans"])}
        for node in nodes:
            if node["node_id"] in ids:
                x0, y0, x1, y1 = node["bbox"]
                node["bbox"] = [x0 + shift_x, y0 + shift_y, x1 + shift_x, y1 + shift_y]
    assert body_evidence(1, nodes) is None


@pytest.mark.parametrize("change", [None, "outside_cell", "different_text"])
def test_table_raw_duplicate_requires_cell_containment_and_text(change: str | None) -> None:
    from workers.analyzer.tests.test_terms_body import _table_nodes

    nodes = [asdict(node) for node in _table_nodes()]
    duplicate = deepcopy(nodes[1])
    duplicate.update(
        node_id="synthetic-raw-table",
        kind="BLOCK",
        cells=[],
        source_spans=[],
        schedulable=False,
        issue_codes=["TABLE_LAYOUT_UNRESOLVED"],
    )
    if change == "outside_cell":
        duplicate["bbox"] = [5, 25, 505, 105]
    elif change == "different_text":
        duplicate["text"] = "합성 다른 원문"
    nodes.append(duplicate)
    assert (body_evidence(1, nodes) is not None) is (change is None)


def test_table_rows_use_contained_cell_geometry_without_mutating_table_bounds() -> None:
    from workers.analyzer.tests.test_terms_body import _table_nodes

    nodes = [asdict(node) for node in _table_nodes()]
    for node in nodes:
        node["bbox"] = [5, 5, 510, 110]
    original = deepcopy(nodes)
    assert body_evidence(1, nodes) is not None
    assert nodes == original


def test_unlocated_unresolved_sidebar_does_not_erase_a_valid_column() -> None:
    nodes = _columns(_PAYMENT, "합성 부가 정보")
    extra = deepcopy(nodes[0])
    extra.update(
        node_id="synthetic-unlocated",
        kind="BLOCK",
        source_spans=[],
        bbox=None,
        text="합성 부가 정보",
        schedulable=False,
    )
    nodes.append(extra)
    assert body_evidence(1, nodes) is not None


def test_document_reference_title_above_columns_applies_to_whole_page() -> None:
    nodes = _columns(_PAYMENT, _EXCLUSION)
    extra = deepcopy(nodes[0])
    extra.update(
        node_id="synthetic-document-title",
        kind="BLOCK",
        source_spans=[],
        bbox=[10, 1, 75, 11],
        text="상품설명서",
        schedulable=True,
        issue_codes=[],
        reading_order=-1,
    )
    nodes.append(extra)
    assert body_evidence(1, nodes) is None


@pytest.mark.parametrize(
    "text",
    [
        "Article 3 (Payment)\nThe insurer shall pay the insurance benefit to the beneficiary.",
        "제2조 (용어의 의미)\n피보험자란 보험사고의 대상이 되는 사람을 말합니다.",
        "제1조 (계약 당사자)\n회사는 계약자와 이 보험계약을 체결합니다.",
    ],
)
def test_semantic_provisions_need_no_exact_terms_title(text: str) -> None:
    assert body_evidence(1, _nodes(text)) is not None


def test_unrelated_footer_table_reference_uses_cell_position() -> None:
    from workers.analyzer.tests.test_terms_body import _table_nodes

    nodes = _columns(_PAYMENT, "")
    footer = asdict(_table_nodes("상품설명서")[0])
    footer["bbox"] = [1, 1, 590, 780]
    footer["cells"][0]["bbox"] = [320, 700, 400, 715]
    nodes.append(footer)
    assert body_evidence(1, nodes) is not None


def test_unlocated_intervening_source_cannot_be_skipped_to_join_body() -> None:
    nodes = _columns("제7조 (가상 지급 조건)\n회사는 보험수익자에게\n보험금을 지급합니다.", "")
    subject = next(
        node
        for node in nodes
        if node["kind"] == "TEXT_LINE" and node["text"] == "회사는 보험수익자에게"
    )
    extra = deepcopy(nodes[0])
    extra.update(
        node_id="synthetic-intervening",
        kind="BLOCK",
        source_spans=[],
        bbox=None,
        text="합성 미확인 내용",
        schedulable=False,
        reading_order=subject["reading_order"] + 1,
    )
    nodes.append(extra)
    assert body_evidence(1, nodes) is None


def test_dense_word_source_is_reduced_to_proven_column_lines() -> None:
    filler = "\n가 나 다 라 마 바 사 아 자 차" * 28
    nodes = _columns(_PAYMENT + filler, _EXCLUSION + filler)
    assert sum(node["kind"] == "BLOCK" for node in nodes) >= 500
    result = body_evidence(1, nodes)
    assert result is not None and result[0] == (7, 8) and not result[2]
    assert len(result[1]) == 2


def test_spanning_internal_example_stops_later_articles_in_both_columns() -> None:
    nodes = _columns(
        _PAYMENT + "\n예시\n제9조 (가상 후속 조건)\n회사는 보험금을 지급합니다.",
        _EXCLUSION + "\n\n제10조 (가상 후속 조건)\n회사는 보험금을 지급합니다.",
    )
    marker = next(node for node in nodes if node["text"] == "예시")
    ids = {marker["node_id"], *(span["block_node_id"] for span in marker["source_spans"])}
    for node in nodes:
        if node["node_id"] in ids:
            x0, y0, _, y1 = node["bbox"]
            node["bbox"] = [x0, y0, 550, y1]
    result = body_evidence(1, nodes)
    assert result is not None and result[0] == (7, 8)
