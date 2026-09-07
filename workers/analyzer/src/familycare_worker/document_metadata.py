"""Local, source-addressed component proposals, independent of IR identity.

Classification does not establish enrollment or edition applicability. Explicit
labels provide one local metadata producer; unsupported layouts remain unresolved
for the structured proposal path. Protected text must never be logged.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from typing import cast
from uuid import UUID

from familycare_worker.document_structure import (
    DocumentStructure,
    SourceLineage,
    StructureNode,
    StructurePage,
)
from familycare_worker.generated_metadata import (
    METADATA_FIELD_LABELS,
    METADATA_ROLE_TITLES,
    DocumentMetadataProposal,
    DocumentMetadataRole,
)

ComponentRole = DocumentMetadataRole
REVISION = "document-metadata-v2"


class DocumentMetadataError(ValueError):
    def __init__(self) -> None:
        super().__init__("DOCUMENT_METADATA_INVALID")


@dataclass(frozen=True, repr=False)
class MetadataSpan:
    node_id: str
    page_number: int
    start: int
    end: int
    text: str
    anchor_start: int
    anchor_end: int


@dataclass(frozen=True, repr=False)
class DocumentFact:
    field: str
    value: str
    spans: tuple[MetadataSpan, ...]


@dataclass(frozen=True, repr=False)
class ComponentProposal:
    identity: str
    role: ComponentRole
    page_start: int
    page_end: int
    role_spans: tuple[MetadataSpan, ...]
    facts: tuple[DocumentFact, ...]
    conflicting_fields: tuple[str, ...]
    unresolved_fields: tuple[str, ...]
    authority: str = "CONTENT_CLASSIFICATION_ONLY"


@dataclass(frozen=True, repr=False)
class DocumentMetadata:
    components: tuple[ComponentProposal, ...]
    unresolved_pages: tuple[int, ...]
    revision: str = REVISION


_ROLE_TITLES = cast(dict[ComponentRole, tuple[str, ...]], METADATA_ROLE_TITLES)
_LABELS = METADATA_FIELD_LABELS
_LABEL_PATTERNS = tuple(
    (
        field,
        re.compile(
            r"^\s*(?:"
            + "|".join(re.escape(label) for label in labels)
            + r")(?:\s*[:：]\s*|\s+)(?P<value>\S(?:.*\S)?)\s*$",
            re.IGNORECASE,
        ),
    )
    for field, labels in _LABELS.items()
)
_DATES = frozenset(
    {
        "edition_date",
        "applicability_start",
        "applicability_end",
        "contract_date",
        "amendment_effective_date",
    }
)
_MULTIPLE = frozenset({"rider_code", "terms_reference", "edition_reference"})
_IDENTITY = frozenset({"product_code", "product_name", "terms_code", "edition_code"})
_REFERENCE_HEADING = re.compile(
    r"예시|예제|참고|목록|제출|구비|청구|서류|설명|읽어|참조|안내|"
    r"\b(?:example|sample\s+(?:of|document)|checklist|reference|submit|read)\b",
    re.IGNORECASE,
)


def _product_heading(raw: str) -> bool:
    """Recognize a product caption, never an arbitrary preceding line."""
    return bool(
        3 < len(raw) <= 200
        and not _REFERENCE_HEADING.search(raw)
        and not re.search(r"[:：.!?。]|제\s*\d+\s*조", raw)
        and not re.search(r"(?:생명보험|손해보험|화재해상보험|주식회사)$", raw)
        and re.fullmatch(r".+(?:보험|\bpolicy)(?:\s*\([^()]{1,40}\))?", raw, re.IGNORECASE)
    )


def _cover_prelude(raw: str) -> bool:
    if _REFERENCE_HEADING.search(raw) or len(raw) > 160:
        return False
    return _product_heading(raw) or bool(
        re.fullmatch(
            r"(?:\(?무배당\)?|\(?갱신형\)?|\S+(?:생명|화재|손해보험|생명보험|주식회사)|"
            r"[\w ]+(?:Assurance|Life|Insurance Company))",
            raw,
            re.IGNORECASE,
        )
    )


def _role_title(raw: str) -> tuple[ComponentRole, int, int, int] | None:
    """Return original title bounds and an optional product-caption prefix end."""
    for role, titles in _ROLE_TITLES.items():
        for title in sorted(titles, key=len, reverse=True):
            pattern = (
                r"\s*".join(re.escape(char) for char in title)
                if re.search(r"[가-힣]", title)
                else re.escape(title).replace(r"\ ", r"\s+")
            )
            match = re.search(pattern + r"$", raw, re.IGNORECASE)
            if match is None:
                continue
            prefix = raw[: match.start()].rstrip()
            if not prefix or (role == "terms" and _product_heading(prefix)):
                return role, match.start(), match.end(), len(prefix)
    return None


def _key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def _value(field: str, value: str) -> str | None:
    if len(value) > 240:
        return None
    if field not in _DATES:
        return unicodedata.normalize("NFC", value)
    match = re.fullmatch(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})\.?", value)
    if match is None:
        return None
    try:
        return date(*(int(part) for part in match.groups())).isoformat()
    except ValueError:
        return None


def _span(
    node: StructureNode,
    start: int,
    end: int,
    *,
    anchor_start: int | None = None,
    anchor_end: int | None = None,
) -> MetadataSpan:
    return MetadataSpan(
        node.node_id,
        node.page_number,
        start,
        end,
        node.text[start:end],
        start if anchor_start is None else anchor_start,
        end if anchor_end is None else anchor_end,
    )


def _facts(items: list[DocumentFact]) -> tuple[DocumentFact, ...]:
    grouped: dict[tuple[str, str], list[MetadataSpan]] = {}
    seen: dict[tuple[str, str], set[MetadataSpan]] = {}
    for fact in items:
        spans = grouped.setdefault((fact.field, fact.value), [])
        identities = seen.setdefault((fact.field, fact.value), set())
        for span in fact.spans:
            if span not in identities:
                identities.add(span)
                spans.append(span)
    return tuple(
        DocumentFact(field, value, tuple(spans)) for (field, value), spans in grouped.items()
    )


def _conflicts(facts: tuple[DocumentFact, ...]) -> tuple[str, ...]:
    values: dict[str, set[str]] = {}
    for fact in facts:
        values.setdefault(fact.field, set()).add(_key(fact.value))
    conflicts = {
        field for field, items in values.items() if len(items) > 1 and field not in _MULTIPLE
    }
    starts, ends = values.get("applicability_start", set()), values.get("applicability_end", set())
    if len(starts) == len(ends) == 1 and next(iter(starts)) > next(iter(ends)):
        conflicts.update(("applicability_start", "applicability_end"))
    return tuple(sorted(conflicts))


def _compatible(left: ComponentProposal, right: ComponentProposal) -> bool:
    if left.page_end + 1 != right.page_start or left.role != right.role:
        return False
    if left.role in {"policy", "application", "amendment"}:
        # Product metadata alone cannot identify a contract or an insured party.
        return False
    if left.conflicting_fields or right.conflicting_fields:
        return False
    left_values = {f.field: _key(f.value) for f in left.facts if f.field not in _MULTIPLE}
    right_values = {f.field: _key(f.value) for f in right.facts if f.field not in _MULTIPLE}
    common = left_values.keys() & right_values.keys()
    return bool(common & _IDENTITY) and all(
        left_values[field] == right_values[field] for field in common
    )


def _merge_components(pages: list[ComponentProposal]) -> list[ComponentProposal]:
    groups: list[list[ComponentProposal]] = []
    summary: ComponentProposal | None = None
    summary_facts: dict[str, DocumentFact] = {}
    for page in pages:
        if summary is None or not _compatible(summary, page):
            groups.append([])
            summary_facts = {}
        groups[-1].append(page)
        summary_facts.update(
            {fact.field: fact for fact in page.facts if fact.field not in _MULTIPLE}
        )
        # Only unique scalar values are needed for the next boundary comparison.
        summary = replace(page, facts=tuple(summary_facts.values()))
    result = []
    for group in groups:
        facts = _facts([fact for page in group for fact in page.facts])
        result.append(
            replace(
                group[0],
                page_end=group[-1].page_end,
                facts=facts,
                role_spans=tuple(span for page in group for span in page.role_spans),
                conflicting_fields=_conflicts(facts),
                unresolved_fields=tuple(
                    sorted({field for page in group for field in page.unresolved_fields})
                ),
            )
        )
    return result


def _layout_nodes(nodes: Sequence[StructureNode]) -> tuple[Sequence[StructureNode], bool]:
    """Interleave proven cell rows with native lines without altering retained IR."""
    if not any(node.kind == "TABLE_ROW" for node in nodes):
        return nodes, False
    represented = {span.block_node_id for node in nodes for span in node.source_spans}
    positions: list[tuple[float, float, int, StructureNode]] = []
    bottoms: dict[str, float] = {}
    native_order: list[str] = []
    for index, node in enumerate(nodes):
        if node.kind == "BLOCK" and (node.node_id in represented or not node.schedulable):
            continue
        if node.kind == "TABLE_ROW":
            boxes = [cell.bbox for cell in node.cells]
            if not boxes or any(box is None for box in boxes):
                return nodes, False
            known = [box for box in boxes if box is not None]
            top = min(box[1] for box in known)
            left = min(box[0] for box in known)
            bottoms[node.node_id] = max(box[3] for box in known)
        else:
            if node.bbox is None:
                return nodes, False
            left, top = node.bbox[:2]
            bottoms[node.node_id] = node.bbox[3]
            native_order.append(node.node_id)
        positions.append((top, left, index, node))
    ordered = sorted(positions, key=lambda item: item[:3])
    if [item[3].node_id for item in ordered if item[3].kind != "TABLE_ROW"] != native_order:
        return nodes, False
    if any(
        right[0] < bottoms[left[3].node_id]
        for left, right in zip(ordered, ordered[1:], strict=False)
    ):
        return nodes, False
    return [item[3] for item in ordered], True


def _metadata_row_context(node: StructureNode) -> bool:
    cells = node.cells
    if node.row_role == "header" or len(cells) % 2 or not cells:
        return False
    labels = {label for values in _LABELS.values() for label in values}
    for left, right in zip(cells[::2], cells[1::2], strict=True):
        if (
            _key(left.text.strip().rstrip(":：")) not in labels
            or not right.text.strip()
            or _key(right.text.strip().rstrip(":：")) in labels
            or right.column_index != left.column_index + 1
            or right.row_index != left.row_index
            or any(
                span not in (None, 1)
                for cell in (left, right)
                for span in (cell.row_span, cell.column_span)
            )
        ):
            return False
    return True


def analyze_document_metadata(structure: DocumentStructure) -> DocumentMetadata:
    """Propose only pages with explicit role titles and retain conflicting facts.

    An unclassified page is not absorbed because of its neighbor or intake kind.
    Adjacent explicitly classified pages merge only with compatible identity facts.
    The consumer must validate source/scope and applicability before publication.
    """
    by_page: dict[int, list[StructureNode]] = {}
    for node in structure.nodes:
        if node.source_layer != "unavailable":
            by_page.setdefault(node.page_number, []).append(node)
    return analyze_metadata_pages(
        structure.lineage, ((page, by_page.get(page.page_number, [])) for page in structure.pages)
    )


def analyze_metadata_pages(
    lineage: SourceLineage, pages: Iterable[tuple[StructurePage, Sequence[StructureNode]]]
) -> DocumentMetadata:
    """Consume one retained page at a time without restoring the whole IR."""
    components: list[ComponentProposal] = []
    unresolved: list[int] = []
    payload_bytes = 0
    for page, nodes in pages:
        nodes, positioned = _layout_nodes(nodes)
        line_blocks = {span.block_node_id for node in nodes for span in node.source_spans}
        tables = [node for node in nodes if node.kind == "TABLE_ROW"]
        table_top = min(
            (table.bbox[1] if table.bbox is not None else float("-inf") for table in tables),
            default=float("inf"),
        )
        title_area_open = True
        metadata_area_open = True
        roles: dict[ComponentRole, list[MetadataSpan]] = {}
        facts: list[DocumentFact] = []
        unresolved_fields: set[str] = set()
        for node in nodes:
            if "LINE_COLUMN_CONTEXT_UNRESOLVED" in node.issue_codes:
                title_area_open = False
                metadata_area_open = False
                for line in node.text.splitlines():
                    for field, pattern in _LABEL_PATTERNS:
                        if pattern.fullmatch(line):
                            unresolved_fields.add(field)
                continue
            if node.kind == "BLOCK" and node.node_id in line_blocks:
                continue
            if node.kind == "BLOCK" and not node.schedulable:
                title_area_open = False
                continue
            cell_text_flow = (
                positioned
                and node.kind == "TABLE_ROW"
                and node.row_role != "header"
                and len(node.cells) == 1
                and node.text == node.cells[0].text
            )
            if node.kind == "TABLE_ROW" and not cell_text_flow:
                if positioned and node.text.strip() and not _metadata_row_context(node):
                    title_area_open = False
                    metadata_area_open = False
                if node.row_role == "header":
                    continue
                cell_offset = 0
                for index, cell in enumerate(node.cells[:-1]):
                    next_cell = node.cells[index + 1]
                    adjacent = (
                        next_cell.column_index == cell.column_index + 1
                        and next_cell.row_index == cell.row_index
                        and all(
                            span in (None, 1)
                            for span in (
                                cell.row_span,
                                cell.column_span,
                                next_cell.row_span,
                                next_cell.column_span,
                            )
                        )
                    )
                    if adjacent:
                        for field, labels in _LABELS.items():
                            if _key(cell.text.strip().rstrip(":：")) not in labels:
                                continue
                            raw_value = next_cell.text.strip()
                            value = _value(field, raw_value)
                            if (
                                value is None
                                or not raw_value
                                or any(
                                    _key(raw_value.strip().rstrip(":：")) in other_labels
                                    for other_labels in _LABELS.values()
                                )
                            ):
                                unresolved_fields.add(field)
                                continue
                            start = cell_offset + len(cell.text) + 1
                            start += len(next_cell.text) - len(next_cell.text.lstrip())
                            if node.text[start : start + len(raw_value)] != raw_value:
                                unresolved_fields.add(field)
                                continue
                            facts.append(
                                DocumentFact(
                                    field,
                                    value,
                                    (
                                        _span(
                                            node,
                                            start,
                                            start + len(raw_value),
                                            anchor_start=cell_offset,
                                            anchor_end=cell_offset
                                            + len(cell.text)
                                            + 1
                                            + len(next_cell.text),
                                        ),
                                    ),
                                )
                            )
                            if not metadata_area_open:
                                unresolved_fields.add(field)
                    cell_offset += len(cell.text) + 1
                continue
            offset = 0
            for line in node.text.splitlines(keepends=True):
                raw = line.rstrip("\r\n")
                trimmed = raw.strip(" \t[]【】")
                trim_start = len(raw) - len(raw.lstrip(" \t[]【】"))
                title = _role_title(trimmed)
                known_title = title is not None
                labelled = any(pattern.fullmatch(raw) for _, pattern in _LABEL_PATTERNS)
                if (
                    labelled
                    and not re.search(r"[:：\t]", raw)
                    and any(
                        _REFERENCE_HEADING.search(match.group("value"))
                        for _, pattern in _LABEL_PATTERNS
                        if (match := pattern.fullmatch(raw))
                    )
                ):
                    labelled = False
                prelude = _cover_prelude(trimmed)
                if raw.strip() and not known_title and not labelled and not prelude:
                    title_area_open = False
                    metadata_area_open = False
                table_barrier = (
                    not positioned
                    and bool(tables)
                    and (node.bbox is None or table_top < node.bbox[3])
                )
                if title_area_open and not table_barrier and title is not None:
                    role, left, right, _ = title
                    roles.setdefault(role, []).append(
                        _span(
                            node,
                            offset + trim_start + left,
                            offset + trim_start + right,
                            anchor_start=offset,
                            anchor_end=offset + len(raw),
                        )
                    )
                product_end = (
                    title[3]
                    if title is not None
                    else (len(trimmed) if _product_heading(trimmed) else 0)
                )
                if metadata_area_open and not table_barrier and not labelled and product_end:
                    facts.append(
                        DocumentFact(
                            "product_name",
                            unicodedata.normalize("NFC", trimmed[:product_end]),
                            (
                                _span(
                                    node,
                                    offset + trim_start,
                                    offset + trim_start + product_end,
                                    anchor_start=offset,
                                    anchor_end=offset + len(raw),
                                ),
                            ),
                        )
                    )
                for field, pattern in _LABEL_PATTERNS:
                    match = pattern.fullmatch(raw)
                    if match is None:
                        continue
                    if not metadata_area_open:
                        unresolved_fields.add(field)
                    value = _value(field, match.group("value"))
                    if value is None:
                        unresolved_fields.add(field)
                    else:
                        facts.append(
                            DocumentFact(
                                field,
                                value,
                                (
                                    _span(
                                        node,
                                        offset + match.start("value"),
                                        offset + match.end("value"),
                                        anchor_start=offset,
                                        anchor_end=offset + len(raw),
                                    ),
                                ),
                            )
                        )
                offset += len(line)
        if len(roles) != 1 or page.active_layer == "unavailable":
            unresolved.append(page.page_number)
            continue
        role, role_spans = next(iter(roles.items()))
        page_facts = _facts(facts)
        component = ComponentProposal(
            "",
            role,
            page.page_number,
            page.page_number,
            tuple(role_spans),
            page_facts,
            _conflicts(page_facts),
            tuple(sorted(unresolved_fields)),
        )
        payload_bytes += len(json.dumps(asdict(component), ensure_ascii=False).encode())
        if payload_bytes > 8 * 1024 * 1024:
            raise DocumentMetadataError
        components.append(component)
    components = _merge_components(components)
    for index, component in enumerate(components):
        identity = hashlib.sha256(
            json.dumps(
                [
                    REVISION,
                    str(lineage.document_version_id),
                    str(lineage.extraction_id),
                    lineage.source_payload_sha256,
                    lineage.ocr_revision,
                    component.role,
                    component.page_start,
                    component.page_end,
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        components[index] = replace(component, identity=identity)
    return DocumentMetadata(tuple(components), tuple(unresolved))


def metadata_proposal(
    structure: DocumentStructure | DocumentMetadata,
    generation_id: UUID,
    structure_identity_sha256: str,
) -> DocumentMetadataProposal:
    """Serialize the protected, bounded local contract; never send it as telemetry."""
    metadata = (
        analyze_document_metadata(structure)
        if isinstance(structure, DocumentStructure)
        else structure
    )
    if len(metadata.components) > 500 or len(metadata.unresolved_pages) > 500:
        raise DocumentMetadataError
    for component in metadata.components:
        if (
            not 1 <= component.page_start <= component.page_end <= 500
            or not 1 <= len(component.role_spans) <= 10000
            or len(component.facts) > 10000
            or any(not 1 <= len(fact.spans) <= 10000 for fact in component.facts)
        ):
            raise DocumentMetadataError
    encoded = json.dumps(
        {
            "schema_version": "1",
            "revision": REVISION,
            "generation_id": str(generation_id),
            "structure_identity_sha256": structure_identity_sha256,
            "components": [asdict(item) for item in metadata.components],
            "unresolved_pages": metadata.unresolved_pages,
        },
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    if len(encoded) > 8 * 1024 * 1024:
        raise DocumentMetadataError
    return cast(DocumentMetadataProposal, json.loads(encoded))
