"""Locate an enrollment's native name words without extraction-specific IDs or I/O.

This is a physical-source locator, not enrollment authority. Callers must first
validate household, contract association, and field grounding, and namespace the
returned key by that household/contract. Unsupported geometry has no fallback.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from itertools import pairwise
from typing import Any

_NAME_HEADERS = {"담보명", "특약명", "보장명", "ridername"}
type Box = tuple[float, float, float, float]
type Node = Mapping[str, Any]


def _normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _box(value: object) -> Box | None:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        return None
    if any(type(part) not in (int, float) or not math.isfinite(part) for part in value):
        return None
    x0, y0, x1, y1 = (float(part) for part in value)
    if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _inside(inner: Box, outer: Box) -> bool:
    return (
        outer[0] <= inner[0] < inner[2] <= outer[2] and outer[1] <= inner[1] < inner[3] <= outer[3]
    )


def _anchor(blocks: Sequence[Node], name: str, page: int) -> Box | None:
    """Whole native words/phrases only; never interpolate character positions."""
    if not blocks or len({block["node_id"] for block in blocks}) != len(blocks):
        return None
    positioned = []
    for block in blocks:
        box = _box(block.get("bbox"))
        if (
            block.get("kind") != "BLOCK"
            or block.get("source_layer") != "native"
            or block.get("page_number") != page
            or box is None
        ):
            return None
        positioned.append((box, block["text"]))
    positioned.sort(key=lambda item: item[0][0])
    if _normalized(" ".join(text for _, text in positioned)) != _normalized(name):
        return None
    boxes = [box for box, _ in positioned]
    for left, right in pairwise(boxes):
        height = min(left[3] - left[1], right[3] - right[1])
        if (
            right[0] < left[2]
            or right[0] - left[2] > 2 * height
            or min(left[3], right[3]) - max(left[1], right[1]) < height / 2
        ):
            return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _cells(node: Node) -> dict[int, Node] | None:
    cells = node.get("cells", ())
    if any(
        cell.get("row_span") not in (None, 1)
        or cell.get("column_span") not in (None, 1)
        or cell.get("row_index") != node.get("row_index")
        or type(cell.get("column_index")) is not int
        for cell in cells
    ):
        return None
    columns = {cell["column_index"]: cell for cell in cells}
    return columns if len(columns) == len(cells) else None


def _table_anchor(row: Node, nodes: Mapping[str, Node], name: str) -> Box | None:
    if row.get("row_role") != "data":
        return None
    headers = []
    for key in row.get("context_node_ids", ()):
        header = nodes.get(key)
        if header is None:
            return None
        if header.get("kind") == "TABLE_ROW" and header.get("row_role") == "header":
            cells = _cells(header)
            if cells is None or header.get("source_layer") != "native":
                return None
            headers.append(cells)
    columns = {
        column
        for header in headers
        for column, cell in header.items()
        if re.sub(r"[\s:：]", "", _normalized(cell["text"])) in _NAME_HEADERS
    }
    if len(columns) != 1:
        return None
    column = next(iter(columns))
    if any(
        column not in header
        or re.sub(r"[\s:：]", "", _normalized(header[column]["text"])) not in _NAME_HEADERS
        for header in headers
    ):
        return None
    cells = _cells(row)
    if cells is None or column not in cells:
        return None
    name_cell = cells[column]
    cell_box = _box(name_cell.get("bbox"))
    if cell_box is None or _normalized(name_cell["text"]) != _normalized(name):
        return None
    blocks = [
        node
        for node in nodes.values()
        if node.get("kind") == "BLOCK"
        and node.get("source_layer") == "native"
        and node.get("page_number") == row["page_number"]
        and (box := _box(node.get("bbox"))) is not None
        and _inside(box, cell_box)
    ]
    return _anchor(blocks, name, row["page_number"])


def _line_anchor(line: Node, nodes: Mapping[str, Node], name: str) -> Box | None:
    pattern = r"(?<!\w)" + r"\s+".join(re.escape(word) for word in name.split()) + r"(?!\w)"
    matches = list(re.finditer(pattern, line["text"], re.IGNORECASE))
    if len(matches) != 1:
        return None
    start, end = matches[0].span()
    spans = sorted(line.get("source_spans", ()), key=lambda item: item["line_start"])
    blocks = []
    cursor = start
    previous_end = 0
    for span in spans:
        low, high = span["line_start"], span["line_end"]
        block = nodes.get(span["block_node_id"])
        if (
            type(low) is not int
            or type(high) is not int
            or not previous_end <= low < high <= len(line["text"])
            or block is None
            or span["block_start"] != 0
            or span["block_end"] != len(block["text"])
            or line["text"][low:high] != block["text"]
        ):
            return None
        previous_end = high
        if high <= start or low >= end:
            continue
        if low < start or high > end or line["text"][cursor:low].strip():
            return None
        cursor = high
        blocks.append(block)
    if line["text"][cursor:end].strip():
        return None
    return _anchor(blocks, name, line["page_number"])


def physical_enrollment_locator(
    structure: Mapping[str, Any], original_name: str, source_refs: Sequence[Mapping[str, Any]]
) -> dict[str, Any] | None:
    """Return an exact native name anchor for selected original name-field refs.

    Extraction/node IDs, row numbers, labels, amounts and corrected display names
    are not identity inputs. Equivalent views must resolve to one physical span.
    OCR and uncertain/partial geometry deliberately require separate review.
    """
    try:
        if not isinstance(original_name, str) or not original_name.strip():
            return None
        digest = structure["lineage"]["content_sha256"]
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            return None
        nodes = {node["node_id"]: node for node in structure["nodes"]}
        if len(nodes) != len(structure["nodes"]):
            return None
        anchors: set[tuple[int, Box]] = set()
        raw_blocks: dict[str, Node] = {}
        for ref in source_refs:
            if not ref.get("primary"):
                continue
            node = nodes.get(ref["node_id"])
            if (
                node is None
                or ref.get("source_role") != "policy"
                or node.get("source_layer") != "native"
                or type(node.get("page_number")) is not int
                or node["page_number"] < 1
                or ref.get("page") != node["page_number"]
                or ref.get("start") != 0
                or ref.get("end") != len(node["text"])
            ):
                return None
            if node["kind"] == "BLOCK":
                raw_blocks[node["node_id"]] = node
                continue
            if node["kind"] == "TABLE_ROW":
                if node.get("row_role") == "header":
                    # A shared header can also be a primary range in the envelope.
                    # It supports the field but is not the enrolled name's position.
                    continue
                box = _table_anchor(node, nodes, original_name)
            elif node["kind"] == "TEXT_LINE":
                box = _line_anchor(node, nodes, original_name)
            else:
                return None
            if box is None:
                return None
            anchors.add((node["page_number"], box))
        if raw_blocks:
            blocks = list(raw_blocks.values())
            page = blocks[0]["page_number"]
            box = _anchor(blocks, original_name, page)
            if box is None:
                return None
            anchors.add((page, box))
        if len(anchors) != 1:
            return None
        page, box = next(iter(anchors))
        return {
            "schema_version": "enrollment-physical-v1",
            "content_sha256": digest,
            "physical_page": page,
            "name_bbox": list(box),
        }
    except KeyError, TypeError, ValueError, OverflowError:
        return None
