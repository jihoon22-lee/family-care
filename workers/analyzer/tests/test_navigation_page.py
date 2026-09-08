"""Navigation recognition requires the entire retained page, including raw sources."""

from dataclasses import replace

from familycare_worker.document_structure import StructureNode
from familycare_worker.navigation_page import is_navigation_page

from workers.analyzer.tests.test_document_structure import _build, _extraction, _page
from workers.analyzer.tests.test_document_text_lines import _words

_ENTRIES = "상품설명서 .... 9\nExample clauses … 12\n제1조 (계약의 목적) ...... 20"


def _plain(text: str) -> tuple[StructureNode, ...]:
    return (
        StructureNode(
            "synthetic-navigation-block",
            "BLOCK",
            1,
            "native",
            0,
            text,
            "/synthetic/blocks/0",
            (10, 10, 500, 200),
        ),
    )


def _native() -> tuple[StructureNode, ...]:
    return _build(_extraction(_page(1, _words(["목차", *_ENTRIES.splitlines()])))).nodes


def test_complete_multiline_navigation_accepts_document_names_as_entries() -> None:
    for heading in ("목 차", "【목차】", "차례", "Table of contents"):
        assert is_navigation_page(_plain(f"{heading}\n{_ENTRIES}\n2"))


def test_native_word_lines_are_verified_before_excluding_duplicate_blocks() -> None:
    nodes = _native()
    assert any(node.kind == "TEXT_LINE" for node in nodes)
    assert is_navigation_page(nodes)
    assert nodes == _native()


def test_heading_and_numbered_navigation_entry_are_both_required() -> None:
    for text in (
        "목차",
        _ENTRIES,
        "목차\n제1조 목적",
        "목차\n상품설명서 ....",
        "목차\n상품설명서 .... 0",
        "목차\n상품설명서 .... 501",
        "목차\n상품설명서 .... nine",
        "목차\n상품설명서 .... 9 more",
        "목차\n목차\n상품설명서 .... 9",
        "상품설명서 .... 9\n목차",
    ):
        assert not is_navigation_page(_plain(text))


def test_real_explanatory_or_operative_text_cannot_hide_among_entries() -> None:
    for extra in (
        "상품설명서",
        "다음은 약관을 설명하기 위한 예시입니다.",
        "제7조 (지급 조건)\n회사는 보험수익자에게 보험금을 지급합니다.",
        "회사는 보험금을 지급합니다 .... 9",
        "The insurer must pay the insurance benefit .... 9",
    ):
        assert not is_navigation_page(_plain(f"목차\n{_ENTRIES}\n{extra}"))


def test_a_line_cannot_hide_unrepresented_text_in_its_raw_block() -> None:
    nodes = list(_native())
    line = next(node for node in nodes if node.kind == "TEXT_LINE")
    block = next(node for node in nodes if node.node_id == line.source_spans[0].block_node_id)
    nodes[nodes.index(block)] = replace(block, text=block.text + "\n실제 예시 문장입니다.")
    assert not is_navigation_page(nodes)


def test_invalid_or_duplicate_native_source_addresses_fail_closed() -> None:
    original = _native()
    line = next(node for node in original if node.kind == "TEXT_LINE")
    for replacement in (
        replace(line, text=line.text + " forged"),
        replace(line, bbox=(0, 0, 1, 1)),
        replace(line, source_spans=()),
    ):
        assert not is_navigation_page(tuple(replacement if n is line else n for n in original))
    assert not is_navigation_page((*original, original[0]))
    assert not is_navigation_page(
        (*original, replace(line, node_id="synthetic-duplicate-line", source_path="/duplicate"))
    )
    assert not is_navigation_page(
        tuple(n for n in original if n.node_id != line.source_spans[0].block_node_id)
    )


def test_unlocated_mixed_page_or_ambiguous_flow_is_not_navigation() -> None:
    node = _plain(f"목차\n{_ENTRIES}")[0]
    for change in (
        {"bbox": None},
        {"reading_order": True},
        {"schedulable": False},
        {"issue_codes": ("LINE_COLUMN_CONTEXT_UNRESOLVED",)},
    ):
        assert not is_navigation_page((replace(node, **change),))
    second = replace(
        node,
        node_id="synthetic-second-block",
        source_path="/synthetic/blocks/1",
        text="상품설명서 .... 9",
        reading_order=1,
    )
    assert not is_navigation_page((node, replace(second, page_number=2)))
    assert not is_navigation_page(
        (node, replace(second, bbox=(10, 210, 500, 230), page_number=True))
    )
    assert not is_navigation_page((node, second))  # Overlapping independent blocks.
    assert not is_navigation_page(
        (node, replace(second, bbox=(10, 210, 500, 230), reading_order=0))
    )
    assert not is_navigation_page(
        (node, replace(second, bbox=(10, 210, 500, 230), source_path=node.source_path))
    )


def test_unsupported_tables_and_budgets_cannot_drop_page_content() -> None:
    node = _plain(f"목차\n{_ENTRIES}")[0]
    assert not is_navigation_page(())
    assert not is_navigation_page((replace(node, kind="TABLE_ROW"),))
    assert not is_navigation_page((replace(node, text="x" * 262145),))
    assert not is_navigation_page((replace(node, text="목차\n" + "상품 .... 9\n" * 4096),))
    assert not is_navigation_page((node,) * 4097)
