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
_LEGACY_PATTERNS = {
    name: re.compile(
        r"^\s*(?:"
        + "|".join(re.escape(label) for label in labels)
        + r")\s*(?:[:：]|\t)\s*(?P<value>\S(?:.*\S)?)\s*$",
        re.IGNORECASE,
    )
    for name, labels in METADATA_FIELD_LABELS.items()
}
_PATTERNS = {
    name: re.compile(
        r"^\s*(?:"
        + "|".join(re.escape(label) for label in labels)
        + r")(?:\s*[:：]\s*|\s+)(?P<value>\S(?:.*\S)?)\s*$",
        re.IGNORECASE,
    )
    for name, labels in METADATA_FIELD_LABELS.items()
}
_REFERENCE_HEADING = re.compile(
    r"예시|예제|참고|목록|제출|구비|청구|서류|설명|읽어|참조|안내|"
    r"\b(?:example|sample\s+(?:of|document)|checklist|reference|submit|read)\b",
    re.IGNORECASE,
)


def _product_caption(text: str) -> bool:
    return bool(
        3 < len(text) <= 200
        and not _REFERENCE_HEADING.search(text)
        and not re.search(r"[:：.!?。]|제\s*\d+\s*조", text)
        and not re.search(r"(?:생명보험|손해보험|화재해상보험|주식회사)$", text)
        and re.fullmatch(r".+(?:보험|\bpolicy)(?:\s*\([^()]{1,40}\))?", text, re.IGNORECASE)
    )


def _cover_caption(text: str) -> bool:
    return bool(
        len(text) <= 160
        and not _REFERENCE_HEADING.search(text)
        and (
            _product_caption(text)
            or re.fullmatch(
                r"(?:\(?무배당\)?|\(?갱신형\)?|\S+(?:생명|화재|손해보험|생명보험|주식회사)|"
                r"[\w ]+(?:Assurance|Life|Insurance Company))",
                text,
                re.IGNORECASE,
            )
        )
    )


def _cover_role(text: str) -> tuple[str, int, int, int] | None:
    for role, titles in METADATA_ROLE_TITLES.items():
        for title in sorted(titles, key=len, reverse=True):
            pattern = (
                r"\s*".join(re.escape(char) for char in title)
                if re.search(r"[가-힣]", title)
                else re.escape(title).replace(r"\ ", r"\s+")
            )
            match = re.search(pattern + r"$", text, re.IGNORECASE)
            if match is not None:
                prefix = text[: match.start()].rstrip()
                if not prefix or (role == "terms" and _product_caption(prefix)):
                    return role, match.start(), match.end(), len(prefix)
    return None


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


def _source_layout(nodes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    if not any(node["kind"] == "TABLE_ROW" for node in nodes):
        return nodes, False
    represented = {span["block_node_id"] for node in nodes for span in node.get("source_spans", [])}
    positioned = []
    bottoms: dict[str, float] = {}
    native_order = []
    for index, node in enumerate(nodes):
        if node["kind"] == "BLOCK" and (
            node["node_id"] in represented or not node.get("schedulable", True)
        ):
            continue
        if node["kind"] == "TABLE_ROW":
            cells = node["cells"]
            if not cells or any(cell.get("bbox") is None for cell in cells):
                return nodes, False
            top, left = (
                min(cell["bbox"][1] for cell in cells),
                min(cell["bbox"][0] for cell in cells),
            )
            bottoms[node["node_id"]] = max(cell["bbox"][3] for cell in cells)
        else:
            if node.get("bbox") is None:
                return nodes, False
            left, top = node["bbox"][:2]
            bottoms[node["node_id"]] = node["bbox"][3]
            native_order.append(node["node_id"])
        positioned.append((top, left, index, node))
    ordered = sorted(positioned, key=lambda item: item[:3])
    if [item[3]["node_id"] for item in ordered if item[3]["kind"] != "TABLE_ROW"] != native_order:
        return nodes, False
    if any(
        right[0] < bottoms[left[3]["node_id"]]
        for left, right in zip(ordered, ordered[1:], strict=False)
    ):
        return nodes, False
    return [item[3] for item in ordered], True


def _metadata_row_context(node: dict[str, Any]) -> bool:
    cells = node["cells"]
    if node.get("row_role") == "header" or not cells or len(cells) % 2:
        return False
    labels = {label for names in METADATA_FIELD_LABELS.values() for label in names}
    for left, right in zip(cells[::2], cells[1::2], strict=True):
        if (
            _key(left["text"].strip().rstrip(":：")) not in labels
            or not right["text"].strip()
            or _key(right["text"].strip().rstrip(":：")) in labels
            or right["column_index"] != left["column_index"] + 1
            or right["row_index"] != left["row_index"]
            or any(
                cell.get(axis) not in (None, 1)
                for cell in (left, right)
                for axis in ("row_span", "column_span")
            )
        ):
            return False
    return True


def _observe(nodes: list[dict[str, Any]], *, legacy: bool = False) -> _Observed:
    observed = _Observed()
    patterns = _LEGACY_PATTERNS if legacy else _PATTERNS
    nodes, positioned = (nodes, False) if legacy else _source_layout(nodes)
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
                for name, pattern in patterns.items()
                if pattern.fullmatch(line)
            )
            continue
        if node["kind"] == "BLOCK" and node["node_id"] in represented:
            continue
        if node["kind"] == "BLOCK" and not node.get("schedulable", True):
            title_area_open = False
            continue
        cell_text_flow = (
            positioned
            and node["kind"] == "TABLE_ROW"
            and node.get("row_role") != "header"
            and len(node["cells"]) == 1
            and node["text"] == node["cells"][0]["text"]
        )
        if node["kind"] == "TABLE_ROW" and not cell_text_flow:
            if positioned and node["text"].strip() and not _metadata_row_context(node):
                title_area_open = False
                metadata_area_open = False
            _table(node, observed, metadata_area_open)
            continue
        if node["kind"] not in {"BLOCK", "TEXT_LINE"} and not cell_text_flow:
            continue
        offset = 0
        for line in node["text"].splitlines(keepends=True):
            raw = line.rstrip("\r\n")
            trimmed = raw.strip(" \t[]【】")
            trim_start = len(raw) - len(raw.lstrip(" \t[]【】"))
            title = _key(trimmed)
            cover_role = None if legacy else _cover_role(trimmed)
            known_title = (
                any(title in titles for titles in METADATA_ROLE_TITLES.values())
                if legacy
                else cover_role is not None
            )
            labelled = any(pattern.fullmatch(raw) for pattern in patterns.values())
            if (
                not legacy
                and labelled
                and not re.search(r"[:：\t]", raw)
                and any(
                    _REFERENCE_HEADING.search(match["value"])
                    for pattern in patterns.values()
                    if (match := pattern.fullmatch(raw))
                )
            ):
                labelled = False
            prelude = not legacy and _cover_caption(trimmed)
            if raw.strip() and not known_title and not labelled and not prelude:
                title_area_open = False
                metadata_area_open = False
            table_barrier = (
                not positioned
                and bool(tables)
                and (node.get("bbox") is None or table_top < node["bbox"][3])
            )
            for role, titles in METADATA_ROLE_TITLES.items():
                if legacy and title_area_open and not table_barrier and title in titles:
                    left = offset + len(raw) - len(raw.lstrip(" \t[]【】"))
                    right = offset + len(raw.rstrip(" \t[]【】"))
                    observed.roles.setdefault(role, set()).add(
                        _span(node, left, right, offset, offset + len(raw))
                    )
            if title_area_open and not table_barrier and cover_role is not None:
                role, left, right, _ = cover_role
                observed.roles.setdefault(role, set()).add(
                    _span(
                        node,
                        offset + trim_start + left,
                        offset + trim_start + right,
                        offset,
                        offset + len(raw),
                    )
                )
            product_end = (
                cover_role[3]
                if cover_role is not None
                else (len(trimmed) if not legacy and _product_caption(trimmed) else 0)
            )
            if metadata_area_open and not table_barrier and not labelled and product_end:
                observed.add(
                    "product_name",
                    trimmed[:product_end],
                    _span(
                        node,
                        offset + trim_start,
                        offset + trim_start + product_end,
                        offset,
                        offset + len(raw),
                    ),
                )
            for name, pattern in patterns.items():
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
    revision: str = "document-metadata-v2",
) -> ValidatedComponent | None:
    """Require complete original anchors; caller separately checks generation and scope.

    Retained projections are read locally by the server, never supplied by a client.
    Metadata classification confers neither enrollment nor edition applicability.
    """
    try:
        return _validate(component, projection, page_loader, revision)
    except KeyError, TypeError, ValueError, AttributeError, OverflowError:
        return None


def _validate(
    component: dict[str, Any],
    projection: dict[str, Any],
    page_loader: Callable[[int], dict[str, Any]] | None,
    revision: str,
) -> ValidatedComponent | None:
    if revision not in {"document-metadata-v1", "document-metadata-v2"}:
        return None
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
                revision,
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
        page = _observe(page_nodes, legacy=revision == "document-metadata-v1")
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
