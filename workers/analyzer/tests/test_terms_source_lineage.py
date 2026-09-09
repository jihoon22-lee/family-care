"""Synthetic native lines use the constructor's first-word geometry contract."""

from dataclasses import replace

import pytest
from familycare_worker.document_structure import (
    BBox,
    DocumentStructure,
    SourceTextSpan,
    StructureNode,
)
from familycare_worker.terms_body import _InvalidPage, _lineage, observe_terms_body

from workers.analyzer.tests.test_document_structure import _build, _extraction, _page
from workers.analyzer.tests.test_document_text_lines import _words

_VALID_BOXES = (
    ((10, 20, 20, 30), (23, 18, 33, 28), (36, 22, 46, 32)),
    ((10, 20, 20, 30), (23, 21, 33, 29), (47, 20, 57, 30)),
)


def _native_source(boxes: tuple[BBox, ...]) -> DocumentStructure:
    blocks = [
        {"text": word, "reading_order": order, "bbox": box}
        for order, (word, box) in enumerate(zip(("Sample", "native", "words"), boxes, strict=True))
    ]
    return _build(_extraction(_page(1, blocks)))


def _whole_word_view(source: DocumentStructure) -> tuple[StructureNode, dict[str, StructureNode]]:
    blocks = [node for node in source.nodes if node.kind == "BLOCK"]
    template = next(node for node in source.nodes if node.kind == "TEXT_LINE")
    spans = []
    offset = 0
    for block in blocks:
        spans.append(
            SourceTextSpan(block.node_id, 0, len(block.text), offset, offset + len(block.text))
        )
        offset += len(block.text) + 1
    boxes = [block.bbox for block in blocks if block.bbox is not None]
    line = replace(
        template,
        text=" ".join(block.text for block in blocks),
        reading_order=blocks[0].reading_order,
        source_spans=tuple(spans),
        bbox=(
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        ),
    )
    return line, {block.node_id: block for block in blocks}


@pytest.mark.parametrize("boxes", _VALID_BOXES, ids=("alternating-baselines", "short-middle-word"))
def test_constructor_line_passes_first_word_lineage_validation(boxes: tuple[BBox, ...]) -> None:
    source = _native_source(boxes)
    lines = [node for node in source.nodes if node.kind == "TEXT_LINE"]
    assert len(lines) == 1 and len(lines[0].source_spans) == 3
    by_id = {node.node_id: node for node in source.nodes}
    assert _lineage(lines[0], by_id) == {span.block_node_id for span in lines[0].source_spans}


@pytest.mark.parametrize("change", ("alternating-baselines", "short-middle-word"))
def test_constructor_geometry_keeps_an_operative_provision(change: str) -> None:
    blocks = _words(["제7조 (가상 지급 조건)", "회사는 보험수익자에게 보험금을 지급합니다."])
    body = [block for block in blocks if block["bbox"][1] == 35]
    if change == "alternating-baselines":
        body[1]["bbox"][1] -= 2
        body[1]["bbox"][3] -= 2
        body[2]["bbox"][1] += 2
        body[2]["bbox"][3] += 2
    else:
        body[1]["bbox"][1] += 1
        body[1]["bbox"][3] -= 1
        for block in body[2:]:
            block["bbox"][0] += 11
            block["bbox"][2] += 11
    source = _build(_extraction(_page(1, blocks)))
    observed = observe_terms_body(1, source.nodes)
    assert observed.status == "SUPPORTED"
    assert len(observed.provisions) == 1
    assert observed.provisions[0].semantic_kinds == ("PAYMENT",)
    by_id = {node.node_id: node for node in source.nodes}
    for span in (observed.provisions[0].heading, *observed.provisions[0].body):
        assert by_id[span.node_id].kind == "TEXT_LINE"
        assert by_id[span.node_id].text[span.start : span.end] == span.text


@pytest.mark.parametrize(
    "boxes",
    (
        ((10, 20, 20, 30), (23, 22, 33, 32), (36, 24, 46, 34)),
        ((10, 21, 20, 29), (23, 20, 33, 30), (47, 20, 57, 30)),
        ((10, 20, 20, 30), (19, 20, 29, 30), (32, 20, 42, 30)),
    ),
    ids=("neighbor-only-drift", "gap-exceeds-first-word-limit", "overlapping-words"),
)
def test_lineage_rejects_geometry_the_constructor_does_not_join(boxes: tuple[BBox, ...]) -> None:
    source = _native_source(boxes)
    assert not any(
        node.kind == "TEXT_LINE" and len(node.source_spans) == 3 for node in source.nodes
    )
    line, by_id = _whole_word_view(source)
    with pytest.raises(_InvalidPage, match="SOURCE_LINEAGE_INVALID"):
        _lineage(line, by_id)


@pytest.mark.parametrize("change", ("span-offset", "word-order", "box-union", "source-layer"))
def test_first_word_geometry_does_not_weaken_other_lineage_checks(change: str) -> None:
    source = _native_source(_VALID_BOXES[0])
    line, by_id = _whole_word_view(source)
    second = line.source_spans[1]
    block = by_id[second.block_node_id]
    if change == "span-offset":
        line = replace(
            line,
            source_spans=(
                line.source_spans[0],
                replace(second, block_start=1),
                line.source_spans[2],
            ),
        )
    elif change == "word-order":
        by_id[block.node_id] = replace(block, reading_order=block.reading_order + 1)
    elif change == "source-layer":
        by_id[block.node_id] = replace(block, source_layer="ocr")
    else:
        assert line.bbox is not None
        line = replace(line, bbox=(*line.bbox[:2], line.bbox[2] + 1, line.bbox[3]))
    with pytest.raises(_InvalidPage, match="SOURCE_LINEAGE_INVALID"):
        _lineage(line, by_id)
