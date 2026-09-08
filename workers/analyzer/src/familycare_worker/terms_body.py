"""Bounded observations of contractual prose; never enrollment or applicability authority.

The caller supplies a complete retained page, including preceding context and raw
blocks referenced by TEXT_LINE views. Observations preserve source addresses and
do not establish that adjacent pages belong to the same document component.
"""

import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from familycare_worker.document_structure import StructureNode

SemanticKind = Literal["PARTIES", "PAYMENT", "EXCLUSION", "DEFINITION"]
ObservationStatus = Literal["SUPPORTED", "UNSUPPORTED", "AMBIGUOUS"]
_MAX_NODES = 4096
_MAX_TEXT = 262144
_MAX_LINES = 4096
_MAX_PROVISIONS = 64
_MAX_BODY_SPANS = 32
_MAX_OUTPUT_TEXT = 32768


@dataclass(frozen=True, repr=False)
class BodySpan:
    node_id: str
    page_number: int
    start: int
    end: int
    text: str


@dataclass(frozen=True, repr=False)
class BodyProvision:
    article_number: int
    heading: BodySpan
    body: tuple[BodySpan, ...]
    semantic_kinds: tuple[SemanticKind, ...]


@dataclass(frozen=True, repr=False)
class BodyTableContext:
    """Retained IR references; a caller must resolve cross-page references separately."""

    node_id: str
    table_id: str
    context_node_ids: tuple[str, ...]
    header_node_ids: tuple[str, ...]
    continuation_of: str | None


@dataclass(frozen=True, repr=False)
class TermsBodyObservation:
    page_number: int
    status: ObservationStatus
    reason_codes: tuple[str, ...]
    provisions: tuple[BodyProvision, ...] = ()
    table_contexts: tuple[BodyTableContext, ...] = ()


_ARTICLE = re.compile(
    r"^(?:제\s*(?P<ko>[1-9][0-9]{0,3})\s*조|Article\s+(?P<en>[1-9][0-9]{0,3}))"
    r"(?:\s*[(（][^()（）\n]{1,160}[)）]|\s+[^\n]{1,160})?\s*$",
    re.IGNORECASE,
)
_CONTENTS_LEADER = re.compile(r"\.{2,}|…|·{2,}")
_CONTEXT = re.compile(
    r"^(?:상품\s*설명서|보험상품\s*설명서|목\s*차|차\s*례|"
    r"(?:제출|구비|준비)\s*서류\s*(?:목록|안내)|"
    r"(?:product\s+)?brochure|table\s+of\s+contents|checklist|example\s+clauses)"
    r"(?:\s|[:：(（\[【]|$)|"
    r"(?:약관|조항).{0,40}(?:설명.{0,20})?(?:위한\s*)?(?:예시|예문)|"
    r"(?:예시|예문).{0,30}(?:약관|조항)|"
    r"(?:for\s+(?:illustration|reference)|quoted\s+(?:clause|example))",
    re.IGNORECASE,
)
_EXAMPLE_START = re.compile(
    r"^(?:예시|예문|예제|예를\s*들어|example|illustration)(?:\s|[:：]|$)", re.I
)
_ITEM = r"(?:[①-⑳]\s*|[0-9]{1,2}[.)]\s*)?"
_COMPANY = r"(?:회사|보험회사|보험자)(?:는|가)"
_MIDDLE = r"[^.!?。\n]{0,180}"
_PAY = re.compile(
    _ITEM
    + _COMPANY
    + _MIDDLE
    + r"보험금"
    + _MIDDLE
    + r"지급(?:합니다|한다|하여야\s*합니다|하여야\s*한다|해야\s*합니다|해야\s*한다)[.]?"
)
_EXCLUDE = re.compile(
    _ITEM
    + _COMPANY
    + _MIDDLE
    + r"보험금"
    + _MIDDLE
    + r"지급하지\s*(?:않습니다|않는다|아니합니다|아니한다)[.]?"
)
_PARTIES = re.compile(
    _ITEM
    + _COMPANY
    + _MIDDLE
    + r"계약자"
    + _MIDDLE
    + r"보험계약"
    + _MIDDLE
    + r"(?:체결합니다|체결한다)[.]?"
)
_DEFINE = re.compile(
    _ITEM + r"[\"‘“]?(?:계약자|피보험자|보험수익자|보험금|보험회사|보험자|회사)[\"’”]?"
    r"(?:란|이라\s*함은|라\s*함은)\s+[^.!?。\n]{1,180}(?:말합니다|말한다|의미합니다)[.]?"
)
_EN_PAYMENT = re.compile(
    _ITEM + r"(?:The\s+)?(?:insurer|insurance\s+company)\s+"
    r"(?:shall|must)\s+(?P<negative>not\s+)?pay\s+[^.!?\n]{0,120}"
    r"(?:insurance\s+benefit|insured\s+benefit)[^.!?\n]{0,120}[.]?",
    re.IGNORECASE,
)
_EN_DEFINE = re.compile(
    _ITEM + r"(?:The\s+)?(?:insured|policyholder|beneficiary|insurance\s+benefit)\s+"
    r"(?:means|is\s+defined\s+as)\s+[^.!?\n]{1,180}[.]?",
    re.IGNORECASE,
)


class _InvalidPage(Exception):
    def __init__(self, reason: str, *, ambiguous: bool = True) -> None:
        self.reason = reason
        self.ambiguous = ambiguous


def _box(node: StructureNode) -> tuple[float, float, float, float]:
    box = node.bbox
    if (
        box is None
        or len(box) != 4
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in box
        )
        or not (0 <= box[0] < box[2] and 0 <= box[1] < box[3])
    ):
        raise _InvalidPage("SOURCE_GEOMETRY_UNRESOLVED")
    return box


def _lineage(node: StructureNode, by_id: dict[str, StructureNode]) -> set[str]:
    if not node.source_spans or len(node.source_spans) > _MAX_NODES:
        raise _InvalidPage("SOURCE_LINEAGE_INVALID")
    covered = 0
    blocks: set[str] = set()
    boxes = []
    previous: StructureNode | None = None
    for span in node.source_spans:
        block = by_id.get(span.block_node_id)
        if (
            block is None
            or block.kind != "BLOCK"
            or block.node_id in blocks
            or block.source_layer != node.source_layer
            or not 0 <= span.block_start < span.block_end <= len(block.text)
            or not covered <= span.line_start < span.line_end <= len(node.text)
            or node.text[covered : span.line_start].strip()
            or block.text[span.block_start : span.block_end]
            != node.text[span.line_start : span.line_end]
        ):
            raise _InvalidPage("SOURCE_LINEAGE_INVALID")
        blocks.add(block.node_id)
        covered = span.line_end
        box = _box(block)
        if previous is not None:
            last = _box(previous)
            height, last_height = box[3] - box[1], last[3] - last[1]
            if (
                block.reading_order != previous.reading_order + 1
                or min(box[3], last[3]) - max(box[1], last[1]) < 0.8 * max(height, last_height)
                or not 0 <= box[0] - last[2] <= 1.5 * min(height, last_height)
            ):
                raise _InvalidPage("SOURCE_LINEAGE_INVALID")
        elif node.reading_order != block.reading_order:
            raise _InvalidPage("SOURCE_LINEAGE_INVALID")
        previous = block
        boxes.append(box)
    if node.text[covered:].strip() or _box(node) != (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ):
        raise _InvalidPage("SOURCE_LINEAGE_INVALID")
    return blocks


def _inside(inner: tuple[float, ...], outer: tuple[float, ...]) -> bool:
    return (
        outer[0] <= inner[0] < inner[2] <= outer[2] and outer[1] <= inner[1] < inner[3] <= outer[3]
    )


def _placement(node: StructureNode) -> tuple[float, float, float, float]:
    outer = _box(node)
    if node.kind != "TABLE_ROW":
        return outer
    boxes = [cell.bbox for cell in node.cells]
    if not boxes or any(box is None or not _inside(box, outer) for box in boxes):
        raise _InvalidPage("TABLE_LAYOUT_UNRESOLVED")
    known = [box for box in boxes if box is not None]
    return (
        min(b[0] for b in known),
        min(b[1] for b in known),
        max(b[2] for b in known),
        max(b[3] for b in known),
    )


def _reference(node: StructureNode) -> bool:
    return any(
        _CONTEXT.search(_reference_text(line)) or _EXAMPLE_START.search(_reference_text(line))
        for line in node.text.splitlines()
    )


def _reference_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text.strip()).strip("[](){}【】<> ")


def _table_represents(node: StructureNode, table: StructureNode) -> bool:
    """Quarantine is not permission to hide raw text absent from the visible cell view."""
    if table.source_layer != node.source_layer or table.text != "\t".join(
        cell.text for cell in table.cells
    ):
        return False
    try:
        _placement(table)
        raw = _box(node)
        return any(
            cell.bbox is not None
            and _inside(raw, cell.bbox)
            and node.text
            and node.text in cell.text
            for cell in table.cells
        )
    except _InvalidPage:
        return False


def _table_valid(
    node: StructureNode, by_id: dict[str, StructureNode], *, require_local_contexts: bool
) -> bool:
    if node.kind != "TABLE_ROW":
        return True
    if (
        not node.table_id
        or node.row_role not in {"header", "data"}
        or len(node.cells) != 1
        or node.cells[0].text != node.text
        or node.cells[0].row_index != node.row_index
        or node.cells[0].row_span not in (None, 1)
        or node.cells[0].column_span not in (None, 1)
    ):
        return False
    for identifier in node.context_node_ids:
        context = by_id.get(identifier)
        if context is None:
            if require_local_contexts or node.continuation_of is None:
                return False
        elif (
            context.kind != "TABLE_ROW"
            or context.row_role != "header"
            or context.table_id != node.table_id
            or context.row_index is None
            or node.row_index is None
            or context.row_index >= node.row_index
            or _placement(context)[3] > _placement(node)[1]
        ):
            return False
    return True


def _shares_column(left: tuple[float, ...], right: tuple[float, ...]) -> bool:
    overlap = min(left[2], right[2]) - max(left[0], right[0])
    tolerance = min(48.0, 2 * min(left[3] - left[1], right[3] - right[1]))
    return abs(left[0] - right[0]) <= tolerance and overlap >= 0.5 * min(
        left[2] - left[0], right[2] - right[0]
    )


def _regions(
    page_number: int, nodes: Sequence[StructureNode], *, require_local_contexts: bool
) -> tuple[list[list[StructureNode]], bool, bool]:
    """Build independent local flows; unusable geometry is a barrier, never blank text."""
    if len(nodes) > _MAX_NODES or sum(len(node.text) for node in nodes) > _MAX_TEXT:
        raise _InvalidPage("PAGE_BUDGET_EXCEEDED", ambiguous=False)
    by_id = {node.node_id: node for node in nodes}
    if len(by_id) != len(nodes) or any(node.page_number != page_number for node in nodes):
        raise _InvalidPage("SOURCE_PAGE_INVALID")
    represented: set[str] = set()
    invalid: set[str] = set()
    for node in nodes:
        if node.kind == "TEXT_LINE":
            try:
                blocks = _lineage(node, by_id)
                if represented & blocks:
                    raise _InvalidPage("SOURCE_LINEAGE_INVALID")
                represented.update(blocks)
            except _InvalidPage:
                invalid.add(node.node_id)
    tables = [node for node in nodes if node.kind == "TABLE_ROW"]
    for node in nodes:
        if node.kind != "BLOCK" or node.schedulable or node.node_id in represented:
            continue
        if any(_table_represents(node, table) for table in tables):
            represented.add(node.node_id)
    active = [node for node in nodes if node.node_id not in represented and node.text.strip()]
    boxes: dict[str, tuple[float, float, float, float]] = {}
    for node in active:
        try:
            boxes[node.node_id] = _placement(node)
            if (
                not node.schedulable
                or node.source_layer not in {"native", "ocr"}
                or any(
                    "UNRESOLVED" in issue
                    and not (node.kind == "TEXT_LINE" and issue == "LINE_COLUMN_CONTEXT_UNRESOLVED")
                    for issue in node.issue_codes
                )
                or not _table_valid(node, by_id, require_local_contexts=require_local_contexts)
            ):
                invalid.add(node.node_id)
        except _InvalidPage:
            invalid.add(node.node_id)
            if _reference(node):
                raise _InvalidPage("REFERENCE_CONTEXT_UNLOCATED") from None
    positioned = sorted(
        (node for node in active if node.node_id in boxes),
        key=lambda node: (boxes[node.node_id][1], boxes[node.node_id][0]),
    )
    # A document-level reference title above all other text remains page context,
    # even when the title is narrower than the columns beneath it.
    article_top = min(
        (
            boxes[node.node_id][1]
            for node in positioned
            if any(_ARTICLE.fullmatch(line.strip()) for line in node.text.splitlines())
        ),
        default=float("inf"),
    )
    for node in positioned:
        first_line = _reference_text(node.text.splitlines()[0])
        if (
            re.match(
                r"(?:(?:보험)?상품\s*설명서|목\s*차|차\s*례|(?:product\s+)?brochure|"
                r"table\s+of\s+contents|checklist)(?:\s|[:：(（\[【]|$)",
                first_line,
                re.I,
            )
            and boxes[node.node_id][1] <= article_top
        ):
            raise _InvalidPage("EXPLANATORY_OR_REFERENCE_CONTEXT", ambiguous=False)
    comparisons = 0
    # Sweep vertical overlaps. Only intersecting rectangles contaminate each other.
    for index, node in enumerate(positioned):
        left = boxes[node.node_id]
        other_index = index + 1
        while other_index < len(positioned) and boxes[positioned[other_index].node_id][1] < left[3]:
            other = positioned[other_index]
            right = boxes[other.node_id]
            comparisons += 1
            if comparisons > 65536:
                raise _InvalidPage("PAGE_BUDGET_EXCEEDED", ambiguous=False)
            if min(left[2], right[2]) > max(left[0], right[0]):
                invalid.update((node.node_id, other.node_id))
            other_index += 1
    columns: list[list[StructureNode]] = []
    for node in sorted(
        (n for n in positioned if n.node_id not in invalid),
        key=lambda n: (boxes[n.node_id][0], boxes[n.node_id][1]),
    ):
        matching = [
            column
            for column in columns
            if _shares_column(boxes[column[0].node_id], boxes[node.node_id])
        ]
        if len(matching) == 1:
            matching[0].append(node)
        elif not matching and len(columns) < 64:
            columns.append([node])
        else:
            invalid.add(node.node_id)
    barriers = [node for node in positioned if node.node_id in invalid]
    unlocated = [node for node in active if node.node_id in invalid and node.node_id not in boxes]
    references = [node for node in positioned if _reference(node)]
    regions: list[list[StructureNode]] = []
    reference_limited = False
    for column in columns:
        column.sort(key=lambda node: (boxes[node.node_id][1], boxes[node.node_id][0]))
        ids = {node.node_id for node in column}
        # An outside reference stops the affected flow. Never inject the other
        # region's surrounding text into this region's operative sentence parser.
        cutoff = min(
            (
                boxes[node.node_id][1]
                for node in references
                if node.node_id not in ids
                and any(
                    min(boxes[node.node_id][2], boxes[item.node_id][2])
                    > max(boxes[node.node_id][0], boxes[item.node_id][0])
                    and boxes[node.node_id][1] < boxes[item.node_id][3]
                    for item in column
                )
            ),
            default=float("inf"),
        )
        flow: list[StructureNode] = []
        for node in column:
            if boxes[node.node_id][3] > cutoff:
                reference_limited = True
                break
            if flow:
                previous = flow[-1]
                left, right = boxes[previous.node_id], boxes[node.node_id]
                gap = min(48.0, 3 * min(left[3] - left[1], right[3] - right[1]))
                barrier = any(
                    left[3] <= boxes[b.node_id][1] < right[1]
                    and _shares_column(boxes[b.node_id], right)
                    for b in barriers
                )
                barrier = barrier or (
                    previous.kind != "TABLE_ROW"
                    and node.kind != "TABLE_ROW"
                    and any(
                        b.kind != "TABLE_ROW"
                        and b.source_layer == node.source_layer
                        and previous.reading_order < b.reading_order < node.reading_order
                        for b in unlocated
                    )
                )
                if (
                    barrier
                    or right[1] - left[3] > gap
                    or previous.source_layer != node.source_layer
                ):
                    regions.append(flow)
                    flow = []
            flow.append(node)
        if flow:
            regions.append(flow)
    return regions, bool(invalid), reference_limited


def _semantic_kinds(text: str) -> tuple[SemanticKind, ...]:
    kinds: set[SemanticKind] = set()
    # Sentence boundaries prevent assembling a subject in a description with an
    # unrelated quoted predicate. A wrapped sentence may span original nodes.
    for sentence in re.split(r"(?<=[.!?。])\s+", text):
        if _EXCLUDE.fullmatch(sentence):
            kinds.add("EXCLUSION")
        elif _PAY.fullmatch(sentence):
            kinds.add("PAYMENT")
        if _PARTIES.fullmatch(sentence):
            kinds.add("PARTIES")
        if _DEFINE.fullmatch(sentence) or _EN_DEFINE.fullmatch(sentence):
            kinds.add("DEFINITION")
        english = _EN_PAYMENT.fullmatch(sentence)
        if english:
            kinds.add("EXCLUSION" if english.group("negative") else "PAYMENT")
    return tuple(sorted(kinds))


def role_witness(observation: TermsBodyObservation) -> tuple[BodySpan, ...]:
    """Keep sufficient role evidence after complete page/context observation.

    Select only the first supported provision. Prefer the fewest consecutive body
    spans, then their earliest source order; wrapped sentences remain intact.
    This witness does not replace the full observation's range/conflict checks.
    """
    if observation.status != "SUPPORTED" or not observation.provisions:
        return ()
    provision = observation.provisions[0]
    body = provision.body
    if not body or len(body) > _MAX_BODY_SPANS:
        return ()
    for length in range(1, len(body) + 1):
        for start in range(len(body) - length + 1):
            window = body[start : start + length]
            if _semantic_kinds(" ".join(span.text for span in window)):
                return (provision.heading, *window)
    return ()


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


def _complete_view_sources(node: StructureNode, by_id: dict[str, StructureNode]) -> set[str] | None:
    """Return completely represented raw words; preserve unrepresented tails."""
    if node.kind == "BLOCK":
        return set()
    try:
        if any(
            type(value) is not int
            for span in node.source_spans
            for value in (span.block_start, span.block_end, span.line_start, span.line_end)
        ):
            return None
        represented = _lineage(node, by_id)
        if any(
            by_id[span.block_node_id].page_number != node.page_number
            or by_id[span.block_node_id].issue_codes
            or by_id[span.block_node_id].text[: span.block_start].strip()
            or by_id[span.block_node_id].text[span.block_end :].strip()
            for span in node.source_spans
        ):
            return None
        return represented
    except _InvalidPage, KeyError, TypeError, ValueError, AttributeError, IndexError:
        return None


def _instruction_nodes(nodes: Sequence[StructureNode]) -> set[str]:
    by_id = {node.node_id: node for node in nodes}
    ignored: set[str] = set()
    if len(by_id) != len(nodes) or len({(n.kind, n.source_path) for n in nodes}) != len(nodes):
        return ignored
    for node in nodes:
        if node.kind not in {"BLOCK", "TEXT_LINE"} or node.issue_codes or not node.schedulable:
            continue
        if not _navigation_instruction(node.text):
            continue
        represented = _complete_view_sources(node, by_id)
        if represented is not None:
            ignored.update(represented)
            ignored.add(node.node_id)
    return ignored


def _reading_guide_heading(text: str) -> bool:
    return (
        re.fullmatch(
            r"(?:보험)?약관\s*(?:이용\s*(?:가이드|안내)|읽는\s*방법|이해\s*(?:가이드|길잡이))",
            _reference_text(text),
        )
        is not None
    )


def _reading_guide_notice(text: str) -> bool:
    text = _reference_text(text)
    return bool(
        len(text) <= 240
        and len(text.splitlines()) == 1
        and re.fullmatch(
            r"(?:예시|예문|예제)(?:\s|[:：])[^.!?。\n]{1,230}"
            r"(?:합니다|됩니다|있습니다|없습니다|입니다)[.]?",
            text,
        )
        and "약관" in text
        and re.search(r"이해.{0,15}(?:돕|도움|쉽)", text)
        and re.search(r"참고|참조", text)
        and not re.search(
            r"가정|가상|다음|아래|이하|경우|계약자|피보험자|보험금|지급|사망|입원|수술|인용", text
        )
    )


def _reading_guide_lines(nodes: Sequence[StructureNode]) -> set[tuple[str, int]]:
    """Exclude only a proven reading-guide legend, never the rest of its page."""
    if (
        not 1 <= len(nodes) <= _MAX_NODES
        or sum(len(node.text) for node in nodes) > _MAX_TEXT
        or sum(len(node.text.splitlines()) for node in nodes) > _MAX_LINES
    ):
        return set()
    by_id = {node.node_id: node for node in nodes}
    ignored: set[tuple[str, int]] = set()
    if len(by_id) != len(nodes) or len({(n.kind, n.source_path) for n in nodes}) != len(nodes):
        return ignored
    valid: list[tuple[StructureNode, set[str]]] = []
    for node in nodes:
        if (
            node.kind not in {"BLOCK", "TEXT_LINE"}
            or not isinstance(node.node_id, str)
            or not 1 <= len(node.node_id) <= 128
            or not isinstance(node.source_path, str)
            or not node.source_path
            or node.issue_codes
            or node.schedulable is not True
            or node.source_layer not in {"native", "ocr"}
            or type(node.page_number) is not int
            or not 1 <= node.page_number <= 500
            or type(node.reading_order) is not int
            or node.reading_order < 0
            or (node.kind == "TEXT_LINE" and len(node.text.splitlines()) != 1)
        ):
            continue
        represented = _complete_view_sources(node, by_id)
        if represented is None or any(
            type(by_id[identifier].page_number) is not int
            or type(by_id[identifier].reading_order) is not int
            or by_id[identifier].reading_order < 0
            or type(by_id[identifier].schedulable) is not bool
            for identifier in represented
        ):
            continue
        try:
            _box(node)
        except _InvalidPage:
            continue
        valid.append((node, represented))
    claims: dict[str, int] = {}
    for _, represented in valid:
        for identifier in represented:
            claims[identifier] = claims.get(identifier, 0) + 1
    valid = [
        (node, represented)
        for node, represented in valid
        if node.node_id not in claims and all(claims[identifier] == 1 for identifier in represented)
    ]
    headings = [
        (node, index)
        for node, _ in valid
        for index, line in enumerate(node.text.splitlines())
        if _reading_guide_heading(line)
    ]
    for node, represented in valid:
        for index, line in enumerate(node.text.splitlines()):
            if not _reading_guide_notice(line):
                continue
            if not any(
                header.page_number == node.page_number
                and header.source_layer == node.source_layer
                and (
                    (header.node_id == node.node_id and header_index < index)
                    or (
                        header.node_id != node.node_id
                        and header.reading_order < node.reading_order
                        and _box(header)[3] <= _box(node)[1]
                    )
                )
                for header, header_index in headings
            ):
                continue
            ignored.add((node.node_id, index))
            for identifier in represented:
                ignored.update(
                    (identifier, i) for i, _ in enumerate(by_id[identifier].text.splitlines())
                )
    return ignored


def reference_context_present(
    nodes: Sequence[StructureNode],
    *,
    persistent_only: bool = False,
    navigation_instructions: bool = False,
    reading_guides: bool = False,
) -> bool:
    """Retain an explicit reference boundary even if its layout is unsupported."""
    ignored = _instruction_nodes(nodes) if navigation_instructions else set()
    if not persistent_only:
        return any(_reference(node) for node in nodes if node.node_id not in ignored)
    ignored_lines = _reading_guide_lines(nodes) if reading_guides else set()
    for node in nodes:
        if node.node_id in ignored:
            continue
        for index, line in enumerate(node.text.splitlines()):
            if (node.node_id, index) in ignored_lines:
                continue
            text = _reference_text(line)
            navigation = re.match(
                r"^(?:목\s*차|차\s*례|table\s+of\s+contents)(?=\s|[:：(（\[【]|$)", text, re.I
            )
            if navigation:
                text = _reference_text(text[navigation.end() :].lstrip(":： "))
            if _CONTEXT.search(text) or _EXAMPLE_START.search(text):
                return True
    return False


def _observe_region(page_number: int, nodes: Sequence[StructureNode]) -> TermsBodyObservation:
    """Inspect one source-proven flow, retaining its own preceding passage context."""
    if type(page_number) is not int or not 1 <= page_number <= 500:
        return TermsBodyObservation(page_number, "AMBIGUOUS", ("SOURCE_PAGE_INVALID",))
    try:
        ordered = list(nodes)
        native = [node for node in ordered if node.kind != "TABLE_ROW"]
        if any(
            right.reading_order <= left.reading_order
            for left, right in zip(native, native[1:], strict=False)
        ):
            raise _InvalidPage("SOURCE_LAYOUT_UNRESOLVED")
        provisions: list[BodyProvision] = []
        heading: BodySpan | None = None
        body: list[BodySpan] = []
        article_number = 0
        line_count = 0
        output_size = 0
        reference_passage = False

        def finish() -> None:
            nonlocal output_size
            if heading is None or not body:
                return
            kinds = _semantic_kinds(" ".join(span.text for span in body))
            if not kinds:
                return
            if len(body) > _MAX_BODY_SPANS or any(
                len(span.text) > 240 for span in (heading, *body)
            ):
                raise _InvalidPage("EVIDENCE_BUDGET_EXCEEDED", ambiguous=False)
            output_size += len(heading.text) + sum(len(span.text) for span in body)
            if len(provisions) >= _MAX_PROVISIONS or output_size > _MAX_OUTPUT_TEXT:
                raise _InvalidPage("EVIDENCE_BUDGET_EXCEEDED", ambiguous=False)
            provisions.append(BodyProvision(article_number, heading, tuple(body), kinds))

        for node in ordered:
            if reference_passage:
                break
            offset = 0
            for raw in node.text.splitlines(keepends=True):
                line_count += 1
                if line_count > _MAX_LINES:
                    raise _InvalidPage("PAGE_BUDGET_EXCEEDED", ambiguous=False)
                text = raw.strip()
                start = offset + len(raw) - len(raw.lstrip())
                offset += len(raw)
                if not text:
                    continue
                if _CONTEXT.search(_reference_text(text)) or _EXAMPLE_START.search(
                    _reference_text(text)
                ):
                    finish()
                    if not provisions:
                        raise _InvalidPage("EXPLANATORY_OR_REFERENCE_CONTEXT", ambiguous=False)
                    reference_passage = True
                    heading, body = None, []
                    break
                if node.kind == "TABLE_ROW" and node.row_role == "header":
                    continue
                matched = _ARTICLE.fullmatch(text)
                if matched and not _CONTENTS_LEADER.search(text):
                    finish()
                    number = int(matched.group("ko") or matched.group("en"))
                    if number <= article_number:
                        raise _InvalidPage("ARTICLE_ORDER_AMBIGUOUS")
                    article_number = number
                    heading = BodySpan(node.node_id, page_number, start, start + len(text), text)
                    body = []
                elif heading is not None:
                    body.append(BodySpan(node.node_id, page_number, start, start + len(text), text))
        finish()
        if not provisions:
            return TermsBodyObservation(page_number, "UNSUPPORTED", ("NO_OPERATIVE_PROVISION",))
        used = {span.node_id for p in provisions for span in (p.heading, *p.body)}
        local_headers = {node.node_id for node in ordered if node.row_role == "header"}
        contexts = tuple(
            BodyTableContext(
                node.node_id,
                node.table_id,
                node.context_node_ids,
                tuple(
                    identifier
                    for identifier in node.context_node_ids
                    if identifier in local_headers
                ),
                node.continuation_of,
            )
            for node in ordered
            if node.node_id in used and node.table_id is not None
        )
        reasons: tuple[str, ...] = ("OPERATIVE_PROVISION_OBSERVED",)
        if reference_passage:
            reasons += ("REFERENCE_PASSAGE_EXCLUDED",)
        return TermsBodyObservation(page_number, "SUPPORTED", reasons, tuple(provisions), contexts)
    except _InvalidPage as error:
        return TermsBodyObservation(
            page_number, "AMBIGUOUS" if error.ambiguous else "UNSUPPORTED", (error.reason,)
        )


def observe_terms_body(
    page_number: int, nodes: Sequence[StructureNode], *, require_local_contexts: bool = False
) -> TermsBodyObservation:
    """Observe independent bounded regions of a complete retained page.

    A malformed sidebar/table does not erase another proved flow. No sentence is
    assembled across columns, intervening unusable regions, or large gaps. Multiple
    supported flows have deterministic output order, not document reading-order
    authority; callers must not infer article/range continuity between them.
    Strict local contexts exclude unresolved external table headers before any
    provisions are collected, preserving independent proved text regions.
    """
    if type(page_number) is not int or not 1 <= page_number <= 500:
        return TermsBodyObservation(page_number, "AMBIGUOUS", ("SOURCE_PAGE_INVALID",))
    try:
        regions, unresolved, reference_limited = _regions(
            page_number, nodes, require_local_contexts=require_local_contexts
        )
    except _InvalidPage as error:
        return TermsBodyObservation(
            page_number, "AMBIGUOUS" if error.ambiguous else "UNSUPPORTED", (error.reason,)
        )
    observed = [_observe_region(page_number, region) for region in regions]
    supported = [item for item in observed if item.status == "SUPPORTED"]
    by_id = {node.node_id: node for node in nodes}
    supported.sort(
        key=lambda item: (
            _placement(by_id[item.provisions[0].heading.node_id])[1],
            _placement(by_id[item.provisions[0].heading.node_id])[0],
            item.provisions[0].heading.node_id,
            item.provisions[0].heading.start,
        )
    )
    unresolved = unresolved or any(item.status == "AMBIGUOUS" for item in observed)
    if not supported:
        return TermsBodyObservation(
            page_number,
            "AMBIGUOUS" if unresolved else "UNSUPPORTED",
            ("UNRESOLVED_REGIONS_EXCLUDED" if unresolved else "NO_OPERATIVE_PROVISION",),
        )
    provisions = tuple(provision for item in supported for provision in item.provisions)
    if (
        len(provisions) > _MAX_PROVISIONS
        or sum(
            len(span.text)
            for provision in provisions
            for span in (provision.heading, *provision.body)
        )
        > _MAX_OUTPUT_TEXT
    ):
        return TermsBodyObservation(page_number, "UNSUPPORTED", ("EVIDENCE_BUDGET_EXCEEDED",))
    reasons = ["OPERATIVE_PROVISION_OBSERVED"]
    if len(supported) > 1:
        reasons.append("INDEPENDENT_BODY_REGIONS")
    if unresolved:
        reasons.append("UNRESOLVED_REGIONS_EXCLUDED")
    if reference_limited or any(
        "REFERENCE_PASSAGE_EXCLUDED" in item.reason_codes
        or "EXPLANATORY_OR_REFERENCE_CONTEXT" in item.reason_codes
        for item in observed
    ):
        reasons.append("REFERENCE_PASSAGE_EXCLUDED")
    return TermsBodyObservation(
        page_number,
        "SUPPORTED",
        tuple(reasons),
        provisions,
        tuple(context for item in supported for context in item.table_contexts),
    )
