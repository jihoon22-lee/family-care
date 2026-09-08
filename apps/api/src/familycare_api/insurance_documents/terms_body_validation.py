"""Independently derive classification evidence from retained contractual body text.

These observations classify content only. They establish no enrollment, edition
applicability, executable condition or payment amount.
"""

import math
import re
import unicodedata
from typing import Any

_ARTICLE = re.compile(
    r"^(?:제\s*(?P<ko>[1-9][0-9]{0,3})\s*조|Article\s+(?P<en>[1-9][0-9]{0,3}))"
    r"(?:\s*[(（][^()（）\n]{1,160}[)）]|\s+[^\n]{1,160})?\s*$",
    re.IGNORECASE,
)
_REFERENCE = re.compile(
    r"^(?:상품\s*설명서|보험상품\s*설명서|목\s*차|차\s*례|"
    r"(?:제출|구비|준비)\s*서류\s*(?:목록|안내)|"
    r"(?:product\s+)?brochure|table\s+of\s+contents|checklist|example\s+clauses)"
    r"(?:\s|[:：(（\[【]|$)|(?:약관|조항).{0,40}(?:설명.{0,20})?(?:위한\s*)?(?:예시|예문)|"
    r"(?:예시|예문).{0,30}(?:약관|조항)|"
    r"(?:for\s+(?:illustration|reference)|quoted\s+(?:clause|example))|"
    r"^(?:예시|예문|예제|예를\s*들어|example|illustration)(?:\s|[:：]|$)",
    re.IGNORECASE,
)
_DOCUMENT_REFERENCE = re.compile(
    r"^(?:상품\s*설명서|보험상품\s*설명서|목\s*차|차\s*례|"
    r"(?:제출|구비|준비)\s*서류\s*(?:목록|안내)|"
    r"(?:product\s+)?brochure|table\s+of\s+contents|checklist)(?:\s|[:：(（\[【]|$)",
    re.IGNORECASE,
)
_ITEM = r"(?:[①-⑳]\s*|[0-9]{1,2}[.)]\s*)?"
_COMPANY = r"(?:회사|보험회사|보험자)(?:는|가)"
_MIDDLE = r"[^.!?。\n]{0,180}"


def _operative(text: str) -> bool:
    patterns = (
        _ITEM
        + _COMPANY
        + _MIDDLE
        + r"보험금"
        + _MIDDLE
        + r"(?:지급(?:합니다|한다|하여야\s*합니다|하여야\s*한다|해야\s*합니다|해야\s*한다)"
        + r"|지급하지\s*(?:않습니다|않는다|아니합니다|아니한다))[.]?",
        _ITEM
        + _COMPANY
        + _MIDDLE
        + r"계약자"
        + _MIDDLE
        + r"보험계약"
        + _MIDDLE
        + r"(?:체결합니다|체결한다)[.]?",
        _ITEM
        + r"[\"‘“]?(?:계약자|피보험자|보험수익자|보험금|보험회사|보험자|회사)[\"’”]?"
        + r"(?:란|이라\s*함은|라\s*함은)\s+[^.!?。\n]{1,180}(?:말합니다|말한다|의미합니다)[.]?",
        _ITEM
        + r"(?:The\s+)?(?:insurer|insurance\s+company)\s+(?:shall|must)\s+(?:not\s+)?pay\s+"
        + r"[^.!?\n]{0,120}(?:insurance\s+benefit|insured\s+benefit)[^.!?\n]{0,120}[.]?",
        _ITEM
        + r"(?:The\s+)?(?:insured|policyholder|beneficiary|insurance\s+benefit)\s+"
        + r"(?:means|is\s+defined\s+as)\s+[^.!?\n]{1,180}[.]?",
    )
    return any(
        re.fullmatch(pattern, sentence, re.IGNORECASE)
        for sentence in re.split(r"(?<=[.!?。])\s+", text)
        for pattern in patterns
    )


def _box(node: dict[str, Any]) -> tuple[float, ...]:
    value = node["bbox"]
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 4
        or any(type(number) not in (int, float) or not math.isfinite(number) for number in value)
        or not (0 <= value[0] < value[2] and 0 <= value[1] < value[3])
    ):
        raise ValueError("invalid retained geometry")
    return tuple(value)


def _lineage_valid(node: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> bool:
    if node["kind"] != "TEXT_LINE":
        return True
    spans = node.get("source_spans", [])
    if not spans:
        return False
    cursor = 0
    boxes = []
    previous = None
    used: set[str] = set()
    for span in spans:
        block = by_id.get(span["block_node_id"])
        left, right = span["line_start"], span["line_end"]
        start, end = span["block_start"], span["block_end"]
        if (
            block is None
            or block["kind"] != "BLOCK"
            or block["node_id"] in used
            or block["page_number"] != node["page_number"]
            or block["source_layer"] != node["source_layer"]
            or not 0 <= start < end <= len(block["text"])
            or not cursor <= left < right <= len(node["text"])
            or node["text"][cursor:left].strip()
            or node["text"][left:right] != block["text"][start:end]
        ):
            return False
        used.add(block["node_id"])
        box = _box(block)
        if previous is not None:
            last = _box(previous)
            height, last_height = box[3] - box[1], last[3] - last[1]
            if (
                block["reading_order"] != previous["reading_order"] + 1
                or min(box[3], last[3]) - max(box[1], last[1]) < 0.8 * max(height, last_height)
                or not 0 <= box[0] - last[2] <= 1.5 * min(height, last_height)
            ):
                return False
        elif block["reading_order"] != node["reading_order"]:
            return False
        boxes.append(box)
        previous = block
        cursor = right
    return not node["text"][cursor:].strip() and _box(node) == (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _span(node: dict[str, Any], start: int, end: int) -> dict[str, Any]:
    return {
        "node_id": node["node_id"],
        "page_number": node["page_number"],
        "start": start,
        "end": end,
        "text": node["text"][start:end],
        "anchor_start": start,
        "anchor_end": end,
    }


def body_evidence(
    page_number: int, nodes: list[dict[str, Any]]
) -> tuple[tuple[int, ...], tuple[dict[str, Any], ...], bool] | None:
    """Return complete, bounded source observations, or no supported body route."""
    try:
        return _body_evidence(page_number, nodes)
    except KeyError, TypeError, ValueError, AttributeError, IndexError:
        return None


def _navigation_instruction(text: str) -> bool:
    """A complete instruction referring to documents is not a document heading."""
    normalized = unicodedata.normalize("NFKC", text.strip())
    if len(normalized.splitlines()) != 1:
        return False
    return bool(
        re.fullmatch(
            r"(?:보험)?상품\s*설명서(?:를|을|와|과|\s)[^.!?。\n]{0,160}"
            r"(?:참고|참조|확인)[^.!?。\n]{0,80}(?:하십시오|하시기\s*바랍니다|하세요)[.]?",
            normalized,
        )
        and not re.search(r"예시|예문|예제|인용|설명하기\s*위한|[:：]", normalized)
    )


def _instruction_nodes(nodes: list[dict[str, Any]]) -> set[str]:
    by_id = {node["node_id"]: node for node in nodes}
    ignored: set[str] = set()
    if len(by_id) != len(nodes) or len({(n["kind"], n["source_path"]) for n in nodes}) != len(
        nodes
    ):
        return ignored
    for node in nodes:
        if (
            node["kind"] not in {"BLOCK", "TEXT_LINE"}
            or node.get("issue_codes")
            or not node.get("schedulable")
        ):
            continue
        if not _navigation_instruction(node["text"]):
            continue
        if node["kind"] == "BLOCK":
            ignored.add(node["node_id"])
            continue
        try:
            if any(
                type(value) is not int
                for span in node["source_spans"]
                for value in (
                    span["block_start"],
                    span["block_end"],
                    span["line_start"],
                    span["line_end"],
                )
            ):
                continue
            if not _lineage_valid(node, by_id):
                continue
            spans = node["source_spans"]
            if any(
                by_id[span["block_node_id"]].get("issue_codes")
                or by_id[span["block_node_id"]]["text"][: span["block_start"]].strip()
                or by_id[span["block_node_id"]]["text"][span["block_end"] :].strip()
                for span in spans
            ):
                continue
            ignored.update(span["block_node_id"] for span in spans)
            ignored.add(node["node_id"])
        except KeyError, TypeError, ValueError, AttributeError, IndexError:
            continue
    return ignored


def _guide_heading(text: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?:보험)?약관\s*(?:이용\s*(?:가이드|안내)|읽는\s*방법|이해\s*(?:가이드|길잡이))",
            _reference_text(text),
        )
    )


def _guide_notice(text: str) -> bool:
    normalized = _reference_text(text)
    return bool(
        1 <= len(normalized) <= 240
        and len(normalized.splitlines()) == 1
        and re.fullmatch(
            r"(?:예시|예문|예제)(?:\s|[:：])[^.!?。\n]{1,230}"
            r"(?:합니다|됩니다|있습니다|없습니다|입니다)[.]?",
            normalized,
        )
        and "약관" in normalized
        and re.search(r"이해.{0,15}(?:돕|도움|쉽)", normalized)
        and re.search(r"참고|참조", normalized)
        and not re.search(
            r"가정|가상|다음|아래|이하|경우|계약자|피보험자|보험금|지급|사망|입원|수술|인용",
            normalized,
        )
    )


def _guide_view_sources(
    node: dict[str, Any], by_id: dict[str, dict[str, Any]]
) -> tuple[str, ...] | None:
    """Validate one view without suppressing another invalid source on the page."""
    try:
        if (
            node["kind"] not in {"BLOCK", "TEXT_LINE"}
            or not isinstance(node["node_id"], str)
            or not 1 <= len(node["node_id"]) <= 128
            or not isinstance(node["source_path"], str)
            or not node["source_path"]
            or type(node["page_number"]) is not int
            or not 1 <= node["page_number"] <= 500
            or node["source_layer"] not in {"native", "ocr"}
            or type(node["reading_order"]) is not int
            or node["reading_order"] < 0
            or node["schedulable"] is not True
            or node.get("issue_codes")
        ):
            return None
        _box(node)
        if node["kind"] != "TEXT_LINE":
            return ()
        spans = node["source_spans"]
        if (
            len(node["text"].splitlines()) != 1
            or not 1 <= len(spans) <= 4096
            or any(
                type(span[key]) is not int
                for span in spans
                for key in ("block_start", "block_end", "line_start", "line_end")
            )
            or not _lineage_valid(node, by_id)
        ):
            return None
        identifiers = tuple(span["block_node_id"] for span in spans)
        if any(
            type(by_id[span["block_node_id"]]["page_number"]) is not int
            or type(by_id[span["block_node_id"]]["reading_order"]) is not int
            or by_id[span["block_node_id"]]["reading_order"] < 0
            or type(by_id[span["block_node_id"]]["schedulable"]) is not bool
            or by_id[span["block_node_id"]].get("issue_codes")
            or by_id[span["block_node_id"]]["text"][: span["block_start"]].strip()
            or by_id[span["block_node_id"]]["text"][span["block_end"] :].strip()
            for span in spans
        ):
            return None
        return identifiers
    except KeyError, TypeError, ValueError, AttributeError, IndexError, OverflowError:
        return None


def _guide_source_views(
    nodes: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], tuple[str, ...]]]:
    """Retain complete logical lines with unique addresses and raw ownership."""
    if (
        not 1 <= len(nodes) <= 4096
        or sum(len(node["text"]) for node in nodes) > 262144
        or sum(len(node["text"].splitlines()) for node in nodes) > 4096
    ):
        return []
    by_id = {node["node_id"]: node for node in nodes}
    if len(by_id) != len(nodes) or len({(n["kind"], n["source_path"]) for n in nodes}) != len(
        nodes
    ):
        return []
    views = [
        (node, sources)
        for node in nodes
        if (sources := _guide_view_sources(node, by_id)) is not None
    ]
    owners: dict[str, int] = {}
    for _, sources in views:
        for identifier in sources:
            owners[identifier] = owners.get(identifier, 0) + 1
    return [
        (node, sources)
        for node, sources in views
        if node["node_id"] not in owners and all(owners[identifier] == 1 for identifier in sources)
    ]


def _reading_guide_lines(nodes: list[dict[str, Any]]) -> set[tuple[str, int]]:
    """Exempt only a legend notice below a proved guide heading in the same source."""
    ignored: set[tuple[str, int]] = set()
    try:
        views = _guide_source_views(nodes)
        headings = [
            (node, index)
            for node, _ in views
            for index, text in enumerate(node["text"].splitlines())
            if _guide_heading(text)
        ]
        by_id = {node["node_id"]: node for node in nodes}
        for node, raw_ids in views:
            for index, text in enumerate(node["text"].splitlines()):
                if not _guide_notice(text):
                    continue
                if not any(
                    (heading["node_id"] == node["node_id"] and heading_index < index)
                    or (
                        heading["node_id"] != node["node_id"]
                        and heading["page_number"] == node["page_number"]
                        and heading["source_layer"] == node["source_layer"]
                        and heading["reading_order"] < node["reading_order"]
                        and _box(heading)[3] <= _box(node)[1]
                    )
                    for heading, heading_index in headings
                ):
                    continue
                ignored.add((node["node_id"], index))
                # TEXT_LINE is proved to contain one line. Its fully represented
                # raw words cannot become independent example headings again.
                for identifier in raw_ids:
                    ignored.update(
                        (identifier, raw_index)
                        for raw_index, _ in enumerate(by_id[identifier]["text"].splitlines())
                    )
        return ignored
    except KeyError, TypeError, ValueError, AttributeError, IndexError, OverflowError:
        return set()


def reference_context_present(
    nodes: list[dict[str, Any]],
    *,
    persistent_only: bool = False,
    navigation_instructions: bool = False,
    reading_guides: bool = False,
) -> bool:
    """A visible reference boundary persists until a new document boundary."""
    ignored = _instruction_nodes(nodes) if navigation_instructions else set()
    guide_lines = _reading_guide_lines(nodes) if persistent_only and reading_guides else set()
    for node in nodes:
        if ignored and node["node_id"] in ignored:
            continue
        for index, line in enumerate(node["text"].splitlines()):
            if guide_lines and (node["node_id"], index) in guide_lines:
                continue
            text = _reference_text(line)
            if persistent_only:
                navigation = re.match(
                    r"^(?:목\s*차|차\s*례|table\s+of\s+contents)(?=\s|[:：(（\[【]|$)", text, re.I
                )
                if navigation:
                    text = _reference_text(text[navigation.end() :].lstrip(":： "))
            if _REFERENCE.search(text):
                return True
    return False


def _reference_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text.strip()).strip("[](){}【】<> ")


def _contains(outer: tuple[float, ...], inner: tuple[float, ...]) -> bool:
    return (
        outer[0] <= inner[0] < inner[2] <= outer[2] and outer[1] <= inner[1] < inner[3] <= outer[3]
    )


def _layout_box(node: dict[str, Any]) -> tuple[float, ...]:
    if node["kind"] == "TABLE_ROW" and len(node.get("cells", [])) == 1:
        return _box(node["cells"][0])
    return _box(node)


def _table_valid(node: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> bool:
    cells = node.get("cells", [])
    if (
        len(cells) != 1
        or not node.get("table_id")
        or node.get("row_role") not in {"header", "data"}
        or cells[0]["text"] != node["text"]
        or not _contains(_box(node), _box(cells[0]))
        or cells[0]["row_index"] != node.get("row_index")
        or cells[0].get("row_span") not in (None, 1)
        or cells[0].get("column_span") not in (None, 1)
    ):
        return False
    for identifier in node.get("context_node_ids", []):
        context = by_id.get(identifier)
        if (
            context is None
            or context["kind"] != "TABLE_ROW"
            or context.get("row_role") != "header"
            or context.get("table_id") != node.get("table_id")
            or context["source_layer"] != node["source_layer"]
            or context.get("row_index") is None
            or node.get("row_index") is None
            or context["row_index"] >= node["row_index"]
            or _layout_box(context)[3] > _layout_box(node)[1]
            or not _table_valid(context, {})
        ):
            return False
    return True


def _table_duplicate(node: dict[str, Any], tables: list[dict[str, Any]]) -> bool:
    if node["kind"] not in {"BLOCK", "TEXT_LINE"}:
        return False
    for table in tables:
        if node["source_layer"] != table["source_layer"]:
            continue
        for cell in table.get("cells", []):
            try:
                represented = " ".join(node["text"].split())
                if (
                    represented
                    and _contains(_box(table), _box(cell))
                    and _contains(_box(cell), _box(node))
                    and represented in " ".join(cell["text"].split())
                ):
                    return True
            except KeyError, TypeError, ValueError:
                continue
    return False


def _local_nodes(
    nodes: list[dict[str, Any]], by_id: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    represented: set[str] = set()
    owners: dict[str, list[str]] = {}
    for node in nodes:
        if node["kind"] == "TEXT_LINE":
            for span in node.get("source_spans", []):
                identifier = span["block_node_id"]
                represented.add(identifier)
                owners.setdefault(identifier, []).append(node["node_id"])
    conflicting = {identifier for ids in owners.values() if len(ids) > 1 for identifier in ids}
    selected: list[dict[str, Any]] = []
    barriers: list[dict[str, Any]] = []
    tables = [node for node in nodes if node["kind"] == "TABLE_ROW"]
    for node in nodes:
        if node["node_id"] in represented or not node["text"].strip():
            continue
        try:
            _box(node)
            if _table_duplicate(node, tables):
                continue
            valid = (
                node["node_id"] not in conflicting
                and node["source_layer"] in {"native", "ocr"}
                and node["kind"] in {"BLOCK", "TEXT_LINE", "TABLE_ROW"}
                and node.get("schedulable", True)
                and not any(
                    "UNRESOLVED" in issue and issue != "LINE_COLUMN_CONTEXT_UNRESOLVED"
                    for issue in node.get("issue_codes", [])
                )
                and _lineage_valid(node, by_id)
                and (node["kind"] != "TABLE_ROW" or _table_valid(node, by_id))
            )
        except KeyError, TypeError, ValueError, IndexError:
            valid = False
        (selected if valid else barriers).append(node)
    return selected, barriers


def _intersects(left: tuple[float, ...], right: tuple[float, ...]) -> bool:
    return min(left[2], right[2]) > max(left[0], right[0]) and min(left[3], right[3]) > max(
        left[1], right[1]
    )


def _continues(left: dict[str, Any], right: dict[str, Any]) -> bool:
    a, b = _layout_box(left), _layout_box(right)
    height = min(a[3] - a[1], b[3] - b[1])
    return (
        left["source_layer"] == right["source_layer"]
        and abs(a[0] - b[0]) <= min(48, height * 2)
        and min(a[2], b[2]) - max(a[0], b[0]) >= min(a[2] - a[0], b[2] - b[0]) * 0.5
        and 0 <= b[1] - a[3] <= min(48, height * 3)
        and (
            left["kind"] == "TABLE_ROW"
            or right["kind"] == "TABLE_ROW"
            or left["reading_order"] < right["reading_order"]
        )
    )


def _regions(
    nodes: list[dict[str, Any]], barriers: list[dict[str, Any]]
) -> list[list[dict[str, Any]]]:
    obstacles = []
    unlocated = []
    for node in barriers:
        try:
            obstacles.append(_layout_box(node))
        except KeyError, TypeError, ValueError:
            unlocated.append(node)
    boxes = [_layout_box(node) for node in nodes]
    overlapping = {
        index
        for index, box in enumerate(boxes)
        if any(_intersects(box, other) for other in obstacles)
        or any(
            index != other_index and _intersects(box, other)
            for other_index, other in enumerate(boxes)
        )
    }
    obstacles.extend(boxes[index] for index in overlapping)
    usable = [node for index, node in enumerate(nodes) if index not in overlapping]
    regions: list[list[dict[str, Any]]] = []
    for node in sorted(
        usable, key=lambda item: (_layout_box(item)[1], _layout_box(item)[0], item["node_id"])
    ):
        candidates = []
        for region in regions:
            if not _continues(region[-1], node):
                continue
            if any(
                barrier["kind"] in {"BLOCK", "TEXT_LINE"}
                and barrier["source_layer"] == node["source_layer"]
                and region[-1]["reading_order"] < barrier["reading_order"] < node["reading_order"]
                for barrier in unlocated
            ):
                continue
            a, b = _layout_box(region[-1]), _layout_box(node)
            corridor = (min(a[0], b[0]), a[3], max(a[2], b[2]), b[1])
            if not any(_intersects(corridor, barrier) for barrier in obstacles):
                candidates.append(region)
        if len(candidates) == 1:
            candidates[0].append(node)
        else:
            regions.append([node])
    return regions


def _preceding_reference(region: list[dict[str, Any]], nodes: list[dict[str, Any]]) -> bool:
    first = _layout_box(region[0])
    own_ids = {node["node_id"] for node in region}
    for node in nodes:
        if node["node_id"] in own_ids or not reference_context_present([node]):
            continue
        try:
            box = _layout_box(node)
        except KeyError, TypeError, ValueError:
            return True
        if box[1] <= first[1] and min(box[2], first[2]) > max(box[0], first[0]):
            return True
    return False


def _external_reference_top(
    region: list[dict[str, Any]], context: list[dict[str, Any]]
) -> float | None:
    own_ids = {node["node_id"] for node in region}
    tops = []
    for node in context:
        if node["node_id"] in own_ids or not reference_context_present([node]):
            continue
        try:
            box = _layout_box(node)
        except KeyError, TypeError, ValueError:
            continue
        if any(
            box[1] < _layout_box(item)[3]
            and min(box[2], _layout_box(item)[2]) > max(box[0], _layout_box(item)[0])
            for item in region
        ):
            tops.append(box[1])
    return min(tops) if tops else None


def _body_evidence(
    page_number: int, nodes: list[dict[str, Any]]
) -> tuple[tuple[int, ...], tuple[dict[str, Any], ...], bool] | None:
    if type(page_number) is not int or not 1 <= page_number <= 500 or len(nodes) > 4096:
        return None
    by_id = {node["node_id"]: node for node in nodes}
    if len(by_id) != len(nodes):
        return None
    if (
        sum(len(node["text"]) for node in nodes) > 262144
        or sum(len(node["text"].splitlines()) for node in nodes) > 4096
    ):
        return None
    if any(node["page_number"] != page_number for node in nodes):
        return None
    selected, barriers = _local_nodes(nodes, by_id)
    regions = _regions(selected, barriers)
    results = []
    article_tops = [
        _layout_box(node)[1]
        for node in selected
        if any(_ARTICLE.fullmatch(line.strip()) for line in node["text"].splitlines())
    ]
    if article_tops and any(
        _DOCUMENT_REFERENCE.search(_reference_text(node["text"].partition("\n")[0]))
        and _layout_box(node)[1] <= min(article_tops)
        for node in nodes
    ):
        return None
    for region in regions:
        if _preceding_reference(region, nodes):
            continue
        result = _region_evidence(region, _external_reference_top(region, [*selected, *barriers]))
        if result is not None:
            results.append(result)
    if not results:
        return None
    results.sort(
        key=lambda result: (
            _layout_box(by_id[result[1][0]["node_id"]])[1],
            _layout_box(by_id[result[1][0]["node_id"]])[0],
            result[1][0]["node_id"],
            result[1][0]["start"],
        )
    )
    numbers = tuple(number for result in results for number in result[0])
    if len(numbers) > 64:
        return None
    # Region ordering is deterministic output placement, never cross-column order proof.
    return numbers, results[0][1], len(results) == 1


def _region_evidence(
    ordered: list[dict[str, Any]],
    external_reference_top: float | None = None,
) -> tuple[tuple[int, ...], tuple[dict[str, Any], ...], bool] | None:
    numbers: list[int] = []
    evidence: list[dict[str, Any]] = []
    current: tuple[int, dict[str, Any]] | None = None
    body: list[dict[str, Any]] = []
    blocked = False
    last_article = 0
    line_count = 0

    def finish() -> None:
        if current is None or not body or not _operative(" ".join(span["text"] for span in body)):
            return
        if len(body) > 32 or any(len(span["text"]) > 240 for span in (current[1], *body)):
            raise ValueError("evidence budget exceeded")
        numbers.append(current[0])
        if not evidence:
            for length in range(1, len(body) + 1):
                window = next(
                    (
                        body[start : start + length]
                        for start in range(len(body) - length + 1)
                        if _operative(
                            " ".join(span["text"] for span in body[start : start + length])
                        )
                    ),
                    None,
                )
                if window is not None:
                    evidence.extend((current[1], *window))
                    break
        if len(numbers) > 64 or sum(len(span["text"]) for span in evidence) > 32768:
            raise ValueError("evidence budget exceeded")

    for node in ordered:
        if external_reference_top is not None and _layout_box(node)[3] > external_reference_top:
            break
        if blocked:
            break
        offset = 0
        for line in node["text"].splitlines(keepends=True):
            line_count += 1
            if line_count > 4096:
                return None
            raw = line.rstrip("\r\n")
            trimmed = raw.strip()
            start = offset + len(raw) - len(raw.lstrip())
            end = start + len(trimmed)
            offset += len(line)
            if not trimmed:
                continue
            if _REFERENCE.search(_reference_text(trimmed)):
                finish()
                blocked = True
                current = None
                body = []
                break
            if node["kind"] == "TABLE_ROW" and node["row_role"] == "header":
                continue
            heading = _ARTICLE.fullmatch(trimmed)
            if heading and not re.search(r"\.{2,}|…|·{2,}", trimmed):
                finish()
                number = int(heading.group("ko") or heading.group("en"))
                if number <= last_article:
                    return None
                last_article = number
                current = number, _span(node, start, end)
                body = []
            elif current is not None:
                body.append(_span(node, start, end))
    finish()
    return (tuple(numbers), tuple(evidence), True) if evidence else None
