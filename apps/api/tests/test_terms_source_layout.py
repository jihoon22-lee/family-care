"""Synthetic original-source layout proofs keep scope and full coverage explicit."""

from copy import deepcopy

import pytest
from familycare_api.terms_knowledge.source_layout import observe_semantic_regions

from workers.analyzer.tests.test_document_metadata import _structure
from workers.analyzer.tests.test_document_structure import (
    _block,
    _build,
    _extraction,
    _page,
    _table,
)
from workers.analyzer.tests.test_document_text_lines import _words

_ARTICLE = "제7조 (합성 지급 조건)"
_NEXT = "제8조 (합성 제외 조건)"
_BODY = "입원 1일마다 가입금액을 지급하며 주1을 적용합니다."


def _observe(source, end=1):
    return observe_semantic_regions(source, component_page_start=1, component_page_end=end)


def _replay(source, layout):
    nodes = {node["node_id"]: node for node in source["nodes"]}
    for region in layout.regions:
        for span in region.spans:
            node = nodes[span.node_id]
            assert node["text"][span.start : span.end] == span.text
            assert span.page_number == node["page_number"]
            assert span.source_layer == node["source_layer"]
            assert tuple(node["bbox"]) == span.bbox


def test_complete_component_proves_sequential_cross_page_article_continuation() -> None:
    source = _structure(_ARTICLE + "\n" + _BODY, "지급일수는 10일을 한도로 합니다.").to_dict()
    original = deepcopy(source)
    layout = _observe(source, end=2)
    assert layout.complete and len(layout.regions) == 1
    region = layout.regions[0]
    assert region.kind == "article" and region.label == "제7조" and region.complete
    assert region.heading.text == _ARTICLE
    assert {span.page_number for span in region.body_spans} == {1, 2}
    assert layout.expected_region_ids == (region.region_id,)
    assert _observe(source, end=2) == layout and source == original
    assert _BODY not in repr(layout) and _BODY not in repr(region)
    _replay(source, layout)


@pytest.mark.parametrize("fault", ["missing_page", "missing_node", "no_manifest", "unresolved"])
def test_missing_page_or_unprocessed_manifest_never_proves_complete_coverage(fault) -> None:
    source = _structure(_ARTICLE + "\n" + _BODY, "합성 계속 조건입니다.").to_dict()
    if fault == "missing_page":
        source["pages"].pop()
        source["nodes"] = [n for n in source["nodes"] if n["page_number"] == 1]
    elif fault == "missing_node":
        source["nodes"].pop()
    elif fault == "no_manifest":
        source.pop("pages")
    else:
        source["unresolved"] = [
            {"node_id": None, "page_number": 2, "start": 0, "end": 0, "reason_code": "PAGE_MISSING"}
        ]
    layout = _observe(source, end=2)
    assert not layout.complete and layout.reason_codes
    assert not any(region.complete for region in layout.regions)


@pytest.mark.parametrize("fault", ["line_text", "hidden_raw", "split_lineage", "duplicate_owner"])
def test_native_derived_lines_require_complete_original_source_lineage(fault) -> None:
    source = _build(_extraction(_page(1, _words([_ARTICLE, _BODY])))).to_dict()
    assert _observe(source).complete
    line = next(n for n in source["nodes"] if n["kind"] == "TEXT_LINE" and n["text"] == _BODY)
    raw = next(
        n for n in source["nodes"] if n["node_id"] == line["source_spans"][0]["block_node_id"]
    )
    if fault == "line_text":
        line["text"] = line["text"].replace("1일", "9일")
    elif fault == "hidden_raw":
        raw["text"] += " 숨겨진 실제 예시 조건"
    elif fault == "split_lineage":
        raw["page_number"] = 2
    else:
        duplicate = dict(
            line, node_id="synthetic-duplicate-line", source_path="/synthetic/duplicate"
        )
        source["nodes"].append(duplicate)
        source["pages"][0]["node_ids"].append(duplicate["node_id"])
    layout = _observe(source)
    assert not layout.complete and not any(r.complete for r in layout.regions)


def test_local_example_needs_an_explicit_end_before_the_next_article() -> None:
    example = "예시\n회사는 가상 조건에서 보험금을 지급합니다."
    open_source = _structure(
        _ARTICLE + "\n" + _BODY + "\n" + example + "\n" + _NEXT + "\n합성 제외 조건입니다."
    ).to_dict()
    open_layout = _observe(open_source)
    assert not open_layout.complete
    assert not any(r.label == "제8조" and r.complete for r in open_layout.regions)
    closed_source = _structure(
        _ARTICLE
        + "\n"
        + _BODY
        + "\n"
        + example
        + "\n[예시 끝]\n"
        + _NEXT
        + "\n합성 제외 조건입니다."
    ).to_dict()
    closed = _observe(closed_source)
    assert closed.complete
    article = next(r for r in closed.regions if r.label == "제8조")
    assert article.complete and all("가상" not in span.text for span in article.spans)
    assert any("LOCAL_EXAMPLE_EXCLUDED" in r.reason_codes for r in closed.regions)
    _replay(closed_source, closed)


def test_numbered_footnote_requires_its_explicit_reference_in_the_enclosing_region() -> None:
    text = _ARTICLE + "\n" + _BODY + "\n주1: 최초 2일을 제외합니다."
    source = _structure(text).to_dict()
    layout = _observe(source)
    note = next(r for r in layout.regions if r.kind == "footnote")
    assert layout.complete and note.complete and note.label == "주1"
    assert note.heading.text == "주1"
    assert [span.text for span in note.body_spans] == ["최초 2일을 제외합니다."]
    _replay(source, layout)
    wrong = _observe(_structure(text.replace("주1을", "주2를")).to_dict())
    assert not wrong.complete
    assert not next(r for r in wrong.regions if r.kind == "footnote").complete
    unmarked = _observe(
        _structure(_ARTICLE + "\n" + _BODY + "\n※ 최초 2일을 제외합니다.").to_dict()
    )
    assert not unmarked.complete


def test_appendix_table_retains_header_and_rows_with_verified_original_context() -> None:
    source = _build(
        _extraction(
            _page(
                1,
                [_block(_ARTICLE + "\n별표 1을 따릅니다."), _block("별표 1 (합성 분류)", 1, y=40)],
                tables=[_table([["분류", "코드"], ["합성 분류", "class-a"]], header_rows=[0])],
            )
        )
    ).to_dict()
    layout = _observe(source)
    assert layout.complete
    appendix = next(r for r in layout.regions if r.kind == "appendix")
    assert appendix.label == "별표 1" and appendix.complete
    assert [s.text for s in appendix.body_spans] == ["분류\t코드", "합성 분류\tclass-a"]
    _replay(source, layout)
    data = next(n for n in source["nodes"] if n["kind"] == "TABLE_ROW" and n["row_role"] == "data")
    data["context_node_ids"] = [source["nodes"][0]["node_id"]]
    assert not _observe(source).complete


def test_unsupported_hierarchy_and_ambiguous_columns_remain_unresolved() -> None:
    unknown = _observe(
        _structure(_ARTICLE + "\n" + _BODY + "\n제2장 합성 다음 장\n" + _NEXT).to_dict()
    )
    assert not unknown.complete and any(r.kind == "unresolved" for r in unknown.regions)
    source = _build(
        _extraction(
            _page(
                1,
                [
                    {
                        "text": _ARTICLE + "\n" + _BODY,
                        "reading_order": 0,
                        "bbox": [10, 10, 200, 100],
                    },
                    {
                        "text": _NEXT + "\n합성 제외 조건",
                        "reading_order": 1,
                        "bbox": [300, 10, 500, 100],
                    },
                ],
            )
        )
    ).to_dict()
    assert not _observe(source).complete


def test_invalid_component_bounds_and_source_identity_fail_closed() -> None:
    source = _structure(_ARTICLE + "\n" + _BODY).to_dict()
    assert not observe_semantic_regions(
        source, component_page_start=True, component_page_end=1
    ).complete
    source["lineage"]["content_sha256"] = "synthetic-invalid-hash"
    assert not _observe(source).complete


def test_pure_terms_title_context_cannot_absorb_arbitrary_cover_body() -> None:
    title = _observe(_structure("보험약관\n" + _ARTICLE + "\n" + _BODY).to_dict())
    assert title.complete
    context = next(r for r in title.regions if "SEMANTIC_TERMS_TITLE_CONTEXT" in r.reason_codes)
    assert not context.body_spans
    mixed = _observe(
        _structure(
            "보험약관\n별도 범위가 불명확한 합성 설명문\n" + _ARTICLE + "\n" + _BODY
        ).to_dict()
    )
    assert not mixed.complete
    assert any("SEMANTIC_SOURCE_ORPHAN_TEXT" in r.reason_codes for r in mixed.regions)
