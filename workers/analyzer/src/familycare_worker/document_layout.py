"""Conservative source-backed layout annotations; no business facts or I/O.

Only table metadata is added to a detached JSON copy. Relations are proposals
with source paths, not enrollment, monetary-unit conversion or user approval.
Missing/ambiguous geometry or labels produces explicit unresolved codes.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

BBox = tuple[float, float, float, float]
_HEADER_LABELS = {
    "특약명": "name",
    "담보명": "name",
    "보장명": "name",
    "보장내용": "name",
    "rider name": "name",
    "coverage name": "name",
    "coverage": "name",
    "가입금액": "amount",
    "보험가입금액": "amount",
    "보장금액": "amount",
    "sum assured": "amount",
    "insured amount": "amount",
    "보험기간": "period",
    "보장기간": "period",
    "가입기간": "period",
    "coverage period": "period",
    "insurance period": "period",
}
_UNIT = re.compile(
    r"^[\[(（]?\s*(?:단위|units?)\s*[:：]\s*(만원|천원|원|krw|usd|eur|gbp|jpy)\s*[\])）]?$",
    re.IGNORECASE,
)
_MARKER = r"(?<!\*)(\*{1,3}|[†‡※¹²³⁴⁵⁶⁷⁸⁹])"
_CELL_MARKER = re.compile(_MARKER + r"\s*$")
_NOTE = re.compile(r"^\s*" + _MARKER + r"(?:\s+|[:：])\s*\S")
_CONTINUED = re.compile(
    r"^(?:[（(]?계속[)）]?|표\s*계속|continued|table continued|continued from previous page)$",
    re.IGNORECASE,
)
_TITLE = re.compile(
    r"^(?:계약명|상품명|contract(?: title| name)?|policy(?: name)?)\s*[:：]\s*(.+)$", re.IGNORECASE
)
_CONTRACT_NUMBER = re.compile(
    r"^(?:증권번호|계약번호|policy number|contract number)\s*[:：]\s*(.+)$", re.IGNORECASE
)
_CAPTION_GAP_POINTS = 36.0
_NOTE_GAP_POINTS = 72.0
_LINE_CENTER_TOLERANCE = 3.0
_COLUMN_TOLERANCE = 0.005


class DocumentLayoutError(ValueError):
    def __init__(self) -> None:
        super().__init__("DOCUMENT_LAYOUT_INVALID")


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise DocumentLayoutError
    return cast(Mapping[str, object], value)


def _sequence(value: object) -> Sequence[object]:
    if not isinstance(value, (tuple, list)):
        raise DocumentLayoutError
    return value


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DocumentLayoutError
    return value


def _copy(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _copy(item) for key, item in _mapping(value).items()}
    if isinstance(value, (tuple, list)):
        return [_copy(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise DocumentLayoutError


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise DocumentLayoutError
    return value


def _normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _box(value: object) -> BBox | None:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        return None
    if any(
        isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item)
        for item in value
    ):
        return None
    x0, y0, x1, y1 = (float(item) for item in value)
    if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _evidence(path: str, number: int, box: BBox | None) -> dict[str, object]:
    return {"source_path": path, "page_number": number, "bbox": list(box) if box else None}


@dataclass(repr=False)
class _Block:
    text: str
    order: int
    box: BBox
    path: str


@dataclass(repr=False)
class _Line:
    blocks: list[_Block]
    page_number: int

    @property
    def text(self) -> str:
        return " ".join(block.text for block in self.blocks)

    @property
    def box(self) -> BBox:
        return (
            min(block.box[0] for block in self.blocks),
            min(block.box[1] for block in self.blocks),
            max(block.box[2] for block in self.blocks),
            max(block.box[3] for block in self.blocks),
        )

    @property
    def orders(self) -> list[int]:
        return sorted(block.order for block in self.blocks)

    @property
    def evidence(self) -> list[dict[str, object]]:
        return [_evidence(block.path, self.page_number, block.box) for block in self.blocks]


@dataclass(repr=False)
class _Table:
    page_number: int
    table_index: int
    width: float | None
    raw: dict[str, object]
    path: str
    box: BBox | None
    rows: dict[int, list[tuple[int, Mapping[str, object]]]]
    headers: list[int] = field(default_factory=list)
    header_evidence: list[dict[str, object]] = field(default_factory=list)
    relations: list[dict[str, object]] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    proposals: dict[str, object] = field(default_factory=dict)

    def add(self, name: str, value: object, reason: str, evidence: list[dict[str, object]]) -> None:
        self.proposals[name] = value
        self.relations.append(
            {"field": name, "value": value, "reason_code": reason, "evidence": evidence}
        )


@dataclass(repr=False)
class _Page:
    number: int
    lines: list[_Line]
    tables: list[_Table]
    available: bool


def _lines(raw: Mapping[str, object], position: int, number: int) -> list[_Line]:
    blocks = []
    orders = set()
    for index, item in enumerate(_sequence(raw.get("blocks", ()))):
        block = _mapping(item)
        box = _box(block.get("bbox"))
        order = _integer(block.get("reading_order"))
        if order in orders:
            raise DocumentLayoutError
        orders.add(order)
        if box is not None:
            blocks.append(
                _Block(_text(block.get("text")), order, box, f"/pages/{position}/blocks/{index}")
            )
    grouped: list[_Line] = []
    for positioned_block in sorted(blocks, key=lambda item: (item.box[1], item.box[0])):
        center = (positioned_block.box[1] + positioned_block.box[3]) / 2
        candidates = [
            line
            for line in grouped
            if abs((line.box[1] + line.box[3]) / 2 - center) <= _LINE_CENTER_TOLERANCE
        ]
        if len(candidates) == 1:
            candidates[0].blocks.append(positioned_block)
            candidates[0].blocks.sort(key=lambda item: item.box[0])
        else:
            grouped.append(_Line([positioned_block], number))
    return grouped


def _pages(source: dict[str, object]) -> list[_Page]:
    result = []
    seen = set()
    for position, raw_value in enumerate(_sequence(source.get("pages"))):
        raw = _mapping(raw_value)
        number = _integer(raw.get("page_number"))
        if not 1 <= number <= 500 or number in seen:
            raise DocumentLayoutError
        seen.add(number)
        raw_width = raw.get("width_points")
        width = (
            float(raw_width)
            if isinstance(raw_width, (int, float))
            and not isinstance(raw_width, bool)
            and math.isfinite(raw_width)
            and raw_width > 0
            else None
        )
        tables = []
        for index, table_value in enumerate(_sequence(raw.get("tables", ()))):
            table = cast(dict[str, object], _mapping(table_value))
            rows: dict[int, list[tuple[int, Mapping[str, object]]]] = {}
            identities = set()
            for cell_index, cell_value in enumerate(_sequence(table.get("cells", ()))):
                cell = _mapping(cell_value)
                row, column = _integer(cell.get("row_index")), _integer(cell.get("column_index"))
                if (row, column) in identities:
                    raise DocumentLayoutError
                identities.add((row, column))
                rows.setdefault(row, []).append((cell_index, cell))
            for cells in rows.values():
                cells.sort(key=lambda item: _integer(item[1]["column_index"]))
            tables.append(
                _Table(
                    number,
                    index,
                    width,
                    table,
                    f"/pages/{position}/tables/{index}",
                    _box(table.get("bbox")),
                    rows,
                )
            )
        available = raw.get("status") not in {"failed", "cancelled"} and (
            _mapping(raw.get("quality", {})).get("classification") != "OCR_REQUIRED"
        )
        result.append(_Page(number, _lines(raw, position, number), tables, available))
    return sorted(result, key=lambda page: page.number)


def _header_label(text: str) -> str:
    # Only formatting and explicit footnote symbols/units are removed for matching.
    text = _CELL_MARKER.sub("", text)
    text = re.sub(r"[（(]\s*(?:만원|천원|원|krw|usd|eur)\s*[)）]", "", text, flags=re.IGNORECASE)
    return _normalized(text)


def _headers(table: _Table) -> None:
    for row, cells in sorted(table.rows.items()):
        texts = [_header_label(_text(cell.get("text"))) for _, cell in cells]
        if any(re.search(r"\d", text) for text in texts):
            continue
        categories = {_HEADER_LABELS.get(text) for text in texts}
        if "name" not in categories or not categories.intersection({"amount", "period"}):
            continue
        if sum(text in _HEADER_LABELS for text in texts) < 2:
            continue
        table.headers.append(row)
        table.header_evidence.extend(
            _evidence(f"{table.path}/cells/{index}", table.page_number, _box(cell.get("bbox")))
            for index, cell in cells
        )
    if table.headers:
        table.add("header_rows", table.headers, "EXPLICIT_COLUMN_LABELS", table.header_evidence)
    else:
        table.unresolved.append("HEADER_ROW_UNRESOLVED")


def _near(line: _Line, table: _Table, *, below_only: bool = False) -> bool:
    if table.box is None:
        return False
    left, top, right, bottom = table.box
    x0, y0, x1, y1 = line.box
    overlap = min(right, x1) - max(left, x0)
    if overlap <= 0 or overlap / min(right - left, x1 - x0) < 0.5:
        return False
    if below_only:
        return bottom <= y0 <= bottom + _NOTE_GAP_POINTS
    return 0 <= top - y1 <= _CAPTION_GAP_POINTS or 0 <= y0 - bottom <= _CAPTION_GAP_POINTS


def _units(page: _Page) -> None:
    matches: dict[int, list[tuple[str, _Line]]] = {}
    for line in page.lines:
        match = _UNIT.fullmatch(_normalized(line.text))
        if match is None:
            continue
        candidates = [table for table in page.tables if _near(line, table)]
        if len(candidates) != 1:
            for table in candidates:
                table.unresolved.append("UNIT_TABLE_AMBIGUOUS")
            continue
        matches.setdefault(candidates[0].table_index, []).append((match.group(1).casefold(), line))
    for table in page.tables:
        found = matches.get(table.table_index, [])
        if "UNIT_TABLE_AMBIGUOUS" in table.unresolved:
            continue
        if len({unit for unit, _ in found}) > 1:
            table.unresolved.append("UNIT_CAPTION_AMBIGUOUS")
        elif found:
            table.add(
                "unit_block_orders",
                sorted({order for _, line in found for order in line.orders}),
                "UNIQUE_EXPLICIT_UNIT_CAPTION",
                [item for _, line in found for item in line.evidence],
            )
        else:
            table.unresolved.append("UNIT_CAPTION_UNRESOLVED")


def _footnotes(page: _Page) -> None:
    symbols: dict[int, dict[str, list[dict[str, object]]]] = {}
    for table in page.tables:
        found: dict[str, list[dict[str, object]]] = {}
        for cells in table.rows.values():
            for index, cell in cells:
                match = _CELL_MARKER.search(_text(cell.get("text")))
                if match:
                    found.setdefault(match.group(1), []).append(
                        _evidence(
                            f"{table.path}/cells/{index}", page.number, _box(cell.get("bbox"))
                        )
                    )
        symbols[table.table_index] = found
    notes: dict[tuple[int, str], list[_Line]] = {}
    for line in page.lines:
        match = _NOTE.search(line.text)
        if match is None:
            continue
        marker = match.group(1)
        candidates = [
            table
            for table in page.tables
            if marker in symbols[table.table_index] and _near(line, table, below_only=True)
        ]
        if len(candidates) > 1:
            for table in candidates:
                table.unresolved.append("FOOTNOTE_TABLE_AMBIGUOUS")
        elif candidates:
            notes.setdefault((candidates[0].table_index, marker), []).append(line)
    for table in page.tables:
        orders = set()
        evidence = []
        for marker, cell_evidence in symbols[table.table_index].items():
            matches = notes.get((table.table_index, marker), [])
            if len(matches) != 1:
                table.unresolved.append(
                    "FOOTNOTE_AMBIGUOUS" if matches else "FOOTNOTE_REFERENCE_UNRESOLVED"
                )
                continue
            if "FOOTNOTE_TABLE_AMBIGUOUS" in table.unresolved:
                continue
            orders.update(matches[0].orders)
            evidence.extend([*cell_evidence, *matches[0].evidence])
        if orders:
            table.add(
                "footnote_block_orders", sorted(orders), "MATCHING_SOURCE_FOOTNOTE_SYMBOL", evidence
            )


def _signature(table: _Table) -> tuple[tuple[int, str, float, float], ...] | None:
    if not table.headers or table.width is None:
        return None
    signatures = []
    for row in table.headers:
        signature = []
        for _, cell in table.rows[row]:
            box = _box(cell.get("bbox"))
            if box is None:
                return None
            signature.append(
                (
                    _integer(cell["column_index"]),
                    _header_label(_text(cell["text"])),
                    box[0] / table.width,
                    box[2] / table.width,
                )
            )
        signatures.append(tuple(signature))
    return signatures[0] if len(set(signatures)) == 1 else None


def _same_columns(left: _Table, right: _Table) -> bool:
    a, b = _signature(left), _signature(right)
    return (
        a is not None
        and b is not None
        and len(a) == len(b)
        and all(
            one[:2] == two[:2]
            and abs(one[2] - two[2]) <= _COLUMN_TOLERANCE
            and abs(one[3] - two[3]) <= _COLUMN_TOLERANCE
            for one, two in zip(a, b, strict=True)
        )
    )


def _titles(page: _Page) -> tuple[dict[str, set[str]], list[dict[str, object]]]:
    values: dict[str, set[str]] = {}
    evidence = []
    for line in page.lines:
        for kind, pattern in (("title", _TITLE), ("number", _CONTRACT_NUMBER)):
            match = pattern.fullmatch(_normalized(line.text))
            if match:
                values.setdefault(kind, set()).add(match.group(1))
                evidence.extend(line.evidence)
    return values, evidence


def _continuations(pages: Sequence[_Page]) -> None:
    by_number = {page.number: page for page in pages}
    for page in pages:
        previous = by_number.get(page.number - 1)
        if previous is None:
            continue
        if not page.available or not previous.available:
            for table in page.tables:
                table.unresolved.append("CONTINUATION_SOURCE_UNAVAILABLE")
            continue
        markers = [line for line in page.lines if _CONTINUED.fullmatch(_normalized(line.text))]
        current_titles, current_evidence = _titles(page)
        previous_titles, previous_evidence = _titles(previous)
        for table in page.tables:
            near = [
                line
                for line in markers
                if _near(line, table) and table.box is not None and line.box[3] <= table.box[1]
            ]
            if len(near) != 1:
                table.unresolved.append("CONTINUATION_MARKER_UNRESOLVED")
                continue
            if sum(_near(near[0], other) for other in page.tables) != 1:
                table.unresolved.append("CONTINUATION_TABLE_AMBIGUOUS")
                continue
            if (current_titles or previous_titles) and (
                any(len(values) != 1 for values in current_titles.values())
                or current_titles != previous_titles
            ):
                table.unresolved.append("CONTINUATION_CONTRACT_CONFLICT")
                continue
            candidates = [other for other in previous.tables if _same_columns(other, table)]
            if len(candidates) != 1:
                table.unresolved.append(
                    "CONTINUATION_COLUMNS_UNRESOLVED"
                    if not candidates
                    else "CONTINUATION_TABLE_AMBIGUOUS"
                )
                continue
            parent = candidates[0]
            table.add(
                "continuation_of",
                {"page_number": parent.page_number, "table_index": parent.table_index},
                "EXPLICIT_CONTINUATION_MATCHING_COLUMNS",
                [
                    *near[0].evidence,
                    *table.header_evidence,
                    *parent.header_evidence,
                    *current_evidence,
                    *previous_evidence,
                ],
            )


def annotate_extraction_layout(extraction: Mapping[str, object]) -> dict[str, object]:
    """Return source-preserving metadata for only unambiguous observed relations.

    Existing relation keys are never overwritten. ``layout_annotations`` records
    proposed values/reasons/source paths and unresolved conflicts; a proposal is
    not a USER_CONFIRMED assertion or a monetary/enrollment fact.
    """
    try:
        result = cast(dict[str, object], _copy(_mapping(extraction)))
        pages = _pages(result)
        for page in pages:
            if not page.available:
                for table in page.tables:
                    table.unresolved.append("SOURCE_PAGE_UNAVAILABLE")
                continue
            for table in page.tables:
                _headers(table)
            _units(page)
            _footnotes(page)
        _continuations(pages)
        for page in pages:
            for table in page.tables:
                metadata = dict(_mapping(table.raw.get("metadata_json", {})))
                for name, value in table.proposals.items():
                    if name in metadata and metadata[name] != value:
                        table.unresolved.append("EXISTING_METADATA_CONFLICT")
                    else:
                        metadata[name] = value
                metadata["layout_annotations"] = {
                    "schema_version": "layout-relations-v1",
                    "authority": "LAYOUT_RELATION_PROPOSAL",
                    "relations": table.relations,
                    "unresolved_codes": list(dict.fromkeys(table.unresolved)),
                }
                table.raw["metadata_json"] = metadata
        return result
    except TypeError, KeyError, OverflowError, RecursionError:
        raise DocumentLayoutError from None
