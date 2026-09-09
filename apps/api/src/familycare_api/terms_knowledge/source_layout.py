"""Bounded original semantic regions; source layout never proves benefit meaning.

The caller verifies household, edition and immutable generation ownership and
supplies independently known component bounds. No excerpt can certify coverage.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from familycare_api.clauses.source_regions import ClauseSourceSpan, _normative_reference
from familycare_api.insurance_documents.terms_body_validation import (
    _DOCUMENT_REFERENCE,
    _REFERENCE,
    _box,
    _lineage_valid,
    _reference_text,
)

RegionKind = Literal["article", "appendix", "footnote", "unresolved"]
_HEX = re.compile(r"^[0-9a-f]{64}$")
_ADDRESS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ARTICLE = re.compile(
    r"^(?P<label>제\s*[1-9][0-9]{0,3}\s*조|Article\s+[1-9][0-9]{0,3})"
    r"(?:\s*[(（][^()（）\n]{1,160}[)）])?$",
    re.I,
)
_APPENDIX = re.compile(
    r"^(?P<label>별표\s*[1-9][0-9]{0,3}|Appendix\s+[A-Za-z0-9]{1,16})"
    r"(?:\s*[(（][^()（）\n]{1,160}[)）])?$",
    re.I,
)
_FOOTNOTE = re.compile(
    r"^(?P<label>(?:주(?:석)?|각주)\s*[1-9][0-9]{0,3}|Footnote\s+[1-9][0-9]{0,3})"
    r"(?:(?:\s*[:：]\s*)(?P<body>.+)|\s*[(（][^()（）\n]{1,160}[)）])?$",
    re.I,
)
_UNKNOWN_HEADING = re.compile(
    r"^(?:제\s*[1-9][0-9]{0,3}\s*[조장절관]|별표\s*[1-9]|"
    r"(?:Article|Chapter|Section|Appendix|Footnote)\s+|(?:주(?:석)?|각주)\s*[:：]|[※*†‡])",
    re.I,
)
_EXAMPLE_END = re.compile(
    r"^(?:(?:예시|예문|예제)\s*(?:끝|종료)|End\s+of\s+(?:example|illustration))$", re.I
)
_TERMS_TITLE = re.compile(r"^(?:보험\s*약관|약관|Policy\s+terms)$", re.I)


@dataclass(frozen=True, slots=True, repr=False)
class SemanticTableRow:
    """Original table provenance retained only after row/header validation."""

    node_id: str
    row_role: Literal["header", "data"]
    column_indices: tuple[int, ...]
    header_node_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True, repr=False)
class SemanticSourceRegion:
    region_id: str
    label: str
    kind: RegionKind
    heading: ClauseSourceSpan | None
    spans: tuple[ClauseSourceSpan, ...]
    complete: bool
    reason_codes: tuple[str, ...] = ()
    table_rows: tuple[SemanticTableRow, ...] = ()

    @property
    def body_spans(self) -> tuple[ClauseSourceSpan, ...]:
        return tuple(span for span in self.spans if span != self.heading)

    @property
    def body_text(self) -> str:
        return "\n".join(span.text for span in self.body_spans)


@dataclass(frozen=True, slots=True, repr=False)
class SemanticSourceLayout:
    regions: tuple[SemanticSourceRegion, ...]
    expected_region_ids: tuple[str, ...]
    complete: bool
    reason_codes: tuple[str, ...] = ()


@dataclass(repr=False)
class _Draft:
    ordinal: int
    label: str
    kind: RegionKind
    heading: ClauseSourceSpan | None
    spans: list[ClauseSourceSpan] = field(default_factory=list)
    complete: bool = True
    reasons: set[str] = field(default_factory=set)


class _InvalidSource(ValueError):
    pass


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _failure(reason: str) -> SemanticSourceLayout:
    region = SemanticSourceRegion(
        "region-" + _digest(reason), "", "unresolved", None, (), False, (reason,)
    )
    return SemanticSourceLayout((region,), (region.region_id,), False, (reason,))


def _inside(inner: tuple[float, ...], outer: tuple[float, ...]) -> bool:
    return (
        outer[0] <= inner[0] < inner[2] <= outer[2] and outer[1] <= inner[1] < inner[3] <= outer[3]
    )


def _footprint(node: dict[str, Any]) -> tuple[float, ...]:
    if node["kind"] != "TABLE_ROW":
        return _box(node)
    boxes = [_box(cell) for cell in node["cells"]]
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _table_proof(node: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> None:
    cells = node["cells"]
    if (
        not isinstance(node.get("table_id"), str)
        or not node["table_id"]
        or type(node.get("row_index")) is not int
        or node["row_index"] < 0
        or node.get("row_role") not in {"header", "data"}
        or not isinstance(cells, (list, tuple))
        or not 1 <= len(cells) <= 64
        or node["text"] != "\t".join(cell["text"] for cell in cells)
    ):
        raise _InvalidSource("SEMANTIC_TABLE_SOURCE_UNRESOLVED")
    previous = None
    for cell in cells:
        if (
            cell["row_index"] != node["row_index"]
            or type(cell["row_index"]) is not int
            or type(cell["column_index"]) is not int
            or cell["column_index"] < 0
            or cell.get("row_span") not in (None, 1)
            or cell.get("column_span") not in (None, 1)
            or (cell.get("row_span") is not None and type(cell["row_span"]) is not int)
            or (cell.get("column_span") is not None and type(cell["column_span"]) is not int)
            or not _inside(_box(cell), _box(node))
            or (
                previous is not None
                and (
                    cell["column_index"] != previous["column_index"] + 1
                    or _box(cell)[0] < _box(previous)[2]
                )
            )
        ):
            raise _InvalidSource("SEMANTIC_TABLE_SOURCE_UNRESOLVED")
        previous = cell
    contexts = node.get("context_node_ids", [])
    if len(set(contexts)) != len(contexts):
        raise _InvalidSource("SEMANTIC_TABLE_CONTEXT_UNRESOLVED")
    headers = []
    for identifier in contexts:
        context = by_id.get(identifier)
        if (
            context is None
            or context["kind"] != "TABLE_ROW"
            or context.get("row_role") != "header"
            or context["source_layer"] != node["source_layer"]
            or not (
                (
                    context["table_id"] == node["table_id"]
                    and context["page_number"] == node["page_number"]
                    and context["row_index"] < node["row_index"]
                    and _footprint(context)[3] <= _footprint(node)[1]
                )
                or (
                    context["table_id"] == node.get("continuation_of")
                    and context["page_number"] < node["page_number"]
                )
            )
        ):
            raise _InvalidSource("SEMANTIC_TABLE_CONTEXT_UNRESOLVED")
        headers.append(context)
    if node["row_role"] == "data" and not headers:
        raise _InvalidSource("SEMANTIC_TABLE_CONTEXT_UNRESOLVED")


def _validated_nodes(
    source: Mapping[str, Any], start: int, end: int, *, metadata_revision: str
) -> list[dict[str, Any]]:
    pages = source.get("pages")
    unresolved = source.get("unresolved")
    if not isinstance(pages, (list, tuple)) or not isinstance(unresolved, (list, tuple)):
        raise _InvalidSource("SEMANTIC_PAGE_MANIFEST_UNVERIFIED")
    requested = list(range(start, end + 1))
    selected_pages = [p for p in pages if start <= p["page_number"] <= end]
    if sorted(p["page_number"] for p in selected_pages) != requested:
        raise _InvalidSource("SEMANTIC_COMPONENT_COVERAGE_INCOMPLETE")
    if any(start <= item["page_number"] <= end for item in unresolved):
        raise _InvalidSource("SEMANTIC_COMPONENT_COVERAGE_INCOMPLETE")
    nodes = [n for n in source["nodes"] if start <= n["page_number"] <= end]
    if not nodes or len(nodes) > 65536:
        raise _InvalidSource("SEMANTIC_SOURCE_BUDGET_EXCEEDED")
    by_id = {n["node_id"]: n for n in nodes}
    addresses = {
        (n["kind"], n["source_path"], n.get("row_index") if n["kind"] == "TABLE_ROW" else None)
        for n in nodes
    }
    if len(by_id) != len(nodes) or len(addresses) != len(nodes):
        raise _InvalidSource("SEMANTIC_SOURCE_ADDRESS_AMBIGUOUS")
    for page in selected_pages:
        local = [n for n in nodes if n["page_number"] == page["page_number"]]
        if (
            type(page["page_number"]) is not int
            or page["active_layer"] not in {"native", "ocr"}
            or len(local) > 4096
            or sum(len(n["text"]) for n in local) > 262144
            or sum(len(n["text"].splitlines()) for n in local) > 4096
            or len(set(page["node_ids"])) != len(page["node_ids"])
            or set(page["node_ids"]) != {n["node_id"] for n in local}
            or any(n["source_layer"] != page["active_layer"] for n in local)
        ):
            raise _InvalidSource("SEMANTIC_PAGE_MANIFEST_UNVERIFIED")
    represented: set[str] = set()
    for node in nodes:
        if (
            node["kind"] not in {"BLOCK", "TEXT_LINE", "TABLE_ROW"}
            or not isinstance(node["node_id"], str)
            or not _ADDRESS.fullmatch(node["node_id"])
            or not isinstance(node["source_path"], str)
            or not node["source_path"]
            or type(node["page_number"]) is not int
            or type(node["reading_order"]) is not int
            or node["reading_order"] < 0
            or type(node["schedulable"]) is not bool
            or node.get("issue_codes")
        ):
            raise _InvalidSource("SEMANTIC_SOURCE_LAYOUT_UNRESOLVED")
        _box(node)
        if node["kind"] == "TABLE_ROW":
            _table_proof(node, by_id)
        elif node["kind"] == "TEXT_LINE":
            spans = node.get("source_spans", [])
            if (
                len(node["text"].splitlines()) != 1
                or not 1 <= len(spans) <= 4096
                or any(
                    type(s[k]) is not int
                    for s in spans
                    for k in ("block_start", "block_end", "line_start", "line_end")
                )
                or not _lineage_valid(node, by_id, metadata_revision=metadata_revision)
            ):
                raise _InvalidSource("SEMANTIC_SOURCE_LINEAGE_INVALID")
            for span in spans:
                raw = by_id[span["block_node_id"]]
                if (
                    raw["node_id"] in represented
                    or raw["text"][: span["block_start"]].strip()
                    or raw["text"][span["block_end"] :].strip()
                ):
                    raise _InvalidSource("SEMANTIC_SOURCE_LINEAGE_INVALID")
                represented.add(raw["node_id"])
    tables = [node for node in nodes if node["kind"] == "TABLE_ROW"]
    for node in nodes:
        if node["node_id"] in represented or node["schedulable"]:
            continue
        matches = [
            cell
            for table in tables
            if node["kind"] == "BLOCK"
            and node["page_number"] == table["page_number"]
            and node["source_layer"] == table["source_layer"]
            for cell in table["cells"]
            if node["text"] and node["text"] in cell["text"] and _inside(_box(node), _box(cell))
        ]
        if len(matches) != 1:
            raise _InvalidSource("SEMANTIC_SOURCE_LINEAGE_INVALID")
        represented.add(node["node_id"])
    ordered = []
    for page in requested:
        local = sorted(
            (
                n
                for n in nodes
                if n["page_number"] == page
                and n["node_id"] not in represented
                and n["text"].strip()
            ),
            key=lambda n: (_footprint(n)[1], _footprint(n)[0]),
        )
        native = [n for n in local if n["kind"] != "TABLE_ROW"]
        if any(
            b["reading_order"] <= a["reading_order"]
            for a, b in zip(native, native[1:], strict=False)
        ):
            raise _InvalidSource("SEMANTIC_SOURCE_ORDER_UNRESOLVED")
        table_positions: dict[str, int] = {}
        for node in local:
            if node["kind"] == "TABLE_ROW":
                if node["row_index"] <= table_positions.get(node["table_id"], -1):
                    raise _InvalidSource("SEMANTIC_SOURCE_ORDER_UNRESOLVED")
                table_positions[node["table_id"]] = node["row_index"]
        for left, right in zip(local, local[1:], strict=False):
            a, b = _footprint(left), _footprint(right)
            if b[1] < a[3] or min(a[2], b[2]) - max(a[0], b[0]) < 0.5 * min(
                a[2] - a[0], b[2] - b[0]
            ):
                raise _InvalidSource("SEMANTIC_SOURCE_ORDER_UNRESOLVED")
        ordered.extend(local)
    return ordered


def _source_span(node: dict[str, Any], start: int, end: int) -> ClauseSourceSpan:
    box = _box(node)
    return ClauseSourceSpan(
        node["node_id"],
        node["page_number"],
        start,
        end,
        node["text"][start:end],
        node["source_layer"],
        (box[0], box[1], box[2], box[3]),
    )


def _has_footnote_reference(label: str, spans: list[ClauseSourceSpan]) -> bool:
    pattern = (
        r"(?<![A-Za-z0-9가-힣])"
        + r"\s*".join(re.escape(part) for part in label.split())
        + r"(?![0-9])"
    )
    return any(re.search(pattern, span.text, re.I) for span in spans)


def _parse(nodes: list[dict[str, Any]]) -> list[_Draft]:
    drafts: list[_Draft] = []
    current: _Draft | None = None
    example: _Draft | None = None
    blocked_document = False
    last_article = 0
    hierarchy_unresolved = False
    ordinal = 0

    def finish() -> None:
        if (
            current is not None
            and current.kind != "unresolved"
            and not any(s != current.heading for s in current.spans)
        ):
            current.complete = False
            current.reasons.add("SEMANTIC_SOURCE_BODY_MISSING")

    for node in nodes:
        offset = 0
        for raw in node["text"].splitlines(keepends=True):
            text = raw.strip()
            start = offset + len(raw) - len(raw.lstrip())
            offset += len(raw)
            if not text:
                continue
            span = _source_span(node, start, start + len(text))
            ordinal += 1
            normalized = _reference_text(text)
            if example is not None:
                example.spans.append(span)
                if not blocked_document and _EXAMPLE_END.fullmatch(normalized):
                    example.complete = True
                    example.reasons = {"LOCAL_EXAMPLE_EXCLUDED"}
                    example = None
                continue
            if _REFERENCE.search(normalized):
                blocked_document = current is None or bool(_DOCUMENT_REFERENCE.search(normalized))
                example = _Draft(
                    ordinal,
                    text,
                    "unresolved",
                    span,
                    [span],
                    False,
                    {"LOCAL_EXAMPLE_BOUNDARY_UNRESOLVED"},
                )
                drafts.append(example)
                if blocked_document and current is not None:
                    current.complete = False
                    current.reasons.add("SEMANTIC_DOCUMENT_BOUNDARY_UNRESOLVED")
                continue
            found_article, found_appendix, found_note = (
                _ARTICLE.fullmatch(text),
                _APPENDIX.fullmatch(text),
                _FOOTNOTE.fullmatch(text),
            )
            if found_article or found_appendix or found_note:
                matched = found_article or found_appendix or found_note
                assert matched is not None
                label = matched["label"]
                kind: RegionKind = (
                    "article" if found_article else "appendix" if found_appendix else "footnote"
                )
                parent = current
                finish()
                heading = (
                    span
                    if kind != "footnote"
                    else _source_span(node, start, start + matched.end("label"))
                )
                current = _Draft(ordinal, label, kind, heading, [heading])
                drafts.append(current)
                if hierarchy_unresolved:
                    current.complete = False
                    current.reasons.add("SEMANTIC_SOURCE_HIERARCHY_UNRESOLVED")
                if found_article:
                    number_match = re.search(r"[0-9]+", label)
                    assert number_match is not None
                    number = int(number_match[0])
                    if number <= last_article:
                        current.complete = False
                        current.reasons.add("SEMANTIC_SOURCE_HEADING_AMBIGUOUS")
                        if parent is not None:
                            parent.complete = False
                            parent.reasons.add("SEMANTIC_SOURCE_HEADING_AMBIGUOUS")
                    last_article = number
                if found_note:
                    if (
                        parent is None
                        or parent.kind not in {"article", "appendix"}
                        or not _has_footnote_reference(label, parent.spans)
                    ):
                        current.complete = False
                        current.reasons.add("SEMANTIC_FOOTNOTE_REFERENCE_UNRESOLVED")
                        if parent is not None:
                            parent.complete = False
                            parent.reasons.add("SEMANTIC_FOOTNOTE_REFERENCE_UNRESOLVED")
                    if matched["body"] is not None:
                        left = start + matched.start("body")
                        current.spans.append(_source_span(node, left, start + matched.end("body")))
                continue
            if _UNKNOWN_HEADING.match(text) and (
                re.match(r"^(?:[※*†‡]|(?:주(?:석)?|각주)\s*[:：])", text)
                or not _normative_reference(text)
            ):
                if current is not None:
                    current.complete = False
                    current.reasons.add("SEMANTIC_SOURCE_HIERARCHY_UNRESOLVED")
                finish()
                hierarchy_unresolved = True
                current = _Draft(
                    ordinal,
                    text,
                    "unresolved",
                    span,
                    [span],
                    False,
                    {"SEMANTIC_SOURCE_HEADING_UNSUPPORTED"},
                )
                drafts.append(current)
                continue
            if current is None:
                is_title = bool(_TERMS_TITLE.fullmatch(normalized))
                current = _Draft(
                    ordinal,
                    text,
                    "unresolved",
                    span,
                    [span],
                    is_title,
                    {"SEMANTIC_TERMS_TITLE_CONTEXT" if is_title else "SEMANTIC_SOURCE_ORPHAN_TEXT"},
                )
                drafts.append(current)
                if is_title:
                    # A proved title accounts for only that original line, never
                    # arbitrary cover prose that happens to follow it.
                    current = None
            else:
                current.spans.append(span)
    if example is not None and current is not None:
        current.complete = False
        current.reasons.add("LOCAL_EXAMPLE_BOUNDARY_UNRESOLVED")
    finish()
    return drafts


def observe_semantic_regions(
    projection: Mapping[str, object],
    *,
    component_page_start: int,
    component_page_end: int,
    metadata_revision: str = "document-metadata-v8",
) -> SemanticSourceLayout:
    """Derive original regions only from a complete independently selected component."""
    try:
        if (
            type(component_page_start) is not int
            or type(component_page_end) is not int
            or not 1 <= component_page_start <= component_page_end <= 500
        ):
            raise _InvalidSource("SEMANTIC_COMPONENT_BOUNDS_INVALID")
        lineage: Any = projection["lineage"]
        for name in ("document_version_id", "extraction_id"):
            UUID(lineage[name])
        for name in ("content_sha256", "source_payload_sha256"):
            if not isinstance(lineage[name], str) or not _HEX.fullmatch(lineage[name]):
                raise _InvalidSource("SEMANTIC_SOURCE_IDENTITY_INVALID")
        if not lineage["extraction_revision"] or not lineage["structure_version"]:
            raise _InvalidSource("SEMANTIC_SOURCE_IDENTITY_INVALID")
        nodes = _validated_nodes(
            projection,
            component_page_start,
            component_page_end,
            metadata_revision=metadata_revision,
        )
        drafts = _parse(nodes)
        table_rows = {
            node["node_id"]: SemanticTableRow(
                node["node_id"],
                node["row_role"],
                tuple(cell["column_index"] for cell in node["cells"]),
                tuple(node.get("context_node_ids", [])),
            )
            for node in nodes
            if node["kind"] == "TABLE_ROW"
        }
        if not drafts or len(drafts) > 4096:
            raise _InvalidSource("SEMANTIC_SOURCE_REGIONS_UNRESOLVED")
        regions = tuple(
            SemanticSourceRegion(
                "region-"
                + _digest(
                    [
                        lineage,
                        component_page_start,
                        component_page_end,
                        draft.kind,
                        draft.label,
                        [
                            [s.node_id, s.page_number, s.start, s.end, s.source_layer, s.bbox]
                            for s in draft.spans
                        ],
                    ]
                ),
                draft.label,
                draft.kind,
                draft.heading,
                tuple(draft.spans),
                draft.complete,
                tuple(sorted(draft.reasons)),
                tuple(
                    table_rows[key]
                    for key in dict.fromkeys(
                        span.node_id for span in draft.spans if span.node_id in table_rows
                    )
                ),
            )
            for draft in drafts
        )
        reasons = tuple(
            sorted(
                {
                    reason
                    for region in regions
                    if not region.complete
                    for reason in region.reason_codes
                }
            )
        )
        return SemanticSourceLayout(
            regions, tuple(r.region_id for r in regions), all(r.complete for r in regions), reasons
        )
    except _InvalidSource as error:
        return _failure(str(error))
    except KeyError, TypeError, ValueError, AttributeError, IndexError, OverflowError:
        return _failure("SEMANTIC_SOURCE_INPUT_INVALID")
