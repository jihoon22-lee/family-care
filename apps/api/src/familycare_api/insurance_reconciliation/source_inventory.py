"""Check name-location uniqueness against the complete retained source page.

This checks source inventory, not enrollment or contract authority. It includes
unpublished nodes and never interprets private package line numbers. Geometry
proof remains owned by physical_enrollment_locator; uncertain name occurrences
cannot be discarded merely because they have no publication.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from itertools import pairwise
from typing import Any

from familycare_api.policies.enrollment_locator import (
    _NAME_HEADERS,
    _cells,
    _inside,
    physical_enrollment_locator,
)

type Node = Mapping[str, Any]
type Box = tuple[float, float, float, float]


def _normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _box(value: object) -> Box | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(type(part) not in (int, float) or not math.isfinite(part) for part in value):
        return None
    x0, y0, x1, y1 = (float(part) for part in value)
    return (x0, y0, x1, y1) if 0 <= x0 < x1 and 0 <= y0 < y1 else None


def _ref(node: Node) -> dict[str, Any]:
    # The locator's policy-role parameter requests a geometric proof only; this
    # temporary ref is never published as evidence or enrollment authority.
    return {
        "node_id": node["node_id"],
        "page": node["page_number"],
        "start": 0,
        "end": len(node["text"]),
        "primary": True,
        "source_role": "policy",
    }


def _raw_orders(blocks: Sequence[Node]) -> Iterator[Sequence[Node]]:
    """Inspect logical order and geometric rows without assuming one parser order."""
    logical = sorted(
        enumerate(blocks), key=lambda item: (item[1].get("reading_order", item[0]), item[0])
    )
    yield [node for _, node in logical]
    positioned = [(_box(node.get("bbox")), node) for node in blocks]
    valid = [(box, node) for box, node in positioned if box is not None]
    valid.sort(key=lambda item: (item[0][1], item[0][0]))
    rows: list[tuple[Box, list[tuple[Box, Node]]]] = []
    for box, node in valid:
        compatible = [
            row
            for anchor, row in rows
            if min(box[3], anchor[3]) - max(box[1], anchor[1])
            >= min(box[3] - box[1], anchor[3] - anchor[1]) / 2
        ]
        if compatible:
            for row in compatible:
                row.append((box, node))
        else:
            rows.append((box, [(box, node)]))
    for _, row in rows:
        yield [node for _, node in sorted(row, key=lambda item: item[0][0])]
    # A wrapped name may be geometrically adjacent even when extraction order
    # is reversed. Only aligned neighboring lines can form this extra stream;
    # distant occurrences of the same individual words do not imply a name.
    continuation: list[tuple[Box, Node]] = []
    left = 0.0
    for box, node in valid:
        if continuation:
            previous = continuation[-1][0]
            height = min(box[3] - box[1], previous[3] - previous[1])
            same_line = min(box[3], previous[3]) - max(box[1], previous[1]) >= height / 2
            next_line = 0 <= box[1] - previous[3] <= height and abs(box[0] - left) <= height
            if not same_line and not next_line:
                yield [part for _, part in continuation]
                continuation = []
        left = min(left, box[0]) if continuation else box[0]
        continuation.append((box, node))
    if continuation:
        yield [node for _, node in continuation]


def _raw_streams(blocks: Sequence[Node], nodes: Sequence[Node]) -> Iterator[Sequence[Node]]:
    yield from _raw_orders(blocks)
    # Cell-local streams prevent an adjacent amount column from interrupting a
    # hidden wrapped spelling. These add checks even for untrusted table views;
    # they grant no geometry or enrollment authority.
    seen: set[Box] = set()
    for node in nodes:
        if node.get("kind") != "TABLE_ROW":
            continue
        for cell in node.get("cells", ()):
            box = _box(cell.get("bbox"))
            if box is None or box in seen:
                continue
            seen.add(box)
            enclosed = [
                block
                for block in blocks
                if (value := _box(block.get("bbox"))) is not None and _inside(value, box)
            ]
            if len(enclosed) > 1:
                yield from _raw_orders(enclosed)


def _raw_occurrences(blocks: Sequence[Node], pattern: re.Pattern[str]) -> Iterator[list[Node]]:
    pieces = []
    spans = []
    offset = 0
    for node in blocks:
        text = _normalized(node["text"])
        pieces.append(text)
        spans.append((offset, offset + len(text), node))
        offset += len(text) + 1
    for match in pattern.finditer(" ".join(pieces)):
        yield [node for start, end, node in spans if start < match.end() and end > match.start()]


def _overlaps(left: Box, right: Box) -> bool:
    return min(left[2], right[2]) > max(left[0], right[0]) and min(left[3], right[3]) > max(
        left[1], right[1]
    )


def _plain_cells(node: Node) -> dict[int, Node] | None:
    cells = _cells(node)
    if not cells or any(
        not isinstance(cell.get("text"), str)
        or _box(cell.get("bbox")) is None
        or any(
            type(cell[key]) is not int or cell[key] != 1
            for key in ("row_span", "column_span")
            if cell.get(key) is not None
        )
        for cell in cells.values()
    ):
        return None
    boxes = [_box(cells[column]["bbox"]) for column in sorted(cells)]
    # A row supplies distinct columns, not vertically stacked/overlapping alternatives.
    if any(
        left[2] > right[0]
        for left, right in pairwise(boxes)
        if left is not None and right is not None
    ):
        return None
    return cells


def _non_name_regions(
    structure: Mapping[str, Any], nodes: Sequence[Node], pattern: re.Pattern[str]
) -> dict[str, tuple[Node, ...]]:
    """Prove column roles before exempting a reference inside a table cell.

    The complete name cell needs its own native locator. Ambiguous geometry,
    overlapping table views, spanning cells and inconsistent headers grant no exemption.
    """
    by_id = {node["node_id"]: node for node in structure["nodes"]}
    page_cells = [
        (node["node_id"], cell, box)
        for node in nodes
        if node.get("kind") == "TABLE_ROW"
        for cell in node.get("cells", ())
        if (box := _box(cell.get("bbox"))) is not None
    ]
    result: dict[str, tuple[Node, ...]] = {}
    for row in nodes:
        if (
            row.get("kind") != "TABLE_ROW"
            or row.get("row_role") != "data"
            or row.get("source_layer") != "native"
            or row.get("issue_codes")
            or row.get("schedulable") is False
            or not (cells := _plain_cells(row))
            or _normalized(row["text"])
            != _normalized("\t".join(cells[col]["text"] for col in sorted(cells)))
        ):
            continue
        context = [by_id.get(key) for key in row.get("context_node_ids", ())]
        if any(
            node is None or node.get("source_layer") != "native" or node.get("issue_codes")
            for node in context
        ):
            continue
        headers = [
            node
            for node in context
            if node is not None
            and node.get("kind") == "TABLE_ROW"
            and node.get("row_role") == "header"
        ]
        header_cells = [_plain_cells(header) for header in headers]
        if not headers or any(header is None for header in header_cells):
            continue
        valid_headers = [header for header in header_cells if header is not None]
        name_columns = {
            column
            for header in valid_headers
            for column, cell in header.items()
            if re.sub(r"[\s:：]", "", _normalized(cell["text"])) in _NAME_HEADERS
        }
        if len(name_columns) != 1:
            continue
        name_column = next(iter(name_columns))
        if name_column not in cells or any(
            name_column not in header
            or re.sub(r"[\s:：]", "", _normalized(header[name_column]["text"])) not in _NAME_HEADERS
            for header in valid_headers
        ):
            continue
        name_box = _box(cells[name_column]["bbox"])
        assert name_box is not None
        if any(
            _overlaps(name_box, other_box)
            for row_id, other, other_box in page_cells
            if row_id != row["node_id"] or other is not cells[name_column]
        ) or any(
            node.get("kind") == "BLOCK"
            and (box := _box(node.get("bbox"))) is not None
            and _inside(box, name_box)
            and (node.get("source_layer") != "native" or node.get("issue_codes"))
            for node in nodes
        ):
            continue
        eligible = []
        for column, cell in cells.items():
            if column == name_column or not pattern.search(_normalized(cell["text"])):
                continue
            box = _box(cell["bbox"])
            assert box is not None
            if any(
                column not in header
                or not header[column]["text"].strip()
                or re.sub(r"[\s:：]", "", _normalized(header[column]["text"])) in _NAME_HEADERS
                or not (header[column]["bbox"][0] <= box[0] < box[2] <= header[column]["bbox"][2])
                for header in valid_headers
            ):
                continue
            if any(
                _overlaps(box, other_box)
                for row_id, other, other_box in page_cells
                if row_id != row["node_id"] or other is not cell
            ):
                continue
            eligible.append(cell)
        if (
            eligible
            and physical_enrollment_locator(structure, cells[name_column]["text"], [_ref(row)])
            is not None
        ):
            result[row["node_id"]] = tuple(eligible)
    return result


def _non_name_occurrence(
    structure: Mapping[str, Any],
    selected: Sequence[Node],
    regions: Mapping[str, tuple[Node, ...]],
    pattern: re.Pattern[str],
) -> bool:
    if not selected or any(
        node.get("source_layer") != "native"
        or node.get("issue_codes")
        or node.get("kind") not in {"BLOCK", "TEXT_LINE", "TABLE_ROW"}
        or (node.get("kind") != "BLOCK" and node.get("schedulable") is False)
        for node in selected
    ):
        return False
    if len(selected) == 1 and selected[0]["kind"] == "TABLE_ROW":
        row = selected[0]
        eligible = regions.get(row["node_id"], ())
        matches = [
            cell for cell in row.get("cells", ()) if pattern.search(_normalized(cell["text"]))
        ]
        return bool(matches) and all(
            any(cell is allowed for allowed in eligible) for cell in matches
        )
    if any(node["kind"] == "TABLE_ROW" for node in selected):
        return False
    if any(
        node["kind"] == "TEXT_LINE"
        and physical_enrollment_locator(structure, node["text"], [_ref(node)]) is None
        for node in selected
    ):
        return False
    boxes = [_box(node.get("bbox")) for node in selected]
    if any(box is None for box in boxes):
        return False
    text = _normalized(" ".join(node["text"] for node in selected))
    return any(
        text in _normalized(cell["text"])
        and all(_inside(box, cell["bbox"]) for box in boxes if box is not None)
        for cells in regions.values()
        for cell in cells
    )


def unique_native_name_location(
    structure: Mapping[str, Any],
    physical_page: int,
    original_rider_name: str,
    expected_locator: Mapping[str, Any],
) -> bool:
    """Require every possible matching native occurrence to equal one proven span.

    Raw words are inspected even when a derived view claims to cover them. That
    prevents an incomplete/incorrect view from hiding an unresolved second name.
    Equivalent raw/line/table proofs collapse naturally to the expected locator.
    Unrelated unsupported text is not a page-wide failure.
    """
    try:
        if (
            type(physical_page) is not int
            or not 1 <= physical_page <= 500
            or not isinstance(original_rider_name, str)
            or not (name := _normalized(original_rider_name))
            or expected_locator.get("schema_version") != "enrollment-physical-v1"
            or type(expected_locator.get("physical_page")) is not int
            or expected_locator.get("physical_page") != physical_page
            or expected_locator.get("content_sha256") != structure["lineage"]["content_sha256"]
            or _box(expected_locator.get("name_bbox")) is None
        ):
            return False
        # Search all whitespace-equivalent spellings, including unpublished raw
        # fragments. Geometry still uses the actual native publication name.
        pattern = re.compile(
            r"(?<!\w)" + r"\s*".join(re.escape(c) for c in name if not c.isspace()) + r"(?!\w)"
        )
        nodes = [node for node in structure["nodes"] if node.get("page_number") == physical_page]
        found = False
        checked: set[tuple[str, ...]] = set()
        regions: dict[str, tuple[Node, ...]] | None = None

        def reference_only(selected: Sequence[Node]) -> bool:
            nonlocal regions
            if regions is None:
                regions = _non_name_regions(structure, nodes, pattern)
            return _non_name_occurrence(structure, selected, regions, pattern)

        def matches_expected(selected: Sequence[Node]) -> bool:
            if any(
                node.get("source_layer") != "native"
                or node.get("issue_codes")
                or (node.get("kind") != "BLOCK" and node.get("schedulable") is False)
                for node in selected
            ):
                return False
            locator = physical_enrollment_locator(
                structure, original_rider_name, [_ref(node) for node in selected]
            )
            return locator is not None and locator == expected_locator

        for node in nodes:
            text = node.get("text", "")
            if not isinstance(text, str) or not pattern.search(_normalized(text)):
                continue
            if not matches_expected([node]):
                if reference_only([node]):
                    checked.add((node["node_id"],))
                    continue
                return False
            found = True
            checked.add((node["node_id"],))
        raw = [
            node
            for node in nodes
            if node.get("kind") == "BLOCK" and isinstance(node.get("text"), str)
        ]
        layers = {node.get("source_layer") for node in raw}
        for layer in layers:
            blocks = [node for node in raw if node.get("source_layer") == layer]
            for ordered in _raw_streams(blocks, nodes):
                for selected in _raw_occurrences(ordered, pattern):
                    key = tuple(node["node_id"] for node in selected)
                    if key in checked:
                        continue
                    checked.add(key)
                    if not matches_expected(selected):
                        if reference_only(selected):
                            continue
                        return False
                    found = True
        return found
    except KeyError, TypeError, ValueError, AttributeError, OverflowError:
        return False
