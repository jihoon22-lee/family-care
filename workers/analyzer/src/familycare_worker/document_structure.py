"""Lossless local extraction adapter and bounded, source-addressed chunk planning.

This module does no I/O or provider transmission. ``to_dict`` contains protected
source text and is for local persistence only, never logging or public telemetry.
Table relationships are accepted only when explicitly supplied in metadata:
``header_rows``, ``unit_block_orders``, ``footnote_block_orders`` and
``continuation_of = {page_number, table_index}``. Missing relationships remain
unresolved; column spans, units, enrollment and monetary facts are not inferred.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass, replace
from types import MappingProxyType
from typing import Literal, cast
from uuid import UUID

Role = Literal["policy", "terms", "amendment", "unknown", "ambiguous"]
Layer = Literal["native", "ocr", "unavailable"]
BBox = tuple[float, float, float, float]
_MAX_SOURCE_BYTES = 64 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DocumentStructureError(ValueError):
    def __init__(self) -> None:
        super().__init__("DOCUMENT_STRUCTURE_INVALID")


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise DocumentStructureError
    return cast(Mapping[str, object], value)


def _sequence(value: object) -> Sequence[object]:
    if not isinstance(value, (list, tuple)):
        raise DocumentStructureError
    return value


def _integer(value: object, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise DocumentStructureError
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise DocumentStructureError
    return value


def _uuid(value: object) -> UUID:
    try:
        parsed = value if isinstance(value, UUID) else UUID(_text(value))
        if parsed.int == 0:
            raise DocumentStructureError
        return parsed
    except ValueError, TypeError, AttributeError:
        raise DocumentStructureError from None


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in _mapping(value).items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise DocumentStructureError


def _serialize(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _serialize(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {key: _serialize(item) for key, item in _mapping(value).items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return value


def _digest(value: object) -> str:
    encoded = json.dumps(
        _serialize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > _MAX_SOURCE_BYTES:
        raise DocumentStructureError
    return hashlib.sha256(encoded).hexdigest()


def _bbox(value: object) -> BBox | None:
    if value is None:
        return None
    numbers = _sequence(value)
    if len(numbers) != 4 or any(
        isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item)
        for item in numbers
    ):
        raise DocumentStructureError
    x0, y0, x1, y1 = (float(cast(int | float, item)) for item in numbers)
    if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
        raise DocumentStructureError
    return x0, y0, x1, y1


@dataclass(frozen=True, repr=False)
class SourceLineage:
    document_version_id: UUID
    content_sha256: str
    extraction_id: UUID
    extraction_revision: str
    source_payload_sha256: str
    ocr_revision: str | None = None
    structure_version: str = "document-structure-v2"


@dataclass(frozen=True, repr=False)
class StructureCell:
    row_index: int
    column_index: int
    text: str
    bbox: BBox | None
    source_path: str
    row_span: int | None = None
    column_span: int | None = None


@dataclass(frozen=True)
class SourceTextSpan:
    block_node_id: str
    block_start: int
    block_end: int
    line_start: int
    line_end: int


@dataclass(frozen=True, repr=False)
class StructureNode:
    node_id: str
    kind: Literal["BLOCK", "TABLE_ROW", "TEXT_LINE"]
    page_number: int
    source_layer: Layer
    reading_order: int
    text: str
    source_path: str
    bbox: BBox | None
    table_id: str | None = None
    row_role: Literal["header", "data", "unresolved"] | None = None
    row_index: int | None = None
    cells: tuple[StructureCell, ...] = ()
    context_node_ids: tuple[str, ...] = ()
    continuation_of: str | None = None
    issue_codes: tuple[str, ...] = ()
    schedulable: bool = True
    source_spans: tuple[SourceTextSpan, ...] = ()


@dataclass(frozen=True, repr=False)
class StructurePage:
    page_number: int
    active_layer: Layer
    node_ids: tuple[str, ...]
    role: Role
    classification_codes: tuple[str, ...]
    printed_page_label: str | None = None


@dataclass(frozen=True, repr=False)
class DocumentComponent:
    component_id: str
    page_number: int
    role: Role
    node_ids: tuple[str, ...]
    # Page role detection is never an enrollment or contract identity decision.
    authority: str = "CONTENT_CLASSIFICATION_ONLY"


@dataclass(frozen=True, repr=False)
class UnprocessedRange:
    node_id: str | None
    page_number: int
    start: int
    end: int
    reason_code: str


@dataclass(frozen=True, repr=False)
class DocumentStructure:
    lineage: SourceLineage
    pages: tuple[StructurePage, ...]
    nodes: tuple[StructureNode, ...]
    components: tuple[DocumentComponent, ...]
    unresolved: tuple[UnprocessedRange, ...]
    source_extraction: Mapping[str, object]
    source_ocr_pages: tuple[Mapping[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], _serialize(self))


@dataclass(frozen=True, repr=False)
class StructureChunk:
    chunk_id: str
    node_id: str
    component_id: str
    page_number: int
    start: int
    end: int
    text: str
    context_node_ids: tuple[str, ...]
    context_text: str
    issue_codes: tuple[str, ...]


@dataclass(frozen=True, repr=False)
class ChunkPlan:
    lineage: SourceLineage
    chunks: tuple[StructureChunk, ...]
    unprocessed: tuple[UnprocessedRange, ...]
    total_characters: int
    processed_characters: int
    max_content_chars: int
    max_context_chars: int
    max_chunks: int

    @property
    def complete(self) -> bool:
        """All schedulable source was planned, not that a provider processed it."""
        return not self.unprocessed

    def to_dict(self) -> dict[str, object]:
        return {**cast(dict[str, object], _serialize(self)), "complete": self.complete}


@dataclass
class _Table:
    table_id: str
    page_number: int
    table_index: int
    rows: list[StructureNode]
    metadata: Mapping[str, object]
    blocks: Mapping[int, str]
    context_ids: list[str] = field(default_factory=list)
    issue_codes: list[str] = field(default_factory=list)
    continuation_of: str | None = None


def _classify(nodes: Sequence[StructureNode]) -> tuple[Role, tuple[str, ...]]:
    text = "\n".join(node.text for node in nodes).casefold()
    policy = ("보험증권" in text and "가입금액" in text) or (
        "policy certificate" in text and "sum assured" in text
    )
    terms = ("보험약관" in text and re.search(r"제\s*\d+\s*조", text) is not None) or (
        "policy terms" in text and "article 1" in text
    )
    amendment = "계약변경" in text and "적용일" in text
    checks: tuple[tuple[Role, bool], ...] = (
        ("policy", policy),
        ("terms", terms),
        ("amendment", amendment),
    )
    roles = [role for role, matched in checks if matched]
    if len(roles) > 1:
        return "ambiguous", ("COMPONENT_ROLE_CONFLICT",)
    if not roles:
        return "unknown", ("COMPONENT_ROLE_UNRESOLVED",)
    return roles[0], ("COMPONENT_CONTENT_MARKERS",)


def node_source_roles(structure: DocumentStructure) -> dict[str, Role]:
    """Carry a known policy table's role through explicit earlier-table relations.

    Page classification stays unchanged. Unknown surrounding blocks and explicit
    terms/amendment/conflict pages do not gain enrollment-source authority.
    """
    pages = {page.page_number: page.role for page in structure.pages}
    roles = {node.node_id: pages[node.page_number] for node in structure.nodes}
    tables: dict[str, list[StructureNode]] = {}
    for node in structure.nodes:
        if node.table_id is not None:
            tables.setdefault(node.table_id, []).append(node)
    for node in sorted(structure.nodes, key=lambda item: item.page_number):
        if (
            roles[node.node_id] != "unknown"
            or node.kind != "TABLE_ROW"
            or node.continuation_of is None
            or not node.schedulable
            or "CONTEXT_REFERENCE_UNRESOLVED" in node.issue_codes
        ):
            continue
        parents = tables.get(node.continuation_of, ())
        if parents and all(
            parent.page_number < node.page_number
            and parent.schedulable
            and roles[parent.node_id] == "policy"
            and "CONTEXT_REFERENCE_UNRESOLVED" not in parent.issue_codes
            for parent in parents
        ):
            roles[node.node_id] = "policy"
    return roles


def _source_identity(source: Mapping[str, object], document_version_id: UUID | None) -> UUID:
    identities = set()
    if document_version_id is not None:
        identities.add(_uuid(document_version_id))
    if source.get("document_version_id") is not None:
        identities.add(_uuid(source["document_version_id"]))
    for raw in _sequence(source.get("evidence", ())):
        evidence = _mapping(raw)
        identities.add(_uuid(evidence.get("document_version_id")))
        if evidence.get("content_sha256") != source.get("content_sha256"):
            raise DocumentStructureError
    if len(identities) != 1:
        raise DocumentStructureError
    return identities.pop()


def _page_numbers(pages: Sequence[object]) -> dict[int, tuple[int, Mapping[str, object]]]:
    result: dict[int, tuple[int, Mapping[str, object]]] = {}
    for position, raw in enumerate(pages):
        page = _mapping(raw)
        number = _integer(page.get("page_number"), minimum=1)
        if number > 500 or number in result:
            raise DocumentStructureError
        result[number] = position, page
    return result


def _inside(inner: BBox | None, outer: BBox | None) -> bool:
    return (
        inner is not None
        and outer is not None
        and (
            outer[0] <= inner[0] < inner[2] <= outer[2]
            and outer[1] <= inner[1] < inner[3] <= outer[3]
        )
    )


def _page_nodes(
    page: Mapping[str, object], path: str, number: int, layer: Layer, identity: str
) -> tuple[list[StructureNode], list[_Table]]:
    nodes: list[StructureNode] = []
    tables: list[_Table] = []
    block_ids: dict[int, str] = {}
    raw_tables = tuple(_mapping(item) for item in _sequence(page.get("tables", ())))
    boxes = tuple(_bbox(table.get("bbox")) for table in raw_tables)
    for position, raw in enumerate(_sequence(page.get("blocks", ()))):
        block = _mapping(raw)
        order = _integer(block.get("reading_order"))
        if order in block_ids:
            raise DocumentStructureError
        source_path = f"{path}/blocks/{position}"
        node_id = _digest((identity, source_path))
        box = _bbox(block.get("bbox"))
        node = StructureNode(
            node_id=node_id,
            kind="BLOCK",
            page_number=number,
            source_layer=layer,
            reading_order=order,
            text=_text(block.get("text")),
            source_path=source_path,
            bbox=box,
            schedulable=not any(
                _inside(box, _bbox(_mapping(raw_cell).get("bbox")))
                and bool(_text(block.get("text")))
                and _text(block.get("text")) in _text(_mapping(raw_cell).get("text"))
                for table in raw_tables
                for raw_cell in _sequence(table.get("cells", ()))
            ),
        )
        block_ids[order] = node_id
        nodes.append(node)
    nodes.sort(key=lambda item: item.reading_order)
    for table_index, table in enumerate(raw_tables):
        table_path = f"{path}/tables/{table_index}"
        table_id = _digest((identity, table_path))
        rows: dict[int, list[StructureCell]] = {}
        seen: set[tuple[int, int]] = set()
        for position, raw in enumerate(_sequence(table.get("cells", ()))):
            cell = _mapping(raw)
            row_index = _integer(cell.get("row_index"))
            column_index = _integer(cell.get("column_index"))
            key = row_index, column_index
            if key in seen:
                raise DocumentStructureError
            seen.add(key)
            rows.setdefault(row_index, []).append(
                StructureCell(
                    row_index=row_index,
                    column_index=column_index,
                    text=_text(cell.get("text")),
                    bbox=_bbox(cell.get("bbox")),
                    source_path=f"{table_path}/cells/{position}",
                    row_span=(
                        _integer(cell["row_span"], minimum=1) if "row_span" in cell else None
                    ),
                    column_span=(
                        _integer(cell["column_span"], minimum=1) if "column_span" in cell else None
                    ),
                )
            )
        row_nodes = []
        for row_index, cells in sorted(rows.items()):
            cells.sort(key=lambda item: item.column_index)
            row_nodes.append(
                StructureNode(
                    node_id=_digest((identity, table_path, row_index)),
                    kind="TABLE_ROW",
                    page_number=number,
                    source_layer=layer,
                    reading_order=row_index,
                    text="\t".join(cell.text for cell in cells),
                    source_path=table_path,
                    bbox=boxes[table_index],
                    table_id=table_id,
                    row_index=row_index,
                    cells=tuple(cells),
                )
            )
        metadata = _mapping(table.get("metadata_json", {}))
        tables.append(_Table(table_id, number, table_index, row_nodes, metadata, block_ids))
        nodes.extend(row_nodes)
    return nodes, tables


def _derive_text_lines(nodes: list[StructureNode], identity: str) -> list[StructureNode]:
    """Build lossless views only from consecutive, aligned, closely spaced words."""
    groups: list[list[StructureNode]] = []
    words: list[StructureNode] = []
    current: list[StructureNode] = []
    table_boxes = tuple(node.bbox for node in nodes if node.kind == "TABLE_ROW")

    def flush() -> None:
        if len(current) > 1:
            groups.append(list(current))
        current.clear()

    for node in nodes:
        box = node.bbox
        if (
            node.kind != "BLOCK"
            or not node.schedulable
            or box is None
            or not node.text
            or any(character.isspace() for character in node.text)
            or any(_inside(box, table) for table in table_boxes)
        ):
            flush()
            continue
        if current:
            previous = current[-1]
            first_box = current[0].bbox
            previous_box = previous.bbox
            assert first_box is not None and previous_box is not None
            height = box[3] - box[1]
            first_height = first_box[3] - first_box[1]
            overlap = min(box[3], first_box[3]) - max(box[1], first_box[1])
            gap = box[0] - previous_box[2]
            if (
                node.source_layer != previous.source_layer
                or node.reading_order != previous.reading_order + 1
                or overlap < 0.8 * max(height, first_height)
                or not 0 <= gap <= 1.5 * min(height, first_height)
            ):
                flush()
        words.append(node)
        current.append(node)
    flush()
    positioned = sorted(words, key=lambda node: cast(BBox, node.bbox)[1])
    tops = [cast(BBox, node.bbox)[1] for node in positioned]
    replacements: dict[str, StructureNode] = {}
    views: dict[str, StructureNode] = {}
    for group in groups:
        offset = 0
        spans = []
        for node in group:
            spans.append(
                SourceTextSpan(node.node_id, 0, len(node.text), offset, offset + len(node.text))
            )
            offset += len(node.text) + 1
            replacements[node.node_id] = replace(node, schedulable=False)
        boxes = [node.bbox for node in group if node.bbox is not None]
        group_ids = {node.node_id for node in group}
        height = boxes[0][3] - boxes[0][1]
        nearby = positioned[
            bisect_left(tops, boxes[0][1] - height / 4) : bisect_right(
                tops, boxes[0][1] + height / 4
            )
        ]
        ambiguous_column = any(
            node.node_id not in group_ids
            and node.source_layer == group[0].source_layer
            and (box := node.bbox) is not None
            and min(box[3], boxes[0][3]) - max(box[1], boxes[0][1])
            >= 0.8 * max(height, box[3] - box[1])
            for node in nearby
        )
        line_id = _digest((identity, "text-line-v1", tuple(node.node_id for node in group)))
        views[group[0].node_id] = StructureNode(
            node_id=line_id,
            kind="TEXT_LINE",
            page_number=group[0].page_number,
            source_layer=group[0].source_layer,
            reading_order=group[0].reading_order,
            text=" ".join(node.text for node in group),
            source_path=f"/derived/text-lines/{line_id}",
            bbox=(
                min(box[0] for box in boxes),
                min(box[1] for box in boxes),
                max(box[2] for box in boxes),
                max(box[3] for box in boxes),
            ),
            source_spans=tuple(spans),
            issue_codes=("LINE_COLUMN_CONTEXT_UNRESOLVED",) if ambiguous_column else (),
        )
    result = []
    for node in nodes:
        result.append(replacements.get(node.node_id, node))
        if node.node_id in views:
            result.append(views[node.node_id])
    return result


def _resolve_table_context(tables: Sequence[_Table]) -> dict[str, StructureNode]:
    by_address = {(table.page_number, table.table_index): table for table in tables}
    replaced: dict[str, StructureNode] = {}
    for table in tables:
        metadata = table.metadata
        if not any(
            key in metadata
            for key in (
                "header_rows",
                "unit_block_orders",
                "footnote_block_orders",
                "continuation_of",
            )
        ):
            table.issue_codes.append("TABLE_CONTEXT_UNRESOLVED")
        row_ids = {row.row_index: row.node_id for row in table.rows}
        for key, lookup in (
            ("header_rows", row_ids),
            ("unit_block_orders", table.blocks),
            ("footnote_block_orders", table.blocks),
        ):
            for reference in _sequence(metadata.get(key, ())):
                index = _integer(reference)
                if index not in lookup:
                    table.issue_codes.append("CONTEXT_REFERENCE_UNRESOLVED")
                else:
                    table.context_ids.append(lookup[index])
        continuation = metadata.get("continuation_of")
        if continuation is not None:
            reference = _mapping(continuation)
            address = (
                _integer(reference.get("page_number"), minimum=1),
                _integer(reference.get("table_index")),
            )
            parent = by_address.get(address)
            if parent is None or address >= (table.page_number, table.table_index):
                table.issue_codes.append("CONTEXT_REFERENCE_UNRESOLVED")
            else:
                table.continuation_of = parent.table_id
                table.context_ids.extend(parent.context_ids)
                table.issue_codes.extend(parent.issue_codes)
        for row in table.rows:
            replaced[row.node_id] = replace(
                row,
                context_node_ids=tuple(
                    dict.fromkeys(key for key in table.context_ids if key != row.node_id)
                ),
                continuation_of=table.continuation_of,
                row_role=(
                    "header"
                    if row.row_index in _sequence(metadata.get("header_rows", ()))
                    else "data"
                    if "header_rows" in metadata or table.continuation_of is not None
                    else "unresolved"
                ),
                issue_codes=tuple(dict.fromkeys(table.issue_codes)),
            )
    return replaced


def build_document_structure(
    extraction: Mapping[str, object],
    *,
    extraction_id: UUID,
    extraction_revision: str,
    document_version_id: UUID | None = None,
    ocr_pages: Sequence[Mapping[str, object]] = (),
    ocr_revision: str | None = None,
) -> DocumentStructure:
    """Adapt full extraction JSON; preserve source layers and explicit relationships.

    Native ``ExtractionResult`` carries document identity in its Evidence list;
    callers may alternatively supply ``document_version_id`` explicitly. OCR
    inputs must be serialized pages from the same extraction, with a revision.
    Neither source text nor document roles authorize publication or transmission.
    """
    try:
        source = _mapping(_freeze(extraction))
        frozen_ocr = tuple(_mapping(_freeze(item)) for item in ocr_pages)
        content_sha = _text(source.get("content_sha256"))
        if _SHA256.fullmatch(content_sha) is None:
            raise DocumentStructureError
        if not extraction_revision or len(extraction_revision) > 160:
            raise DocumentStructureError
        if frozen_ocr and (not ocr_revision or len(ocr_revision) > 160):
            raise DocumentStructureError
        lineage = SourceLineage(
            document_version_id=_source_identity(source, document_version_id),
            content_sha256=content_sha,
            extraction_id=_uuid(extraction_id),
            extraction_revision=extraction_revision,
            source_payload_sha256=_digest((source, frozen_ocr)),
            ocr_revision=ocr_revision,
        )
        identity = _digest(lineage)
        native = _page_numbers(_sequence(source.get("pages")))
        overlays = _page_numbers(frozen_ocr)
        if not native or not overlays.keys() <= native.keys():
            raise DocumentStructureError
        for number, (_, overlay_page) in overlays.items():
            if "evidence" in overlay_page:
                evidence = _mapping(overlay_page["evidence"])
                if (
                    _uuid(evidence.get("document_version_id")) != lineage.document_version_id
                    or evidence.get("content_sha256") != lineage.content_sha256
                    or evidence.get("page_number") != number
                ):
                    raise DocumentStructureError
        page_count = _integer(source.get("page_count", max(native)), minimum=1)
        if page_count > 500 or max(native) > page_count:
            raise DocumentStructureError
        pages: list[StructurePage] = []
        nodes: list[StructureNode] = []
        tables: list[_Table] = []
        components: list[DocumentComponent] = []
        unresolved: list[UnprocessedRange] = []
        for number in range(1, page_count + 1):
            if number not in native:
                unresolved.append(UnprocessedRange(None, number, 0, 0, "PAGE_MISSING"))
                pages.append(
                    StructurePage(
                        number, "unavailable", (), "unknown", ("COMPONENT_ROLE_UNRESOLVED",)
                    )
                )
                components.append(
                    DocumentComponent(
                        _digest((identity, "component", number)), number, "unknown", ()
                    )
                )
                continue
            position, page = native[number]
            layer: Layer = "native"
            selected = page
            path = f"/pages/{position}"
            failure = None
            if page.get("status") in {"failed", "cancelled"}:
                failure = "PAGE_UNAVAILABLE"
            elif _mapping(page.get("quality", {})).get("classification") == "OCR_REQUIRED":
                overlay = overlays.get(number)
                if overlay is None or overlay[1].get("status") not in {"completed", "warning"}:
                    failure = "OCR_REQUIRED"
                elif not _sequence(overlay[1].get("blocks", ())):
                    failure = "OCR_NO_TEXT"
                else:
                    selected = overlay[1]
                    layer = "ocr"
                    path = f"/ocr_pages/{overlay[0]}"
            page_nodes: list[StructureNode]
            page_tables: list[_Table]
            if failure is not None:
                layer = "unavailable"
                page_nodes, page_tables = [], []
                unresolved.append(UnprocessedRange(None, number, 0, 0, failure))
            else:
                page_nodes, page_tables = _page_nodes(selected, path, number, layer, identity)
                page_nodes = _derive_text_lines(page_nodes, identity)
            role, codes = _classify(page_nodes)
            label = page.get("printed_page_label")
            if label is not None:
                label = _text(label)
            page_node_ids = tuple(node.node_id for node in page_nodes)
            pages.append(StructurePage(number, layer, page_node_ids, role, codes, label))
            components.append(
                DocumentComponent(
                    _digest((identity, "component", number)), number, role, page_node_ids
                )
            )
            nodes.extend(page_nodes)
            tables.extend(page_tables)
        resolved = _resolve_table_context(tables)
        return DocumentStructure(
            lineage,
            tuple(pages),
            tuple(resolved.get(node.node_id, node) for node in nodes),
            tuple(components),
            tuple(unresolved),
            source,
            frozen_ocr,
        )
    except TypeError, KeyError, OverflowError, RecursionError:
        raise DocumentStructureError from None


def plan_structure_chunks(
    structure: DocumentStructure,
    *,
    max_content_chars: int,
    max_context_chars: int,
    max_chunks: int,
) -> ChunkPlan:
    """Partition active source into disjoint content ranges plus explicit omissions.

    Context may repeat across chunks; it never creates another primary row.
    Table rows remain atomic. Text blocks split without discarding whitespace.
    Planning completion is not extraction quality or provider processing success.
    """
    _integer(max_content_chars, minimum=1)
    _integer(max_context_chars)
    _integer(max_chunks)
    by_id = {node.node_id: node for node in structure.nodes}
    components = {item.page_number: item.component_id for item in structure.components}
    chunks: list[StructureChunk] = []
    unprocessed = list(structure.unresolved)
    total = 0
    processed = 0
    for node in structure.nodes:
        if not node.schedulable or not node.text:
            continue
        total += len(node.text)
        context = "\n".join(by_id[key].text for key in node.context_node_ids)
        reason = None
        if "CONTEXT_REFERENCE_UNRESOLVED" in node.issue_codes:
            reason = "CONTEXT_REFERENCE_UNRESOLVED"
        elif "LINE_COLUMN_CONTEXT_UNRESOLVED" in node.issue_codes:
            reason = "LINE_COLUMN_CONTEXT_UNRESOLVED"
        elif node.kind in {"TABLE_ROW", "TEXT_LINE"} and len(node.text) > max_content_chars:
            reason = (
                "ROW_EXCEEDS_CONTENT_BUDGET"
                if node.kind == "TABLE_ROW"
                else "LINE_EXCEEDS_CONTENT_BUDGET"
            )
        elif len(context) > max_context_chars:
            reason = "CONTEXT_EXCEEDS_BUDGET"
        if reason is not None:
            unprocessed.append(
                UnprocessedRange(node.node_id, node.page_number, 0, len(node.text), reason)
            )
            continue
        start = 0
        while start < len(node.text):
            if len(chunks) >= max_chunks:
                unprocessed.append(
                    UnprocessedRange(
                        node.node_id,
                        node.page_number,
                        start,
                        len(node.text),
                        "CHUNK_BUDGET_EXHAUSTED",
                    )
                )
                break
            end = min(start + max_content_chars, len(node.text))
            chunk_id = _digest((structure.lineage, node.node_id, start, end, node.context_node_ids))
            chunks.append(
                StructureChunk(
                    chunk_id,
                    node.node_id,
                    components[node.page_number],
                    node.page_number,
                    start,
                    end,
                    node.text[start:end],
                    node.context_node_ids,
                    context,
                    node.issue_codes,
                )
            )
            processed += end - start
            start = end
    return ChunkPlan(
        structure.lineage,
        tuple(chunks),
        tuple(unprocessed),
        total,
        processed,
        max_content_chars,
        max_context_chars,
        max_chunks,
    )
