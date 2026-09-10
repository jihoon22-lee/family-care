"""Independently recheck a metadata header against the complete retained page."""

from collections.abc import Callable
from typing import Any

from familycare_api.insurance_documents.terms_body_validation import (
    _box,
    _contains,
    _intersects,
    _lineage_valid,
)

_MAX_COMPARISONS = 65536
_MAX_TEXT_WORK = 4 * 1048576


def _position(node: dict[str, Any]) -> tuple[float, ...]:
    outer = _box(node)
    if node["kind"] != "TABLE_ROW":
        return outer
    cells = [_box(cell) for cell in node["cells"]]
    if not cells or any(not _contains(outer, cell) for cell in cells):
        raise ValueError("invalid header geometry")
    return (
        min(cell[0] for cell in cells),
        min(cell[1] for cell in cells),
        max(cell[2] for cell in cells),
        max(cell[3] for cell in cells),
    )


def _same_column(left: tuple[float, ...], right: tuple[float, ...]) -> bool:
    return abs(left[0] - right[0]) <= min(48, 2 * min(left[3] - left[1], right[3] - right[1])) and (
        min(left[2], right[2]) - max(left[0], right[0])
        >= 0.5 * min(left[2] - left[0], right[2] - right[0])
    )


def header_selection(
    nodes: list[dict[str, Any]],
    *,
    title: Callable[[dict[str, Any]], bool],
    metadata: Callable[[dict[str, Any]], bool],
    reference: Callable[[dict[str, Any]], bool],
    prelude: Callable[[dict[str, Any]], bool] | None = None,
) -> tuple[tuple[str, ...], frozenset[str]] | None:
    """A first header is a source observation, never a producer-provided privilege."""
    try:
        return _selection(
            nodes, title=title, metadata=metadata, reference=reference, prelude=prelude
        )
    except KeyError, TypeError, ValueError, AttributeError, IndexError, OverflowError:
        return None


def _selection(
    nodes: list[dict[str, Any]],
    *,
    title: Callable[[dict[str, Any]], bool],
    metadata: Callable[[dict[str, Any]], bool],
    reference: Callable[[dict[str, Any]], bool],
    prelude: Callable[[dict[str, Any]], bool] | None,
) -> tuple[tuple[str, ...], frozenset[str]] | None:
    if not nodes or len(nodes) > 10000:
        return None
    characters = sum(len(node["text"]) for node in nodes)
    if characters > 1048576:
        return None
    by_id = {node["node_id"]: node for node in nodes}
    if len(by_id) != len(nodes) or len({node["page_number"] for node in nodes}) != 1:
        return None
    represented: set[str] = set()
    for node in nodes:
        if node["kind"] != "TEXT_LINE":
            continue
        spans = node.get("source_spans", [])
        sources = {span["block_node_id"] for span in spans}
        if (
            not spans
            or len(spans) > 10000
            or represented & sources
            or any(
                type(span[key]) is not int
                for span in spans
                for key in ("block_start", "block_end", "line_start", "line_end")
            )
            or not _lineage_valid(node, by_id, metadata_revision="document-metadata-v10")
            or any(
                by_id[span["block_node_id"]].get("issue_codes")
                or by_id[span["block_node_id"]]["text"][: span["block_start"]].strip()
                or by_id[span["block_node_id"]]["text"][span["block_end"] :].strip()
                for span in spans
            )
        ):
            return None
        represented.update(sources)
    tables = [node for node in nodes if node["kind"] == "TABLE_ROW"]
    comparisons = text_work = 0
    for node in nodes:
        if (
            node["kind"] != "BLOCK"
            or node.get("schedulable", True)
            or node["node_id"] in represented
        ):
            continue
        for table in tables:
            comparisons += 1 + len(table["cells"])
            text_work += len(table["text"])
            if comparisons > _MAX_COMPARISONS or text_work > _MAX_TEXT_WORK:
                return None
            if table["source_layer"] != node["source_layer"] or table["text"] != "\t".join(
                cell["text"] for cell in table["cells"]
            ):
                continue
            _position(table)
            if any(
                _contains(_box(cell), _box(node)) and node["text"] and node["text"] in cell["text"]
                for cell in table["cells"]
            ):
                represented.add(node["node_id"])
                break
    active = [node for node in nodes if node["node_id"] not in represented and node["text"].strip()]
    if not active:
        return None
    # Unlocated text prevents proving which physical row actually came first.
    boxes = {node["node_id"]: _position(node) for node in active}
    invalid = {
        node["node_id"]
        for node in active
        if not node.get("schedulable", True)
        or node["source_layer"] not in {"native", "ocr"}
        or node["kind"] not in {"BLOCK", "TEXT_LINE", "TABLE_ROW"}
        or any(
            "UNRESOLVED" in code
            and not (node["kind"] == "TEXT_LINE" and code == "LINE_COLUMN_CONTEXT_UNRESOLVED")
            for code in node.get("issue_codes", [])
        )
        or (
            node["kind"] == "TABLE_ROW"
            and node["text"] != "\t".join(c["text"] for c in node["cells"])
        )
        or (
            node["kind"] == "TABLE_ROW"
            and (
                node.get("context_node_ids")
                or node.get("continuation_of") is not None
                or any(
                    cell["row_index"] != node.get("row_index")
                    or cell.get("row_span") not in (None, 1)
                    or cell.get("column_span") not in (None, 1)
                    for cell in node["cells"]
                )
            )
        )
    }
    ordered = sorted(
        active, key=lambda node: (boxes[node["node_id"]][1], boxes[node["node_id"]][0])
    )
    for index, node in enumerate(ordered):
        box = boxes[node["node_id"]]
        for other_index in range(index + 1, len(ordered)):
            other = ordered[other_index]
            if boxes[other["node_id"]][1] >= box[3]:
                break
            comparisons += 1
            if comparisons > _MAX_COMPARISONS:
                return None
            if _intersects(box, boxes[other["node_id"]]):
                invalid.update((node["node_id"], other["node_id"]))
    top = min(box[1] for box in boxes.values())
    titles = [node for node in ordered if boxes[node["node_id"]][1] == top and title(node)]
    reached_title = bool(titles)
    if not titles and prelude is not None:
        titles = [node for node in ordered if boxes[node["node_id"]][1] == top and prelude(node)]
    if len(titles) != 1 or titles[0]["node_id"] in invalid:
        return None
    head = titles[0]
    if any(
        boxes[node["node_id"]][1] < boxes[head["node_id"]][3] and reference(node) for node in active
    ):
        return None
    flow = [head]
    while len(flow) < len(active):
        comparisons += 2 * len(active)
        text_work += 2 * characters
        if comparisons > _MAX_COMPARISONS or text_work > _MAX_TEXT_WORK:
            return None
        previous = flow[-1]
        left = boxes[previous["node_id"]]
        candidates = []
        for node in ordered:
            right = boxes[node["node_id"]]
            if (
                node["node_id"] not in invalid
                and node["source_layer"] == previous["source_layer"]
                and right[1] >= left[3]
                and _same_column(left, right)
                and right[1] - left[3] <= min(48, 3 * min(left[3] - left[1], right[3] - right[1]))
                and (
                    metadata(node)
                    if reached_title
                    else title(node) or (prelude is not None and prelude(node))
                )
                and not reference(node)
            ):
                candidates.append(node)
        if not candidates:
            break
        following = candidates[0]
        right = boxes[following["node_id"]]
        if sum(boxes[node["node_id"]][1] == right[1] for node in candidates) != 1:
            break
        corridor = (min(left[0], right[0]), left[3], max(left[2], right[2]), right[1])
        if any(
            node["node_id"] not in {previous["node_id"], following["node_id"]}
            and _intersects(corridor, boxes[node["node_id"]])
            for node in active
        ):
            break
        if not reached_title and title(following):
            comparisons += 2 * len(active)
            text_work += 2 * characters
            if comparisons > _MAX_COMPARISONS or text_work > _MAX_TEXT_WORK:
                return None
            if sum(title(node) and boxes[node["node_id"]][1] <= right[1] for node in active) != 1:
                return None
            if any(boxes[node["node_id"]][1] < right[3] and reference(node) for node in active):
                return None
            reached_title = True
        flow.append(following)
    if not reached_title:
        return None
    return tuple(node["node_id"] for node in flow), frozenset(represented)
