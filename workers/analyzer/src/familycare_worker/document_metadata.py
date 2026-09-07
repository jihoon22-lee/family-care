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
REVISION = "document-metadata-v1"


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
            + r")\s*(?:[:：]|\t)\s*(?P<value>\S(?:.*\S)?)\s*$",
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
            if node.kind == "TABLE_ROW":
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
                title = _key(raw.strip(" \t[]【】"))
                known_title = any(title in titles for titles in _ROLE_TITLES.values())
                labelled = any(pattern.fullmatch(raw) for _, pattern in _LABEL_PATTERNS)
                if raw.strip() and not known_title and not labelled:
                    title_area_open = False
                    metadata_area_open = False
                table_barrier = bool(tables) and (node.bbox is None or table_top < node.bbox[3])
                for role, titles in _ROLE_TITLES.items():
                    if title_area_open and not table_barrier and title in titles:
                        start = offset + len(raw) - len(raw.lstrip(" \t[]【】"))
                        end = offset + len(raw.rstrip(" \t[]【】"))
                        roles.setdefault(role, []).append(
                            _span(
                                node, start, end, anchor_start=offset, anchor_end=offset + len(raw)
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
