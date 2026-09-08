"""Recognize complete navigation pages without assigning document identity or authority.

The caller supplies every retained node from one immutable page. Unsupported
tables, ambiguous layout and unrepresented raw text prevent recognition.
"""

import re
import unicodedata
from collections.abc import Sequence

from familycare_worker.document_structure import StructureNode
from familycare_worker.terms_body import _box, _InvalidPage, _lineage

_HEADING = re.compile(r"^(?:목\s*차|차\s*례|table\s+of\s+contents)$", re.I)
_ENTRY = re.compile(r"^(?P<label>\S[^\n]{0,238}?)(?:\.{2,}|…+|·{2,})\s*(?P<page>[1-9][0-9]{0,2})$")
_NUMBER = re.compile(r"^-?\s*(?P<page>[1-9][0-9]{0,2})\s*-?$")
_SENTENCE_END = re.compile(r"[.!?。]$|(?:합니다|한다|습니다|말한다|이다)$")
_MODAL = re.compile(r"\b(?:shall|must)\b", re.I)


def is_navigation_page(nodes: Sequence[StructureNode]) -> bool:
    """Require an explicit contents heading and only bounded navigation entries."""
    try:
        return _is_navigation_page(nodes)
    except _InvalidPage, KeyError, TypeError, ValueError, AttributeError, IndexError, OverflowError:
        return False


def _is_navigation_page(nodes: Sequence[StructureNode]) -> bool:
    if (
        not isinstance(nodes, Sequence)
        or not 1 <= len(nodes) <= 4096
        or any(not isinstance(node, StructureNode) for node in nodes)
        or sum(len(node.text) for node in nodes) > 262144
    ):
        return False
    by_id = {node.node_id: node for node in nodes}
    addresses = {(node.kind, node.source_path) for node in nodes}
    pages = {node.page_number for node in nodes}
    layers = {node.source_layer for node in nodes}
    if (
        len(by_id) != len(nodes)
        or len(addresses) != len(nodes)
        or len(pages) != 1
        or any(
            type(node.page_number) is not int or not 1 <= node.page_number <= 500 for node in nodes
        )
        or len(layers) != 1
        or not layers <= {"native", "ocr"}
    ):
        return False
    represented: set[str] = set()
    for node in nodes:
        if (
            node.kind not in {"BLOCK", "TEXT_LINE"}
            or not isinstance(node.node_id, str)
            or not 1 <= len(node.node_id) <= 128
            or not isinstance(node.source_path, str)
            or not node.source_path
            or type(node.schedulable) is not bool
            or node.issue_codes
            or type(node.reading_order) is not int
            or node.reading_order < 0
        ):
            return False
        _box(node)
        if node.kind != "TEXT_LINE":
            continue
        if any(
            type(value) is not int
            for span in node.source_spans
            for value in (span.block_start, span.block_end, span.line_start, span.line_end)
        ):
            return False
        blocks = _lineage(node, by_id)
        if blocks & represented:
            return False
        for span in node.source_spans:
            block = by_id[span.block_node_id]
            # A valid line may cover only part of a raw block. Never discard the
            # rest of that block unless every omitted character is whitespace.
            if block.text[: span.block_start].strip() or block.text[span.block_end :].strip():
                return False
        represented.update(blocks)
    active = sorted(
        (node for node in nodes if node.node_id not in represented and node.text.strip()),
        key=lambda node: node.reading_order,
    )
    if any(not node.schedulable for node in active):
        return False
    for previous, current in zip(active, active[1:], strict=False):
        if current.reading_order <= previous.reading_order or _box(current)[1] < _box(previous)[3]:
            return False
    heading = False
    entries = 0
    lines = 0
    for node in active:
        for raw in node.text.splitlines():
            text = unicodedata.normalize("NFKC", raw.strip()).strip("[](){}【】<> ")
            if not text:
                continue
            lines += 1
            if lines > 4096:
                return False
            number = _NUMBER.fullmatch(text)
            if number and int(number["page"]) <= 500:
                continue
            if _HEADING.fullmatch(text):
                if heading:
                    return False
                heading = True
                continue
            entry = _ENTRY.fullmatch(text)
            if (
                not heading
                or entry is None
                or int(entry["page"]) > 500
                or _SENTENCE_END.search(entry["label"].strip())
                or _MODAL.search(entry["label"])
            ):
                return False
            entries += 1
    return heading and entries > 0
