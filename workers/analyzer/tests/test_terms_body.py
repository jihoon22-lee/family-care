"""Synthetic page observations distinguish operative clauses from quoted examples."""

from dataclasses import replace

import pytest
from familycare_worker.document_structure import StructureCell, StructureNode
from familycare_worker.terms_body import observe_terms_body, role_witness

from workers.analyzer.tests.test_document_metadata import _structure
from workers.analyzer.tests.test_document_structure import _build, _extraction, _page
from workers.analyzer.tests.test_document_text_lines import _words

_PAYMENT = "제7조 (가상 지급 조건)\n회사는 보험수익자에게 보험금을 지급합니다."
_EXCLUSION = (
    "제8조 (가상 제외 조건)\n회사는 계약자가 고의로 발생시킨 손해에 보험금을 지급하지 않습니다."
)


def test_unlabelled_numbered_body_retains_two_distinct_operative_provisions() -> None:
    source = _structure(_PAYMENT + "\n" + _EXCLUSION)
    original = source.to_dict()
    observed = observe_terms_body(1, source.nodes)
    assert observed.status == "SUPPORTED"
    assert [item.article_number for item in observed.provisions] == [7, 8]
    assert observed.provisions[0].semantic_kinds == ("PAYMENT",)
    assert observed.provisions[1].semantic_kinds == ("EXCLUSION",)
    by_id = {node.node_id: node for node in source.nodes}
    for provision in observed.provisions:
        for span in (provision.heading, *provision.body):
            assert span.page_number == 1
            assert 0 < len(span.text) <= 240
            assert by_id[span.node_id].text[span.start : span.end] == span.text
    assert source.to_dict() == original
    assert "보험금" not in repr(observed)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (_PAYMENT, "PAYMENT"),
        ("제1조 (계약 당사자)\n회사는 계약자와 이 보험계약을 체결합니다.", "PARTIES"),
        ("제2조 (용어의 의미)\n피보험자란 보험사고의 대상이 되는 사람을 말합니다.", "DEFINITION"),
        (
            "Article 3 (Payment)\nThe insurer shall pay the insurance benefit to the beneficiary.",
            "PAYMENT",
        ),
    ],
)
def test_one_grounded_provision_is_sufficient(text: str, kind: str) -> None:
    observed = observe_terms_body(1, _structure(text).nodes)
    assert observed.status == "SUPPORTED"
    assert kind in observed.provisions[0].semantic_kinds


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
def test_identical_operative_text_in_explanatory_context_is_not_terms(prefix: str) -> None:
    assert observe_terms_body(1, _structure(prefix + "\n" + _PAYMENT).nodes).status == "UNSUPPORTED"


def test_prior_node_context_cannot_be_hidden_by_a_later_article() -> None:
    source = _structure(_PAYMENT)
    body = replace(source.nodes[0], reading_order=1, bbox=(10, 30, 500, 120))
    prefix = replace(
        body, node_id="synthetic-prefix", text="상품설명서", reading_order=0, bbox=(10, 10, 500, 20)
    )
    assert observe_terms_body(1, (prefix, body)).status == "UNSUPPORTED"


@pytest.mark.parametrize(
    "text",
    [
        "제7조 (가상 지급 조건) ........ 7\n제8조 (가상 제외 조건) ........ 8",
        "제7조 (가상 지급 조건)\n보험금 지급 조건을 설명합니다.",
        "제7조 (가상 지급 조건)\n“회사는 보험수익자에게 보험금을 지급합니다.”",
    ],
)
def test_numbered_headings_or_quoted_text_alone_do_not_establish_terms(text: str) -> None:
    assert observe_terms_body(1, _structure(text).nodes).status == "UNSUPPORTED"


def test_operational_claim_document_language_does_not_count_as_a_checklist() -> None:
    source = _structure("제9조 (지급 절차)\n회사는 보험금 청구서류를 확인하고 보험금을 지급합니다.")
    assert observe_terms_body(1, source.nodes).status == "SUPPORTED"


@pytest.mark.parametrize("example_heading", ["예시", "다음은 약관을 설명하기 위한 예시입니다."])
def test_an_internal_example_does_not_erase_or_extend_a_real_provision(
    example_heading: str,
) -> None:
    observed = observe_terms_body(
        1, _structure(_PAYMENT + "\n" + example_heading + "\n" + _EXCLUSION).nodes
    )
    assert observed.status == "SUPPORTED"
    assert [item.article_number for item in observed.provisions] == [7]
    assert all(example_heading not in span.text for span in observed.provisions[0].body)


def test_operative_sentence_can_mention_example_forms_without_being_an_example() -> None:
    observed = observe_terms_body(
        1,
        _structure(
            "제9조 (지급 절차)\n회사는 예시 서식을 포함한 보험금 청구서류를 확인하고 "
            "보험금을 지급합니다."
        ).nodes,
    )
    assert observed.status == "SUPPORTED"


def test_native_word_lines_preserve_their_source_addresses() -> None:
    source = _build(_extraction(_page(1, _words(_PAYMENT.splitlines()))))
    observed = observe_terms_body(1, source.nodes)
    assert observed.status == "SUPPORTED"
    kinds = {node.node_id: node.kind for node in source.nodes}
    assert kinds[observed.provisions[0].heading.node_id] == "TEXT_LINE"
    assert all(kinds[span.node_id] == "TEXT_LINE" for span in observed.provisions[0].body)


@pytest.mark.parametrize("change", ["text", "missing_block", "geometry", "word_order"])
def test_forged_or_ambiguous_native_lines_are_not_supported(change: str) -> None:
    source = _build(_extraction(_page(1, _words(_PAYMENT.splitlines()))))
    line = next(node for node in source.nodes if node.kind == "TEXT_LINE")
    nodes = list(source.nodes)
    if change == "missing_block":
        nodes = [node for node in nodes if node.node_id != line.source_spans[0].block_node_id]
    elif change == "word_order":
        first, second = [
            next(node for node in nodes if node.node_id == span.block_node_id)
            for span in line.source_spans[:2]
        ]
        nodes[nodes.index(first)] = replace(first, bbox=second.bbox)
        nodes[nodes.index(second)] = replace(second, bbox=first.bbox)
    else:
        changed = replace(
            line,
            **{
                "text": {"text": line.text + " forged"},
                "geometry": {"bbox": (0, 0, 1, 1)},
            }[change],
        )
        nodes[nodes.index(line)] = changed
    assert observe_terms_body(1, nodes).status == "AMBIGUOUS"


def _table_nodes(header: str = "条項") -> tuple[StructureNode, ...]:
    heading = StructureNode(
        "synthetic-header",
        "TABLE_ROW",
        1,
        "native",
        0,
        header,
        "/synthetic/table/0",
        (10, 10, 500, 20),
        table_id="synthetic-table",
        row_role="header",
        row_index=0,
        cells=(StructureCell(0, 0, header, (10, 10, 500, 20), "/synthetic/cell/0"),),
    )
    body = replace(
        heading,
        node_id="synthetic-data",
        reading_order=1,
        text=_PAYMENT,
        bbox=(10, 30, 500, 100),
        row_role="data",
        row_index=1,
        cells=(StructureCell(1, 0, _PAYMENT, (10, 30, 500, 100), "/synthetic/cell/1"),),
        context_node_ids=(heading.node_id,),
    )
    return heading, body


def test_data_cell_body_retains_header_context_without_using_header_as_a_clause() -> None:
    observed = observe_terms_body(1, _table_nodes())
    assert observed.status == "SUPPORTED"
    assert observed.table_contexts[0].header_node_ids == ("synthetic-header",)
    assert observed.provisions[0].heading.node_id == "synthetic-data"
    assert observe_terms_body(1, _table_nodes("설명을 위한 약관 예시")).status == "UNSUPPORTED"
    header, _ = _table_nodes(_PAYMENT)
    assert observe_terms_body(1, (header,)).status == "UNSUPPORTED"


def test_multiple_cells_cannot_be_flattened_into_an_operative_provision() -> None:
    header, body = _table_nodes()
    body = replace(
        body,
        cells=(
            body.cells[0],
            StructureCell(1, 1, "synthetic extra column", (501, 30, 550, 100), "/synthetic/cell/2"),
        ),
    )
    assert observe_terms_body(1, (header, body)).status == "AMBIGUOUS"


def test_external_table_context_is_returned_as_an_unresolved_reference_not_a_header() -> None:
    _, body = _table_nodes()
    body = replace(
        body,
        page_number=2,
        context_node_ids=("synthetic-prior-page-context",),
        continuation_of="synthetic-prior-table",
    )
    observed = observe_terms_body(2, (body,))
    assert observed.status == "SUPPORTED"
    context = observed.table_contexts[0]
    assert context.header_node_ids == ()
    assert context.context_node_ids == ("synthetic-prior-page-context",)
    assert context.continuation_of == "synthetic-prior-table"


def test_a_claimed_table_header_below_its_data_is_ambiguous() -> None:
    header, body = _table_nodes()
    header = replace(
        header,
        bbox=(10, 110, 500, 120),
        cells=(replace(header.cells[0], bbox=(10, 110, 500, 120)),),
    )
    assert observe_terms_body(1, (header, body)).status == "AMBIGUOUS"


@pytest.mark.parametrize("change", ["duplicate", "wrong_page", "overlap", "reverse_articles"])
def test_page_and_article_order_ambiguity_is_explicit(change: str) -> None:
    source = _structure(_PAYMENT)
    node = source.nodes[0]
    if change == "duplicate":
        nodes = (node, node)
    elif change == "wrong_page":
        nodes = (replace(node, page_number=2),)
    elif change == "overlap":
        nodes = (node, replace(node, node_id="synthetic-overlap", reading_order=1))
    else:
        nodes = _structure(_EXCLUSION + "\n" + _PAYMENT).nodes
    assert observe_terms_body(1, nodes).status == "AMBIGUOUS"


def test_large_input_or_oversized_evidence_does_not_return_truncated_authority() -> None:
    node = _structure(_PAYMENT).nodes[0]
    assert observe_terms_body(1, (node,) * 4097).status == "UNSUPPORTED"
    oversized = "제7조 (조건)\n회사는 " + "가상조건 " * 80 + "보험금을 지급합니다."
    assert observe_terms_body(1, _structure(oversized).nodes).status == "UNSUPPORTED"


def test_role_witness_omits_descriptive_surroundings_without_changing_observations() -> None:
    source = _structure(
        "제7조 (가상 지급 조건)\n이 문단은 절차의 순서를 다룹니다.\n"
        "회사는 보험수익자에게 보험금을 지급합니다.\n추가 세부사항은 뒤에 있습니다."
    )
    observed = observe_terms_body(1, source.nodes)
    assert observed.status == "SUPPORTED"
    assert len(observed.provisions[0].body) == 3
    witness = role_witness(observed)
    assert witness == (observed.provisions[0].heading, observed.provisions[0].body[1])
    assert len(observed.provisions[0].body) == 3
    by_id = {node.node_id: node for node in source.nodes}
    for span in witness:
        assert by_id[span.node_id].text[span.start : span.end] == span.text


def test_role_witness_uses_only_the_first_supported_provision_on_a_long_page() -> None:
    source = _structure(
        "\n".join(
            f"제{number}조 (가상 지급 조건)\n회사는 보험수익자에게 보험금을 지급합니다."
            for number in range(1, 41)
        )
    )
    observed = observe_terms_body(1, source.nodes)
    assert observed.status == "SUPPORTED"
    assert len(observed.provisions) == 40
    assert role_witness(observed) == (
        observed.provisions[0].heading,
        observed.provisions[0].body[0],
    )


def test_role_witness_retains_a_wrapped_operative_sentence() -> None:
    source = _structure(
        "제7조 (가상 지급 조건)\n이 문단은 절차를 다룹니다.\n"
        "회사는 보험수익자에게\n보험금을 지급합니다.\n추가 세부사항은 뒤에 있습니다."
    )
    observed = observe_terms_body(1, source.nodes)
    assert observed.status == "SUPPORTED"
    provision = observed.provisions[0]
    assert role_witness(observed) == (provision.heading, *provision.body[1:3])


def test_role_witness_prefers_fewer_spans_before_earlier_source_order() -> None:
    source = _structure(
        "제7조 (가상 지급 조건)\n회사는 보험수익자에게\n보험금을 지급합니다.\n"
        "회사는 보험금을 지급하지 않습니다."
    )
    observed = observe_terms_body(1, source.nodes)
    assert observed.status == "SUPPORTED"
    provision = observed.provisions[0]
    assert role_witness(observed) == (provision.heading, provision.body[2])


def test_equal_length_role_witnesses_use_the_earliest_source_order() -> None:
    source = _structure(
        "제7조 (가상 지급 조건)\n회사는 보험수익자에게 보험금을 지급합니다.\n"
        "회사는 보험금을 지급하지 않습니다."
    )
    observed = observe_terms_body(1, source.nodes)
    assert observed.status == "SUPPORTED"
    provision = observed.provisions[0]
    assert role_witness(observed) == (provision.heading, provision.body[0])


def test_unsupported_or_ambiguous_pages_have_no_role_witness() -> None:
    assert role_witness(observe_terms_body(1, _structure("상품설명서\n" + _PAYMENT).nodes)) == ()
    node = _structure(_PAYMENT).nodes[0]
    assert role_witness(observe_terms_body(1, (node, node))) == ()


def _columns(left: str, right: str = "") -> tuple[StructureNode, ...]:
    blocks = _words(left.splitlines())
    offset = len(blocks)
    for position, block in enumerate(_words(right.splitlines())):
        blocks.append(
            dict(
                block,
                reading_order=offset + position,
                bbox=[
                    block["bbox"][0] + 310,
                    block["bbox"][1],
                    block["bbox"][2] + 310,
                    block["bbox"][3],
                ],
            )
        )
    return _build(_extraction(_page(1, blocks))).nodes


def test_independent_columns_keep_complete_provisions_without_cross_column_order_authority() -> (
    None
):
    nodes = _columns(_PAYMENT, _EXCLUSION)
    assert any("LINE_COLUMN_CONTEXT_UNRESOLVED" in node.issue_codes for node in nodes)
    observed = observe_terms_body(1, nodes)
    assert observed.status == "SUPPORTED"
    assert {provision.article_number for provision in observed.provisions} == {7, 8}
    assert "INDEPENDENT_BODY_REGIONS" in observed.reason_codes


def test_subject_and_predicate_in_different_columns_never_form_one_relationship() -> None:
    nodes = _columns("제7조 (가상 지급 조건)\n회사는 보험수익자에게", "보험금을 지급합니다.")
    assert observe_terms_body(1, nodes).status != "SUPPORTED"


@pytest.mark.parametrize("location", ["side", "footer", "table"])
def test_unrelated_unsupported_region_does_not_erase_a_proved_body(location: str) -> None:
    nodes = _columns(_PAYMENT)
    if location == "table":
        _, unrelated = _table_nodes()
        unrelated = replace(
            unrelated,
            node_id="synthetic-unresolved-table",
            text="Synthetic data",
            bbox=(10, 200, 250, 250),
            row_role="unresolved",
            cells=(
                StructureCell(
                    1, 0, "Synthetic data", (10, 200, 250, 250), "/synthetic/unresolved-cell"
                ),
            ),
            context_node_ids=(),
            issue_codes=("TABLE_CONTEXT_UNRESOLVED",),
        )
    else:
        unrelated = StructureNode(
            "synthetic-unrelated",
            "BLOCK",
            1,
            "native",
            100,
            "Synthetic side note",
            "/synthetic/unrelated",
            (400, 20, 550, 30) if location == "side" else None,
            issue_codes=("SOURCE_BBOX_UNAVAILABLE",)
            if location == "footer"
            else ("UNRELATED_UNIT_UNRESOLVED",),
        )
    observed = observe_terms_body(1, (*nodes, unrelated))
    assert observed.status == "SUPPORTED"
    assert len(observed.provisions) == 1
    assert "UNRESOLVED_REGIONS_EXCLUDED" in observed.reason_codes


def test_a_reference_passage_affects_its_column_without_erasing_an_independent_body() -> None:
    nodes = _columns("다음은 약관을 설명하기 위한 예시입니다.\n" + _EXCLUSION, _PAYMENT)
    observed = observe_terms_body(1, nodes)
    assert observed.status == "SUPPORTED"
    assert [provision.article_number for provision in observed.provisions] == [7]
    assert "REFERENCE_PASSAGE_EXCLUDED" in observed.reason_codes


def test_a_top_document_reference_title_still_governs_both_columns() -> None:
    nodes = _columns(_PAYMENT, _EXCLUSION)
    title = StructureNode(
        "synthetic-document-title",
        "BLOCK",
        1,
        "native",
        -1,
        "상품설명서",
        "/synthetic/title",
        (10, 1, 130, 10),
    )
    assert observe_terms_body(1, (title, *nodes)).status == "UNSUPPORTED"


def test_an_unusable_intervening_region_cannot_complete_a_split_operative_sentence() -> None:
    nodes = _columns("제7조 (가상 지급 조건)\n회사는 보험수익자에게\n보험금을 지급합니다.")
    barrier = StructureNode(
        "synthetic-barrier",
        "BLOCK",
        1,
        "native",
        100,
        "Synthetic unreadable region",
        "/synthetic/barrier",
        (10, 46, 250, 49),
        issue_codes=("SOURCE_LAYOUT_UNRESOLVED",),
    )
    assert observe_terms_body(1, (*nodes, barrier)).status != "SUPPORTED"


def test_participating_forged_lineage_only_invalidates_its_own_region() -> None:
    nodes = list(_columns(_PAYMENT, _EXCLUSION))
    line = next(node for node in nodes if node.kind == "TEXT_LINE" and node.bbox[0] < 100)
    nodes[nodes.index(line)] = replace(line, text=line.text + " forged")
    observed = observe_terms_body(1, nodes)
    assert observed.status == "SUPPORTED"
    assert [provision.article_number for provision in observed.provisions] == [8]
    assert "UNRESOLVED_REGIONS_EXCLUDED" in observed.reason_codes


@pytest.mark.parametrize("represented", [True, False])
def test_unschedulable_table_words_require_cell_containment_and_exact_text(
    represented: bool,
) -> None:
    _, body = _table_nodes()
    body = replace(body, context_node_ids=())
    duplicate = StructureNode(
        "synthetic-raw-table-word",
        "BLOCK",
        1,
        "native",
        0,
        "회사는" if represented else "상품설명서",
        "/synthetic/raw-word",
        (10, 40, 30, 50),
        schedulable=False,
    )
    observed = observe_terms_body(1, (duplicate, body))
    assert (observed.status == "SUPPORTED") is represented


def test_unknown_geometry_between_native_sentence_parts_is_a_flow_barrier() -> None:
    texts = (
        "제7조 (조건)",
        "회사는 보험수익자에게",
        "Synthetic unknown region",
        "보험금을 지급합니다.",
    )
    nodes = tuple(
        StructureNode(
            f"synthetic-gap-{index}",
            "BLOCK",
            1,
            "native",
            index,
            text,
            f"/synthetic/gap/{index}",
            None if index == 2 else (10, 10 + index * 15, 250, 20 + index * 15),
        )
        for index, text in enumerate(texts)
    )
    assert observe_terms_body(1, nodes).status != "SUPPORTED"


def test_region_output_uses_first_supported_heading_position_not_column_preamble() -> None:
    nodes = tuple(
        replace(node, bbox=(node.bbox[0], node.bbox[1] + 60, node.bbox[2], node.bbox[3] + 60))
        if node.bbox[0] < 300
        else node
        for node in _columns(_PAYMENT, _EXCLUSION)
    )
    preamble = StructureNode(
        "synthetic-preamble",
        "BLOCK",
        1,
        "native",
        -1,
        "Synthetic preamble",
        "/synthetic/preamble",
        (10, 1, 200, 10),
    )
    observed = observe_terms_body(1, (preamble, *nodes))
    assert observed.status == "SUPPORTED"
    assert [provision.article_number for provision in observed.provisions] == [8, 7]


def test_external_spanning_example_marker_preserves_only_the_preceding_complete_article() -> None:
    nodes = _columns(_PAYMENT + "\n" + _EXCLUSION)
    marker = StructureNode(
        "synthetic-spanning-example",
        "BLOCK",
        1,
        "native",
        100,
        "다음은 약관을 설명하기 위한 예시입니다.",
        "/synthetic/example",
        (0, 46, 600, 49),
    )
    observed = observe_terms_body(1, (*nodes, marker))
    assert observed.status == "SUPPORTED"
    assert [provision.article_number for provision in observed.provisions] == [7]
    assert "REFERENCE_PASSAGE_EXCLUDED" in observed.reason_codes


def test_a_retained_whole_table_box_uses_the_contained_single_cell_for_flow() -> None:
    header, body = _table_nodes()
    header = replace(header, bbox=(10, 10, 550, 700))
    body = replace(body, bbox=(10, 10, 550, 700))
    observed = observe_terms_body(1, (header, body))
    assert observed.status == "SUPPORTED"
    assert observed.table_contexts[0].header_node_ids == (header.node_id,)


def test_distant_same_column_text_cannot_supply_a_missing_predicate() -> None:
    nodes = _columns("제7조 (조건)\n회사는 보험수익자에게")
    predicate = StructureNode(
        "synthetic-distant-predicate",
        "BLOCK",
        1,
        "native",
        100,
        "보험금을 지급합니다.",
        "/synthetic/footer",
        (10, 700, 250, 720),
    )
    assert observe_terms_body(1, (*nodes, predicate)).status != "SUPPORTED"


def test_inconsistent_table_text_cannot_hide_an_unschedulable_reference_title() -> None:
    nodes = _columns(_PAYMENT)
    header, _ = _table_nodes()
    header = replace(
        header,
        text="Synthetic table",
        bbox=(10, 1, 200, 10),
        cells=(replace(header.cells[0], text="상품설명서", bbox=(10, 1, 200, 10)),),
    )
    raw = StructureNode(
        "synthetic-hidden-title",
        "BLOCK",
        1,
        "native",
        -1,
        "상품설명서",
        "/synthetic/raw-title",
        (10, 1, 130, 10),
        schedulable=False,
    )
    assert observe_terms_body(1, (raw, *nodes, header)).status != "SUPPORTED"


def test_strict_local_table_context_excludes_only_that_region() -> None:
    nodes = _columns(_PAYMENT)
    _, table = _table_nodes()
    table = replace(
        table,
        node_id="synthetic-external-context",
        bbox=(350, 20, 550, 120),
        cells=(replace(table.cells[0], bbox=(350, 20, 550, 120)),),
        context_node_ids=("synthetic-external-header",),
        continuation_of="synthetic-prior-table",
    )
    default = observe_terms_body(1, (*nodes, table))
    assert default.status == "SUPPORTED"
    assert len(default.provisions) == 2
    strict = observe_terms_body(1, (*nodes, table), require_local_contexts=True)
    assert strict.status == "SUPPORTED"
    assert len(strict.provisions) == 1
    assert strict.provisions[0].heading.node_id != table.node_id
    assert "UNRESOLVED_REGIONS_EXCLUDED" in strict.reason_codes
    assert "INDEPENDENT_BODY_REGIONS" not in strict.reason_codes
    assert strict.table_contexts == ()
