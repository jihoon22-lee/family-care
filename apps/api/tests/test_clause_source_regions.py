"""Synthetic whole-clause locations keep headings, bodies and boundaries together."""

from copy import deepcopy
from dataclasses import asdict
from typing import Any

import pytest
from familycare_api.clauses.source_regions import (
    observe_clause_source_regions,
    resolve_clause_source_region,
)

from workers.analyzer.tests.test_document_metadata import _structure
from workers.analyzer.tests.test_document_structure import _build, _extraction, _page
from workers.analyzer.tests.test_document_text_lines import _words
from workers.analyzer.tests.test_terms_body import _table_nodes

_HEADING_7 = "제7조 (합성 지급 조건)"
_HEADING_8 = "제8조 (합성 제외 조건)"
_BODY_7 = "회사는 합성 조건에 따라 보험금을 지급합니다."
_BODY_8 = "회사는 합성 제외 조건에 해당하면 보험금을 지급하지 않습니다."


def _source(*pages: str) -> dict[str, Any]:
    return _structure(*pages).to_dict()


def _resolve(source: dict[str, Any], label: str = "제7조", **kwargs: Any) -> Any:
    observation = observe_clause_source_regions(source, 1, **kwargs)
    return resolve_clause_source_region(observation, label=label)


def _assert_replay(source: dict[str, Any], region: Any) -> None:
    nodes = {node["node_id"]: node for node in source["nodes"]}
    for span in (region.heading, *region.body):
        node = nodes[span.node_id]
        assert span.page_number == node["page_number"] == 1
        assert node["text"][span.start : span.end] == span.text
        assert span.source_layer == node["source_layer"]
    assert region.content_sha256 == source["lineage"]["content_sha256"]
    assert len(region.source_sha256) == 64


def test_adjacent_articles_retain_distinct_complete_bodies_and_raw_offsets() -> None:
    source = _source(f"{_HEADING_7}\n{_BODY_7}\n{_HEADING_8}\n{_BODY_8}")
    original = deepcopy(source)
    observed = observe_clause_source_regions(source, 1, component_page_end=1)
    seven = resolve_clause_source_region(observed, label="제7조")
    eight = resolve_clause_source_region(observed, label="제8조")
    assert seven.status == eight.status == "MATCH"
    assert seven.region.complete and eight.region.complete
    assert seven.region.heading.text == _HEADING_7
    assert seven.region.body_text == _BODY_7
    assert eight.region.body_text == _BODY_8
    assert seven.region.source_sha256 != eight.region.source_sha256
    assert _HEADING_8 not in seven.region.body_text
    _assert_replay(source, seven.region)
    _assert_replay(source, eight.region)
    assert source == original
    assert _BODY_7 not in repr(seven)
    assert _BODY_7 not in repr(seven.region)


def _columns(*, identical_body: bool = False) -> dict[str, Any]:
    right_body = _BODY_7 if identical_body else "회사는 다른 합성 조건에 따라 보험금을 지급합니다."
    blocks = [
        {
            "text": f"{_HEADING_7}\n{body}\n{_HEADING_8}\n{_BODY_8}",
            "reading_order": order,
            "bbox": [left, 10, left + 220, 120],
        }
        for order, (left, body) in enumerate(((10, _BODY_7), (310, right_body)))
    ]
    return _build(_extraction(_page(1, blocks))).to_dict()


def test_same_label_in_two_columns_requires_one_exact_physical_or_body_match() -> None:
    source = _columns()
    observed = observe_clause_source_regions(source, 1)
    candidates = [r for r in observed.regions if r.label == "제7조"]
    assert len(candidates) == 2
    assert candidates[0].heading.bbox != candidates[1].heading.bbox
    ambiguous = resolve_clause_source_region(observed, label="제7조")
    assert ambiguous.status == "UNKNOWN" and ambiguous.region is None
    assert "CLAUSE_SOURCE_AMBIGUOUS" in ambiguous.reason_codes
    exact = resolve_clause_source_region(observed, label="제7조", body_text=_BODY_7)
    assert exact.status == "MATCH" and exact.region.body_text == _BODY_7
    located = resolve_clause_source_region(
        observed, label="제7조", heading_bbox=exact.region.heading.bbox
    )
    assert located.region == exact.region
    mismatch = resolve_clause_source_region(
        observed,
        label="제7조",
        heading_bbox=exact.region.heading.bbox,
        body_text="합성 본문 불일치",
    )
    assert mismatch.status == "UNKNOWN" and mismatch.region is None


def test_identical_label_and_body_in_two_columns_do_not_collapse_to_one_identity() -> None:
    observed = observe_clause_source_regions(_columns(identical_body=True), 1)
    matching = [r for r in observed.regions if r.label == "제7조"]
    assert len({r.source_sha256 for r in matching}) == 2
    result = resolve_clause_source_region(observed, label="제7조", body_text=_BODY_7)
    assert result.status == "UNKNOWN" and result.region is None
    result = resolve_clause_source_region(observed, label="제7조", heading_bbox=(0, 0, 600, 800))
    assert result.status == "UNKNOWN" and result.region is None


def test_repeated_heading_in_one_flow_cannot_certify_a_truncated_continuation() -> None:
    source = _source(
        f"{_HEADING_7}\n{_BODY_7}\n{_HEADING_7}\n합성 조항의 계속되는 조건입니다.\n{_HEADING_8}"
    )
    observed = observe_clause_source_regions(source, 1, component_page_end=1)
    result = resolve_clause_source_region(observed, label="제7조", body_text=_BODY_7)
    assert result.status == "UNKNOWN" and result.region is None


@pytest.mark.parametrize("prefix", ["목차", "Table of contents", "예시", "Example clauses"])
def test_reference_regions_cannot_supply_a_clause_location(prefix: str) -> None:
    source = _source(f"{prefix}\n{_HEADING_7}\n{_BODY_7}\n{_HEADING_8}\n{_BODY_8}")
    result = _resolve(source, component_page_end=1)
    assert result.status == "UNKNOWN" and result.region is None


def test_example_after_a_provision_is_not_a_new_clause_or_a_proven_end() -> None:
    source = _source(f"{_HEADING_7}\n{_BODY_7}\n예시\n{_HEADING_8}\n{_BODY_8}")
    observed = observe_clause_source_regions(source, 1, component_page_end=1)
    seven = resolve_clause_source_region(observed, label="제7조")
    eight = resolve_clause_source_region(observed, label="제8조")
    assert seven.status == "UNKNOWN" and eight.status == "UNKNOWN"
    assert not any(r.complete for r in observed.regions)
    assert all("예시" not in r.body_text for r in observed.regions)


@pytest.mark.parametrize(
    "cross_reference",
    [
        "회사는 제8조 및 별표 1을 참조하여 보험금을 지급합니다.",
        "제8조 및 별표 1을 참조합니다.",
        "제8조 및 별표 1 참조",
        "Article 8 and Appendix A apply to this synthetic benefit.",
    ],
)
def test_normative_cross_references_stay_in_the_same_body(cross_reference: str) -> None:
    source = _source(f"{_HEADING_7}\n{_BODY_7}\n{cross_reference}\n{_HEADING_8}\n{_BODY_8}")
    result = _resolve(source)
    assert result.status == "MATCH"
    assert result.region.body_text == f"{_BODY_7}\n{cross_reference}"
    assert [span.text for span in result.region.body] == [_BODY_7, cross_reference]


@pytest.mark.parametrize("unsupported_heading", ["제8조 합성 제외 조건", "Article 8 Exclusions"])
def test_unsupported_heading_is_an_unresolved_boundary_not_the_previous_body(
    unsupported_heading: str,
) -> None:
    source = _source(
        f"{_HEADING_7}\n{_BODY_7}\n{unsupported_heading}\n{_BODY_8}\n제9조 (합성 후속 조건)"
    )
    observed = observe_clause_source_regions(source, 1, component_page_end=1)
    result = resolve_clause_source_region(observed, label="제7조")
    assert result.status == "UNKNOWN" and result.region is None
    assert "CLAUSE_SOURCE_BOUNDARY_UNRESOLVED" in result.reason_codes
    seven = next(region for region in observed.regions if region.label == "제7조")
    assert not seven.complete
    assert unsupported_heading not in seven.body_text and _BODY_8 not in seven.body_text


def test_raw_body_punctuation_is_preserved_and_not_a_search_normalization_identity() -> None:
    first = f"{_HEADING_7}\n합성 산식 입력은 1.5이며 제8조를 참조합니다.\n{_HEADING_8}\n{_BODY_8}"
    changed = first.replace("1.5", "1,5")
    result = _resolve(_source(first))
    other = _resolve(_source(changed))
    assert result.status == other.status == "MATCH"
    assert "1.5" in result.region.body_text
    assert result.region.source_sha256 != other.region.source_sha256


@pytest.mark.parametrize("mutation", ["text", "missing_block", "geometry"])
def test_invalid_derived_lineage_never_supplies_a_complete_clause(mutation: str) -> None:
    source = _build(
        _extraction(_page(1, _words([_HEADING_7, _BODY_7, _HEADING_8, _BODY_8])))
    ).to_dict()
    good = _resolve(source)
    assert good.status == "MATCH"
    line = next(node for node in source["nodes"] if node["kind"] == "TEXT_LINE")
    if mutation == "text":
        line["text"] += " forged"
    elif mutation == "geometry":
        line["bbox"] = [0, 0, 1, 1]
    else:
        identifier = line["source_spans"][0]["block_node_id"]
        source["nodes"] = [node for node in source["nodes"] if node["node_id"] != identifier]
    result = _resolve(source, component_page_end=1)
    assert result.status == "UNKNOWN" and result.region is None


def test_all_source_lines_are_retained_beyond_a_representative_payment_witness() -> None:
    body = [
        "첫 번째 합성 조건입니다.",
        _BODY_7,
        "이후의 추가 합성 제한 조건도 적용합니다.",
        "제8조 및 별표의 합성 조건을 함께 참조합니다.",
    ]
    source = _build(_extraction(_page(1, _words([_HEADING_7, *body, _HEADING_8])))).to_dict()
    result = _resolve(source)
    assert result.status == "MATCH"
    assert result.region.body_text == "\n".join(body)
    assert len(result.region.body) == len(body)
    assert all(span.source_layer == "native" for span in result.region.body)
    _assert_replay(source, result.region)


def test_single_cell_table_body_preserves_all_rows_and_original_offsets() -> None:
    header, data = [asdict(node) for node in _table_nodes()]
    data["text"] = f"{_HEADING_7}\n{_BODY_7}\n추가 합성 제한입니다.\n{_HEADING_8}\n{_BODY_8}"
    data["cells"][0]["text"] = data["text"]
    source = _source("")
    source["nodes"] = [header, data]
    source["pages"][0]["node_ids"] = [header["node_id"], data["node_id"]]
    result = _resolve(source)
    assert result.status == "MATCH"
    assert result.region.body_text == f"{_BODY_7}\n추가 합성 제한입니다."
    assert all(span.node_id == data["node_id"] for span in result.region.body)
    _assert_replay(source, result.region)


def test_contiguous_single_cell_table_rows_remain_one_complete_body() -> None:
    header, first = [asdict(node) for node in _table_nodes()]
    first["text"] = f"{_HEADING_7}\n{_BODY_7}"
    first["cells"][0]["text"] = first["text"]
    extra, closing = deepcopy(first), deepcopy(first)
    for node, identifier, number, text, box in (
        (
            extra,
            "synthetic-additional-row",
            2,
            "추가 행의 합성 제한 조건입니다.",
            [10, 110, 500, 135],
        ),
        (closing, "synthetic-closing-row", 3, f"{_HEADING_8}\n{_BODY_8}", [10, 145, 500, 180]),
    ):
        node.update(node_id=identifier, reading_order=number, row_index=number, text=text, bbox=box)
        node["cells"][0].update(row_index=number, text=text, bbox=box)
    source = _source("")
    source["nodes"] = [header, first, extra, closing]
    source["pages"][0]["node_ids"] = [node["node_id"] for node in source["nodes"]]
    result = _resolve(source)
    assert result.status == "MATCH"
    assert result.region.body_text == f"{_BODY_7}\n{extra['text']}"
    assert [span.node_id for span in result.region.body] == [first["node_id"], extra["node_id"]]
    _assert_replay(source, result.region)


def test_table_header_inside_a_clause_is_not_discarded_from_its_body() -> None:
    header, data = [asdict(node) for node in _table_nodes()]
    header["text"] = "합성 지급 제한 조건"
    header["cells"][0]["text"] = header["text"]
    data["text"] = _BODY_7
    data["cells"][0]["text"] = data["text"]
    start, closing = deepcopy(header), deepcopy(header)
    for node, identifier, text, order, box in (
        (start, "synthetic-start", _HEADING_7, 0, [10, 0, 500, 8]),
        (closing, "synthetic-end", _HEADING_8, 3, [10, 110, 500, 120]),
    ):
        node.update(
            node_id=identifier,
            kind="BLOCK",
            text=text,
            reading_order=order,
            bbox=box,
            table_id=None,
            row_role=None,
            row_index=None,
            cells=[],
            context_node_ids=[],
        )
    header["reading_order"], data["reading_order"] = 1, 2
    source = _source("")
    source["nodes"] = [start, header, data, closing]
    source["pages"][0]["node_ids"] = [node["node_id"] for node in source["nodes"]]
    result = _resolve(source)
    assert result.status == "MATCH"
    assert result.region.body_text == f"{header['text']}\n{_BODY_7}"
    _assert_replay(source, result.region)


def test_table_header_before_the_clause_is_retained_as_source_context() -> None:
    header, data = [asdict(node) for node in _table_nodes()]
    data["text"] = f"{_HEADING_7}\n{_BODY_7}\n{_HEADING_8}\n{_BODY_8}"
    data["cells"][0]["text"] = data["text"]
    source = _source("")
    source["nodes"] = [header, data]
    source["pages"][0]["node_ids"] = [header["node_id"], data["node_id"]]
    result = _resolve(source)
    assert result.status == "MATCH"
    assert len(result.region.table_context) == 1
    context = result.region.table_context[0]
    assert context.node_id == header["node_id"] and context.text == header["text"]


@pytest.mark.parametrize("projection_only", [False, True])
def test_last_observed_page_line_is_not_assumed_to_end_a_clause(projection_only: bool) -> None:
    source = _source(f"{_HEADING_7}\n{_BODY_7}", "다음 페이지에 계속되는 합성 조건입니다.")
    if projection_only:
        source["pages"] = [p for p in source["pages"] if p["page_number"] == 1]
        source["nodes"] = [node for node in source["nodes"] if node["page_number"] == 1]
    result = _resolve(source)
    assert result.status == "UNKNOWN" and result.region is None
    assert "CLAUSE_SOURCE_BOUNDARY_UNRESOLVED" in result.reason_codes
    result = _resolve(source, component_page_end=2)
    assert result.status == "UNKNOWN" and result.region is None


def test_repository_projection_cannot_certify_a_complete_body_without_page_manifest() -> None:
    source = _source(f"{_HEADING_7}\n{_BODY_7}\n{_HEADING_8}\n{_BODY_8}")
    projection = {name: source[name] for name in ("lineage", "nodes")}
    observed = observe_clause_source_regions(projection, 1, component_page_end=1)
    seven = resolve_clause_source_region(observed, label="제7조")
    eight = resolve_clause_source_region(observed, label="제8조")
    assert seven.status == "UNKNOWN" and seven.region is None
    assert eight.status == "UNKNOWN" and eight.region is None
    assert "CLAUSE_SOURCE_PAGE_MANIFEST_UNVERIFIED" in seven.reason_codes
    assert "CLAUSE_SOURCE_PAGE_MANIFEST_UNVERIFIED" in eight.reason_codes
    assert observed.regions and not any(region.complete for region in observed.regions)


def test_caller_verified_component_end_can_close_an_intact_last_clause() -> None:
    source = _source(f"{_HEADING_7}\n{_BODY_7}", "다른 합성 문서의 첫 페이지입니다.")
    assert _resolve(source).status == "UNKNOWN"
    result = _resolve(source, component_page_end=1)
    assert result.status == "MATCH" and result.region.complete
    assert result.region.body_text == _BODY_7


def test_component_end_does_not_hide_an_unprocessed_page_range() -> None:
    source = _source(f"{_HEADING_7}\n{_BODY_7}")
    source["unresolved"] = [
        {"node_id": None, "page_number": 1, "start": 0, "end": 0, "reason_code": "PAGE_MISSING"}
    ]
    result = _resolve(source, component_page_end=1)
    assert result.status == "UNKNOWN" and result.region is None


def test_missing_page_node_cannot_be_disguised_as_a_complete_component_end() -> None:
    source = _source(f"{_HEADING_7}\n{_BODY_7}")
    source["pages"][0]["node_ids"].append("synthetic-unobserved-tail")
    result = _resolve(source, component_page_end=1)
    assert result.status == "UNKNOWN" and result.region is None
