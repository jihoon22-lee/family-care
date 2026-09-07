"""Recheck component proposals against retained source, without Worker authority."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from familycare_api.documents.generated_metadata import (
    METADATA_FIELD_LABELS,
    METADATA_ROLE_TITLES,
    DocumentMetadataComponent,
    DocumentMetadataFact,
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
_PATTERNS = {
    name: re.compile(
        r"^\s*(?:"
        + "|".join(re.escape(label) for label in labels)
        + r")\s*(?:[:：]|\t)\s*(?P<value>\S(?:.*\S)?)\s*$",
        re.IGNORECASE,
    )
    for name, labels in METADATA_FIELD_LABELS.items()
}


def _key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def _value(name: str, raw: str) -> str | None:
    if not raw or len(raw) > 240:
        return None
    if name not in _DATES:
        return unicodedata.normalize("NFC", raw)
    match = re.fullmatch(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})\.?", raw)
    if match is None:
        return None
    try:
        return date(*(int(part) for part in match.groups())).isoformat()
    except ValueError:
        return None


def _span(node: dict[str, Any], start: int, end: int, left: int, right: int) -> str:
    return json.dumps(
        {
            "node_id": node["node_id"],
            "page_number": node["page_number"],
            "start": start,
            "end": end,
            "text": node["text"][start:end],
            "anchor_start": left,
            "anchor_end": right,
        },
        sort_keys=True,
    )


@dataclass(repr=False)
class _Observed:
    roles: dict[str, set[str]] = field(default_factory=dict)
    facts: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    unresolved: set[str] = field(default_factory=set)

    def add(self, name: str, raw: str, span: str) -> None:
        value = _value(name, raw)
        if value is None:
            self.unresolved.add(name)
        else:
            self.facts.setdefault((name, value), set()).add(span)


def _table(node: dict[str, Any], observed: _Observed, metadata_area_open: bool) -> None:
    if node.get("row_role") == "header":
        return
    cells = node["cells"]
    offset = 0
    for left, right in zip(cells, cells[1:], strict=False):
        adjacent = (
            right["column_index"] == left["column_index"] + 1
            and right["row_index"] == left["row_index"]
            and all(
                cell.get(axis) in (None, 1)
                for cell in (left, right)
                for axis in ("row_span", "column_span")
            )
        )
        for name, labels in METADATA_FIELD_LABELS.items():
            if not adjacent or _key(left["text"].strip().rstrip(":：")) not in labels:
                continue
            raw = right["text"].strip()
            if any(_key(raw.rstrip(":：")) in names for names in METADATA_FIELD_LABELS.values()):
                observed.unresolved.add(name)
                continue
            start = (
                offset + len(left["text"]) + 1 + len(right["text"]) - len(right["text"].lstrip())
            )
            if node["text"][start : start + len(raw)] != raw:
                observed.unresolved.add(name)
                continue
            observed.add(
                name,
                raw,
                _span(
                    node,
                    start,
                    start + len(raw),
                    offset,
                    offset + len(left["text"]) + 1 + len(right["text"]),
                ),
            )
            if not metadata_area_open:
                observed.unresolved.add(name)
        offset += len(left["text"]) + 1


def _observe(nodes: list[dict[str, Any]]) -> _Observed:
    observed = _Observed()
    title_area_open = True
    metadata_area_open = True
    tables = [node for node in nodes if node["kind"] == "TABLE_ROW"]
    table_top = min(
        (table["bbox"][1] if table.get("bbox") is not None else float("-inf") for table in tables),
        default=float("inf"),
    )
    represented = {span["block_node_id"] for node in nodes for span in node.get("source_spans", [])}
    for node in nodes:
        if node["source_layer"] not in {"native", "ocr"}:
            continue
        if "LINE_COLUMN_CONTEXT_UNRESOLVED" in node.get("issue_codes", []):
            title_area_open = False
            metadata_area_open = False
            observed.unresolved.update(
                name
                for line in node["text"].splitlines()
                for name, pattern in _PATTERNS.items()
                if pattern.fullmatch(line)
            )
            continue
        if node["kind"] == "BLOCK" and node["node_id"] in represented:
            continue
        if node["kind"] == "BLOCK" and not node.get("schedulable", True):
            title_area_open = False
            continue
        if node["kind"] == "TABLE_ROW":
            _table(node, observed, metadata_area_open)
            continue
        if node["kind"] not in {"BLOCK", "TEXT_LINE"}:
            continue
        offset = 0
        for line in node["text"].splitlines(keepends=True):
            raw = line.rstrip("\r\n")
            title = _key(raw.strip(" \t[]【】"))
            known_title = any(title in titles for titles in METADATA_ROLE_TITLES.values())
            labelled = any(pattern.fullmatch(raw) for pattern in _PATTERNS.values())
            if raw.strip() and not known_title and not labelled:
                title_area_open = False
                metadata_area_open = False
            table_barrier = bool(tables) and (
                node.get("bbox") is None or table_top < node["bbox"][3]
            )
            for role, titles in METADATA_ROLE_TITLES.items():
                if title_area_open and not table_barrier and title in titles:
                    left = offset + len(raw) - len(raw.lstrip(" \t[]【】"))
                    right = offset + len(raw.rstrip(" \t[]【】"))
                    observed.roles.setdefault(role, set()).add(
                        _span(node, left, right, offset, offset + len(raw))
                    )
            for name, pattern in _PATTERNS.items():
                match = pattern.fullmatch(raw)
                if match:
                    if not metadata_area_open:
                        observed.unresolved.add(name)
                    observed.add(
                        name,
                        match["value"],
                        _span(
                            node,
                            offset + match.start("value"),
                            offset + match.end("value"),
                            offset,
                            offset + len(raw),
                        ),
                    )
            offset += len(line)
    return observed


def _conflicts(facts: dict[tuple[str, str], set[str]]) -> set[str]:
    values: dict[str, set[str]] = {}
    for name, value in facts:
        values.setdefault(name, set()).add(_key(value))
    result = {name for name, items in values.items() if len(items) > 1 and name not in _MULTIPLE}
    starts, ends = values.get("applicability_start", set()), values.get("applicability_end", set())
    if len(starts) == len(ends) == 1 and next(iter(starts)) > next(iter(ends)):
        result.update({"applicability_start", "applicability_end"})
    return result


@dataclass(frozen=True, repr=False)
class ValidatedComponent:
    role: str
    facts: dict[str, tuple[str, ...]]
    conflicting_fields: tuple[str, ...]
    unresolved_fields: tuple[str, ...]


def validate_component_metadata(
    component: dict[str, Any],
    projection: dict[str, Any],
    *,
    page_loader: Callable[[int], dict[str, Any]] | None = None,
) -> ValidatedComponent | None:
    """Require complete original anchors; caller separately checks generation and scope.

    Retained projections are read locally by the server, never supplied by a client.
    Metadata classification confers neither enrollment nor edition applicability.
    """
    try:
        return _validate(component, projection, page_loader)
    except KeyError, TypeError, ValueError, AttributeError, OverflowError:
        return None


def _validate(
    component: dict[str, Any],
    projection: dict[str, Any],
    page_loader: Callable[[int], dict[str, Any]] | None,
) -> ValidatedComponent | None:
    if set(component) != DocumentMetadataComponent.__required_keys__:
        return None
    for name, limit in (
        ("facts", 10000),
        ("role_spans", 10000),
        ("conflicting_fields", len(METADATA_FIELD_LABELS)),
        ("unresolved_fields", len(METADATA_FIELD_LABELS)),
    ):
        items = component[name]
        if not isinstance(items, list) or len(items) > limit:
            return None
        if name.endswith("fields") and len(set(items)) != len(items):
            return None
    start, end, role = component["page_start"], component["page_end"], component["role"]
    if (
        type(start) is not int
        or type(end) is not int
        or not 1 <= start <= end <= 500
        or role not in METADATA_ROLE_TITLES
        or component["authority"] != "CONTENT_CLASSIFICATION_ONLY"
    ):
        return None
    lineage = projection["lineage"]
    identity = hashlib.sha256(
        json.dumps(
            [
                "document-metadata-v1",
                lineage["document_version_id"],
                lineage["extraction_id"],
                lineage["source_payload_sha256"],
                lineage["ocr_revision"],
                role,
                start,
                end,
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    if component["identity"] != identity:
        return None
    pages: dict[int, list[dict[str, Any]]] = {}
    node_ids: set[str] = set()
    for node in projection["nodes"]:
        if node["node_id"] in node_ids:
            return None
        node_ids.add(node["node_id"])
        if start <= node["page_number"] <= end:
            pages.setdefault(node["page_number"], []).append(node)
    observed = _Observed()
    scalar_summary: dict[str, str] = {}
    for number in range(start, end + 1):
        page_nodes = pages.get(number, [])
        if page_loader is not None:
            loaded = page_loader(number)
            if loaded["lineage"] != lineage:
                return None
            page_nodes = [node for node in loaded["nodes"] if node["page_number"] == number]
            identifiers = [node["node_id"] for node in page_nodes]
            if len(set(identifiers)) != len(identifiers):
                return None
        page = _observe(page_nodes)
        if set(page.roles) != {role}:
            return None
        scalars = {name: _key(value) for name, value in page.facts if name not in _MULTIPLE}
        if number > start:
            common = scalar_summary.keys() & scalars.keys()
            if (
                role in {"policy", "application", "amendment"}
                or _conflicts(page.facts)
                or _conflicts(observed.facts)
                or not common & _IDENTITY
                or any(scalar_summary[name] != scalars[name] for name in common)
            ):
                return None
        scalar_summary.update(scalars)
        observed.roles.setdefault(role, set()).update(page.roles[role])
        observed.unresolved.update(page.unresolved)
        for key, spans in page.facts.items():
            observed.facts.setdefault(key, set()).update(spans)
    claimed_roles = {json.dumps(span, sort_keys=True) for span in component["role_spans"]}
    if claimed_roles != observed.roles[role]:
        return None
    claimed: dict[tuple[str, str], set[str]] = {}
    for fact in component["facts"]:
        if set(fact) != DocumentMetadataFact.__required_keys__:
            return None
        if not isinstance(fact["spans"], list) or len(fact["spans"]) > 10000:
            return None
        key = fact["field"], fact["value"]
        if key in claimed or not fact["spans"]:
            return None
        claimed[key] = {json.dumps(span, sort_keys=True) for span in fact["spans"]}
    conflicts = _conflicts(observed.facts)
    if (
        claimed != observed.facts
        or set(component["conflicting_fields"]) != conflicts
        or set(component["unresolved_fields"]) != observed.unresolved
    ):
        return None
    facts: dict[str, tuple[str, ...]] = {}
    for name, value in claimed:
        facts[name] = (*facts.get(name, ()), value)
    return ValidatedComponent(
        role, facts, tuple(sorted(conflicts)), tuple(sorted(observed.unresolved))
    )
