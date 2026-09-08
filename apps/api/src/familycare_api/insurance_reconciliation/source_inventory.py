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
from typing import Any

from familycare_api.policies.enrollment_locator import physical_enrollment_locator

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
        pattern = re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)")
        nodes = [node for node in structure["nodes"] if node.get("page_number") == physical_page]
        found = False
        checked: set[tuple[str, ...]] = set()

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
            for ordered in _raw_orders(blocks):
                for selected in _raw_occurrences(ordered, pattern):
                    key = tuple(node["node_id"] for node in selected)
                    if key in checked:
                        continue
                    checked.add(key)
                    if not matches_expected(selected):
                        return False
                    found = True
        return found
    except KeyError, TypeError, ValueError, AttributeError, OverflowError:
        return False
