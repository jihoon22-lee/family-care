"""Prove a first physical metadata header against every retained page node.

This selects existing source addresses. It neither reconstructs text nor decides
that an arbitrary independent body region is a new document header.
"""

from collections.abc import Callable, Sequence

from familycare_worker.document_structure import StructureNode
from familycare_worker.terms_body import (
    _complete_view_sources,
    _InvalidPage,
    _placement,
    _shares_column,
    _table_represents,
)

Box = tuple[float, float, float, float]
_MAX_COMPARISONS = 65536
_MAX_TEXT_WORK = 4 * 1048576


def _intersects(left: Box, right: Box) -> bool:
    return min(left[2], right[2]) > max(left[0], right[0]) and min(left[3], right[3]) > max(
        left[1], right[1]
    )


def header_selection(
    nodes: Sequence[StructureNode],
    *,
    title: Callable[[StructureNode], bool],
    metadata: Callable[[StructureNode], bool],
    reference: Callable[[StructureNode], bool],
    prelude: Callable[[StructureNode], bool] | None = None,
) -> tuple[tuple[str, ...], frozenset[str]] | None:
    """Return a proved header flow and fully represented source nodes, or nothing."""
    if not nodes or len(nodes) > 10000:
        return None
    characters = sum(len(node.text) for node in nodes)
    if characters > 1048576:
        return None
    by_id = {node.node_id: node for node in nodes}
    if len(by_id) != len(nodes) or len({node.page_number for node in nodes}) != 1:
        return None
    represented: set[str] = set()
    for node in nodes:
        if node.kind != "TEXT_LINE":
            continue
        sources = _complete_view_sources(node, by_id)
        if sources is None or represented & sources:
            return None
        represented.update(sources)
    tables = [node for node in nodes if node.kind == "TABLE_ROW"]
    comparisons = text_work = 0
    for node in nodes:
        if node.kind != "BLOCK" or node.schedulable or node.node_id in represented:
            continue
        for table in tables:
            comparisons += 1 + len(table.cells)
            text_work += len(table.text)
            if comparisons > _MAX_COMPARISONS or text_work > _MAX_TEXT_WORK:
                return None
            if _table_represents(node, table):
                represented.add(node.node_id)
                break
    active = [node for node in nodes if node.node_id not in represented and node.text.strip()]
    boxes: dict[str, Box] = {}
    invalid: set[str] = set()
    try:
        for node in active:
            # Any unlocated source can precede the supposed header. Keep it a
            # barrier even if its extractor ordinal happens to come last.
            boxes[node.node_id] = _placement(node)
            if (
                not node.schedulable
                or node.source_layer not in {"native", "ocr"}
                or node.kind not in {"BLOCK", "TEXT_LINE", "TABLE_ROW"}
                or any(
                    "UNRESOLVED" in issue
                    and not (node.kind == "TEXT_LINE" and issue == "LINE_COLUMN_CONTEXT_UNRESOLVED")
                    for issue in node.issue_codes
                )
                or (node.kind == "TABLE_ROW" and node.text != "\t".join(c.text for c in node.cells))
                or (
                    node.kind == "TABLE_ROW"
                    and (
                        node.context_node_ids
                        or node.continuation_of is not None
                        or any(
                            cell.row_index != node.row_index
                            or cell.row_span not in (None, 1)
                            or cell.column_span not in (None, 1)
                            for cell in node.cells
                        )
                    )
                )
            ):
                invalid.add(node.node_id)
    except TypeError, ValueError, _InvalidPage:
        return None
    if not active:
        return None
    ordered = sorted(active, key=lambda node: (boxes[node.node_id][1], boxes[node.node_id][0]))
    for index, node in enumerate(ordered):
        box = boxes[node.node_id]
        for other_index in range(index + 1, len(ordered)):
            other = ordered[other_index]
            if boxes[other.node_id][1] >= box[3]:
                break
            comparisons += 1
            if comparisons > _MAX_COMPARISONS:
                return None
            if _intersects(box, boxes[other.node_id]):
                invalid.update((node.node_id, other.node_id))
    top = min(box[1] for box in boxes.values())
    first = [node for node in ordered if boxes[node.node_id][1] == top and title(node)]
    reached_title = bool(first)
    if not first and prelude is not None:
        first = [node for node in ordered if boxes[node.node_id][1] == top and prelude(node)]
    if len(first) != 1 or first[0].node_id in invalid:
        return None
    head = first[0]
    if any(boxes[node.node_id][1] < boxes[head.node_id][3] and reference(node) for node in active):
        return None
    flow = [head]
    while len(flow) < len(active):
        # Charge both complete scans before doing them. Exhaustion cannot
        # promote a truncated metadata prefix to a completed proof.
        comparisons += 2 * len(active)
        text_work += 2 * characters
        if comparisons > _MAX_COMPARISONS or text_work > _MAX_TEXT_WORK:
            return None
        previous = flow[-1]
        left = boxes[previous.node_id]
        candidates = [
            node
            for node in ordered
            if node.node_id not in invalid
            and node.source_layer == previous.source_layer
            and boxes[node.node_id][1] >= left[3]
            and _shares_column(left, boxes[node.node_id])
            and boxes[node.node_id][1] - left[3]
            <= min(48, 3 * min(left[3] - left[1], boxes[node.node_id][3] - boxes[node.node_id][1]))
            and (
                metadata(node)
                if reached_title
                else title(node) or (prelude is not None and prelude(node))
            )
            and not reference(node)
        ]
        if not candidates:
            break
        following = candidates[0]
        right = boxes[following.node_id]
        if sum(boxes[node.node_id][1] == right[1] for node in candidates) != 1:
            break
        corridor = (min(left[0], right[0]), left[3], max(left[2], right[2]), right[1])
        if any(
            node.node_id not in {previous.node_id, following.node_id}
            and _intersects(corridor, boxes[node.node_id])
            for node in active
        ):
            break
        if not reached_title and title(following):
            # The prefix cannot borrow a later title from another column or
            # pass earlier reference text, including text outside its corridor.
            comparisons += 2 * len(active)
            text_work += 2 * characters
            if comparisons > _MAX_COMPARISONS or text_work > _MAX_TEXT_WORK:
                return None
            if sum(title(node) and boxes[node.node_id][1] <= right[1] for node in active) != 1:
                return None
            if any(boxes[node.node_id][1] < right[3] and reference(node) for node in active):
                return None
            reached_title = True
        flow.append(following)
    if not reached_title:
        return None
    return tuple(node.node_id for node in flow), frozenset(represented)
