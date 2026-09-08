"""A source-proven reading guide exempts only its legend notice from persistent context."""

from copy import deepcopy

from familycare_api.insurance_documents.terms_body_validation import reference_context_present

from workers.analyzer.tests.test_document_metadata import _structure
from workers.analyzer.tests.test_document_structure import _build, _extraction, _page
from workers.analyzer.tests.test_document_text_lines import _words

_HEADER = "약관 이용 가이드"
_NOTICE = "예시 약관 이해를 돕는 참고 설명이 있습니다."


def _plain(text: str):
    return _structure(text).to_dict()["nodes"]


def _native():
    return _build(_extraction(_page(1, _words([_HEADER, _NOTICE])))).to_dict()["nodes"]


def _restricted(nodes):
    return reference_context_present(nodes, persistent_only=True, reading_guides=True)


def test_exact_guide_heading_exempts_only_its_following_legend_notice() -> None:
    for header in (_HEADER, "【보험약관 읽는 방법】", "약관 이해 길잡이"):
        nodes = _plain(header + "\n" + _NOTICE)
        assert not _restricted(nodes)
        assert reference_context_present(nodes, persistent_only=True)
        assert reference_context_present(nodes, persistent_only=True, navigation_instructions=True)
        assert reference_context_present(nodes, reading_guides=True)


def test_separate_source_nodes_require_both_source_order_and_vertical_order() -> None:
    header = _plain(_HEADER)[0]
    header.update(bbox=[10, 10, 300, 30])
    notice = dict(
        header,
        node_id="synthetic-guide-notice",
        source_path="/synthetic/notice",
        text=_NOTICE,
        bbox=[10, 40, 400, 60],
        reading_order=1,
    )
    assert not _restricted([header, notice])
    for change in (
        {"reading_order": 0},
        {"bbox": [10, 5, 400, 25]},
        {"page_number": 2},
        {"source_layer": "ocr"},
    ):
        assert _restricted([header, dict(notice, **change)])


def test_missing_reversed_or_nonexact_guide_does_not_hide_example_context() -> None:
    for text in (
        _NOTICE,
        _NOTICE + "\n" + _HEADER,
        "참고할 약관 이용 가이드\n" + _NOTICE,
        _HEADER + "\n예시 다음 가상 계약자의 경우를 설명합니다.",
        _HEADER + "\n예시 약관을 인용하여 이해를 돕는 참고 설명이 있습니다.",
        _HEADER + "\n예시 약관 이해를 돕는 참고 설명",
        _HEADER + "\n예시 " + "합성 " * 100 + "약관 이해를 돕는 참고 설명이 있습니다.",
    ):
        assert _restricted(_plain(text))


def test_another_reference_line_remains_restricted_before_or_after_the_notice() -> None:
    for other in ("상품설명서(요약)", "다음은 약관을 설명하기 위한 예시입니다."):
        assert _restricted(_plain(other + "\n" + _HEADER + "\n" + _NOTICE))
        assert _restricted(_plain(_HEADER + "\n" + _NOTICE + "\n" + other))


def test_native_notice_uses_complete_source_lines_without_mutating_raw_words() -> None:
    nodes = _native()
    previous = deepcopy(nodes)
    assert any(node["kind"] == "TEXT_LINE" for node in nodes)
    assert not _restricted(nodes)
    assert nodes == previous
    assert reference_context_present(nodes, persistent_only=True)


def test_missing_hidden_or_multiply_claimed_native_sources_are_not_exempted() -> None:
    original = _native()
    line = next(n for n in original if n["kind"] == "TEXT_LINE" and n["text"] == _NOTICE)
    raw_id = line["source_spans"][0]["block_node_id"]
    for corruption in ("missing", "hidden", "duplicate_id", "duplicate_owner", "duplicate_path"):
        nodes = deepcopy(original)
        if corruption == "missing":
            nodes = [n for n in nodes if n["node_id"] != raw_id]
        elif corruption == "hidden":
            next(n for n in nodes if n["node_id"] == raw_id)["text"] += " 실제 예시 문장"
        else:
            duplicate = deepcopy(line)
            if corruption != "duplicate_id":
                duplicate["node_id"] = "synthetic-duplicate-view"
            if corruption == "duplicate_owner":
                duplicate["source_path"] = "/synthetic/duplicate-view"
            nodes.append(duplicate)
        assert _restricted(nodes)


def test_forged_header_or_multiline_notice_view_cannot_hide_raw_context() -> None:
    for target, change in (
        (_HEADER, {"text": "보험약관 이용 가이드"}),
        (_NOTICE, {"text": _NOTICE + "\n상품설명서"}),
        (_NOTICE, {"bbox": [0, 0, 1, 1]}),
        (_NOTICE, {"page_number": True}),
    ):
        nodes = _native()
        next(n for n in nodes if n["kind"] == "TEXT_LINE" and n["text"] == target).update(change)
        assert _restricted(nodes)
    nodes = _native()
    line = next(n for n in nodes if n["kind"] == "TEXT_LINE" and n["text"] == _NOTICE)
    line["source_spans"][0]["block_start"] = False
    assert _restricted(nodes)


def test_unproven_plain_geometry_and_source_budgets_cannot_exempt_notice() -> None:
    for change in (
        {"bbox": None},
        {"schedulable": False},
        {"issue_codes": ["LINE_COLUMN_CONTEXT_UNRESOLVED"]},
    ):
        nodes = _plain(_HEADER + "\n" + _NOTICE)
        nodes[0].update(change)
        assert _restricted(nodes)
    node = _plain(_HEADER + "\n" + _NOTICE)[0]
    assert _restricted([node] * 4097)
    unrelated = dict(
        node,
        node_id="synthetic-unrelated-table",
        source_path="/synthetic/unrelated-table",
        kind="TABLE_ROW",
        text="별도 합성 표",
        schedulable=False,
    )
    assert not _restricted([node, unrelated])
    assert _restricted([node, dict(unrelated, text="상품설명서")])


def test_default_reference_inspection_keeps_text_only_callers_compatible() -> None:
    assert reference_context_present([{"text": "예시 조항"}])
    assert reference_context_present([{"text": "상품설명서"}], persistent_only=True)
