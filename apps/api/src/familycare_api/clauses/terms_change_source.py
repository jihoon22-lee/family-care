"""Observe protected amendment source fields without creating enrollment or status.

The repository resolves the observed identity to one existing contract, Rider and
edition. A MATCH here attests only source semantics, never target existence.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal
from uuid import UUID

from familycare_api.documents.generated_metadata import DocumentMetadataSpan
from familycare_api.insurance_documents.metadata_validation import validate_component_metadata
from familycare_api.insurance_documents.terms_body_validation import (
    _box,
    _contains,
    _external_reference_top,
    _lineage_valid,
    _preceding_reference,
    _regions,
    _table_duplicate,
    reference_context_present,
)

REVISION = "terms-change-source-v2"
_LABELS = {
    "contract_number": ("대상계약번호", "계약번호", "증권번호", "contract number", "policy number"),
    "insured": ("대상피보험자", "피보험자", "피보험자 성명", "insured", "insured name"),
    "insurer": ("보험사", "보험회사", "insurer"),
    "change_kind": ("변경구분", "change kind"),
    "operation": ("변경방식", "change operation"),
    "scope_kind": ("적용범위", "change scope"),
    "rider_name": ("대상특약명", "대상담보명", "target rider"),
    "clause_label": ("대상조항", "변경전조항", "target clause", "previous clause"),
    "new_clause_label": ("변경후조항", "new clause"),
    "previous_terms_code": ("변경전약관코드", "previous terms code"),
    "previous_edition_code": ("변경전판본코드", "previous edition code"),
    "new_terms_code": ("변경후약관코드", "적용약관코드", "new terms code"),
    "new_edition_code": ("변경후판본코드", "적용판본코드", "new edition code"),
    "effective_from": ("변경적용일", "change effective from"),
    "effective_through": ("변경적용종료일", "change effective through"),
}


def _key(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


@dataclass(frozen=True, repr=False)
class ChangeMember:
    household_space_id: UUID
    id: UUID
    display_name: str
    internal_alias: str
    version: int


@dataclass(frozen=True, repr=False)
class ChangeSourceField:
    name: str
    value: str
    spans: tuple[DocumentMetadataSpan, ...]


@dataclass(frozen=True, repr=False)
class ObservedTermsChange:
    status: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    reason_codes: tuple[str, ...]
    contract_number_sha256: str | None = None
    family_member_id: UUID | None = None
    member_version: int | None = None
    insurer_key: str | None = None
    scope_kind: str | None = None
    rider_name_key: str | None = None
    clause_label_key: str | None = None
    new_clause_label_key: str | None = None
    new_clause_label_declared: bool = False
    operation: str | None = None
    change_kind: str | None = None
    effective_from: date | None = None
    effective_through: date | None = None
    previous_terms_code: str | None = None
    previous_edition_code: str | None = None
    new_terms_code: str | None = None
    new_edition_code: str | None = None
    source_fields: tuple[ChangeSourceField, ...] = ()


def _span(
    node: dict[str, Any], start: int, end: int, left: int, right: int
) -> DocumentMetadataSpan:
    return {
        "node_id": node["node_id"],
        "page_number": node["page_number"],
        "start": start,
        "end": end,
        "text": node["text"][start:end],
        "anchor_start": left,
        "anchor_end": right,
    }


def _reference_context(node: dict[str, Any]) -> bool:
    return reference_context_present([node]) or any(
        reference_context_present([{**node, "text": cell["text"]}])
        for cell in node.get("cells", [])
    )


def _table_layout(node: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cells = node.get("cells", [])
    if not cells or len(cells) > 64 or not node.get("table_id"):
        raise ValueError("unsupported change table")
    row_box = _box(node)
    boxes = [_box(cell) for cell in cells]
    if (
        node["text"] != "\t".join(cell["text"] for cell in cells)
        or any(not _contains(row_box, box) for box in boxes)
        or any(
            cell["row_index"] != node.get("row_index")
            or type(cell["column_index"]) is not int
            or cell["column_index"] < 0
            or cell.get("row_span") not in (None, 1)
            or cell.get("column_span") not in (None, 1)
            for cell in cells
        )
        or any(
            right["column_index"] != left["column_index"] + 1
            or _box(left)[2] > _box(right)[0]
            or min(_box(left)[3], _box(right)[3]) <= max(_box(left)[1], _box(right)[1])
            for left, right in zip(cells, cells[1:], strict=False)
        )
    ):
        raise ValueError("unsupported change table")
    box = (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )
    for identifier in node.get("context_node_ids", []):
        context = by_id.get(identifier)
        if (
            context is None
            or context["kind"] != "TABLE_ROW"
            or context.get("row_role") != "header"
            or context.get("table_id") != node["table_id"]
            or context["page_number"] != node["page_number"]
            or context["source_layer"] != node["source_layer"]
            or context["row_index"] >= node["row_index"]
            or _reference_context(context)
            or _table_layout({**context, "context_node_ids": []}, {})["bbox"][3] > box[1]
        ):
            raise ValueError("unsupported change table context")
    return {**node, "bbox": box}


def _field_regions(
    nodes: list[dict[str, Any]], role_ids: set[str], *, metadata_revision: str
) -> list[dict[str, Any]]:
    by_id = {node["node_id"]: node for node in nodes}
    represented: set[str] = set()
    for node in nodes:
        try:
            if node["kind"] == "TEXT_LINE" and _lineage_valid(
                node, by_id, metadata_revision=metadata_revision
            ):
                represented.update(span["block_node_id"] for span in node["source_spans"])
        except KeyError, TypeError, ValueError, IndexError:
            continue
    pages: dict[int, list[dict[str, Any]]] = {}
    for node in nodes:
        pages.setdefault(node["page_number"], []).append(node)
    result: list[dict[str, Any]] = []
    for page in pages.values():
        if len(page) > 512:
            raise ValueError("unsupported change page")
        valid, barriers = [], []
        tables = [node for node in page if node["kind"] == "TABLE_ROW"]
        for node in page:
            if node["node_id"] in represented or not node["text"].strip():
                continue
            try:
                _box(node)
                if _table_duplicate(node, tables):
                    continue
                if (
                    node["source_layer"] not in {"native", "ocr"}
                    or node["kind"] not in {"BLOCK", "TEXT_LINE", "TABLE_ROW"}
                    or not node.get("schedulable", True)
                    or any("UNRESOLVED" in code for code in node.get("issue_codes", ()))
                    or not _lineage_valid(node, by_id, metadata_revision=metadata_revision)
                ):
                    raise ValueError("unsupported change node")
                valid.append(_table_layout(node, by_id) if node["kind"] == "TABLE_ROW" else node)
            except KeyError, TypeError, ValueError, IndexError:
                barriers.append(node)
        for region in _regions(valid, barriers):
            if any(node["node_id"] in role_ids for node in region):
                if _preceding_reference(region, page):
                    continue
                cutoff = _external_reference_top(region, page)
                result.extend(node for node in region if cutoff is None or _box(node)[3] <= cutoff)
    return result


def _fields(
    nodes: list[dict[str, Any]], role_spans: list[dict[str, Any]], *, metadata_revision: str
) -> tuple[tuple[ChangeSourceField, ...], frozenset[str]]:
    labels = {_key(label): name for name, names in _LABELS.items() for label in names}
    by_id = {node["node_id"]: node for node in nodes}
    if len(by_id) != len(nodes) or len(nodes) > 20000:
        raise ValueError("unsupported change source")
    result: list[ChangeSourceField] = []
    declared: set[str] = set()
    blocked_pages: set[int] = set()
    started_pages: set[int] = set()
    for node in _field_regions(
        nodes, {span["node_id"] for span in role_spans}, metadata_revision=metadata_revision
    ):
        if node["kind"] == "TABLE_ROW" and _reference_context(node):
            blocked_pages.add(node["page_number"])
        if (
            node["source_layer"] not in {"native", "ocr"}
            or node["kind"] not in {"BLOCK", "TEXT_LINE", "TABLE_ROW"}
            or not node.get("schedulable", True)
            or any("UNRESOLVED" in code for code in node.get("issue_codes", ()))
            or not _lineage_valid(node, by_id, metadata_revision=metadata_revision)
        ):
            continue
        if node["page_number"] in blocked_pages:
            continue
        if node["kind"] == "TABLE_ROW":
            cells = node.get("cells", [])
            if len(cells) == 1 and any(span["node_id"] == node["node_id"] for span in role_spans):
                started_pages.add(node["page_number"])
                continue
            if node["page_number"] not in started_pages:
                continue
            if node.get("row_role") != "data" or node["text"] != "\t".join(
                cell["text"] for cell in cells
            ):
                continue
            offset = 0
            for left, right in zip(cells, cells[1:], strict=False):
                name = labels.get(_key(left["text"].strip().rstrip(":：")))
                if (
                    name
                    and right["column_index"] == left["column_index"] + 1
                    and right["row_index"] == left["row_index"]
                    and all(
                        cell.get(axis) in (None, 1)
                        for cell in (left, right)
                        for axis in ("row_span", "column_span")
                    )
                ):
                    declared.add(name)
                    value = right["text"].strip()
                    if not value:
                        declared.add(f"empty:{name}")
                    start = (
                        offset
                        + len(left["text"])
                        + 1
                        + len(right["text"])
                        - len(right["text"].lstrip())
                    )
                    if len(value) > 240:
                        raise ValueError("unsupported change source")
                    if value:
                        result.append(
                            ChangeSourceField(
                                name,
                                value,
                                (_span(node, start, start + len(value), 0, len(node["text"])),),
                            )
                        )
                offset += len(left["text"]) + 1
            if len(result) > 128:
                raise ValueError("unsupported change source")
            continue
        offset = 0
        for line in node["text"].splitlines(keepends=True):
            raw = line.rstrip("\r\n")
            if reference_context_present([{**node, "text": raw}]):
                blocked_pages.add(node["page_number"])
                break
            if any(
                span["node_id"] == node["node_id"]
                and offset <= span["start"] < span["end"] <= offset + len(raw)
                for span in role_spans
            ):
                started_pages.add(node["page_number"])
                offset += len(line)
                continue
            if node["page_number"] not in started_pages:
                offset += len(line)
                continue
            match = re.fullmatch(r"\s*(?P<label>[^:：\n]{1,80})\s*[:：]\s*(?P<value>.*?)\s*", raw)
            if match and (name := labels.get(_key(match["label"]))):
                declared.add(name)
                value = match["value"]
                if not value:
                    declared.add(f"empty:{name}")
                if len(value) > 240:
                    raise ValueError("unsupported change source")
                if value:
                    result.append(
                        ChangeSourceField(
                            name,
                            value,
                            (
                                _span(
                                    node,
                                    offset + match.start("value"),
                                    offset + match.end("value"),
                                    offset,
                                    offset + len(raw),
                                ),
                            ),
                        )
                    )
            elif raw.strip():
                blocked_pages.add(node["page_number"])
                break
            offset += len(line)
        if len(result) > 128:
            raise ValueError("unsupported change source")
    return tuple(result), frozenset(declared)


def _date(text: str | None) -> date | None:
    if text is None:
        return None
    match = re.fullmatch(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})\.?", text)
    if match is None:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def observe_terms_change(
    component: dict[str, Any],
    projection: dict[str, Any],
    *,
    metadata_revision: str,
    household_space_id: UUID,
    family_member_id: UUID,
    members: Sequence[ChangeMember],
) -> ObservedTermsChange:
    """Require original amendment classification, field anchors and an exact local insured."""
    valid = validate_component_metadata(component, projection, revision=metadata_revision)
    if valid is None or valid.role != "amendment":
        return ObservedTermsChange("UNKNOWN", ("SOURCE_CLASSIFICATION_INVALID",))
    try:
        fields, declared = _fields(
            [
                node
                for node in projection["nodes"]
                if component["page_start"] <= node["page_number"] <= component["page_end"]
            ],
            component["role_spans"],
            metadata_revision=metadata_revision,
        )
    except KeyError, TypeError, ValueError, AttributeError, IndexError, OverflowError:
        return ObservedTermsChange("UNKNOWN", ("SOURCE_FIELDS_UNSUPPORTED",))
    values: dict[str, set[str]] = {}
    for field in fields:
        values.setdefault(field.name, set()).add(_key(field.value))
    reasons = ["SOURCE_FIELD_CONFLICT" for items in values.values() if len(items) > 1]

    def scalar(name: str) -> str | None:
        if f"empty:{name}" in declared:
            return None
        items = values.get(name, set())
        return next(iter(items)) if len(items) == 1 else None

    local = [member for member in members if member.household_space_id == household_space_id]
    names: dict[str, set[UUID]] = {}
    for member in local:
        for label in (member.display_name, member.internal_alias):
            if label.strip():
                names.setdefault(_key(label), set()).add(member.id)
    matched = names.get(scalar("insured") or "", set())
    member_id = next(iter(matched)) if len(matched) == 1 else None
    member_rows = [member for member in local if member.id == member_id]
    member_version = member_rows[0].version if len(member_rows) == 1 else None
    wrong_member = member_id is not None and member_id != family_member_id
    if wrong_member:
        reasons.append("WRONG_MEMBER")
    elif member_id is None or member_version is None:
        reasons.append("INSURED_IDENTITY_UNRESOLVED")
    operation = {"교체": "REPLACE", "replace": "REPLACE", "추가": "ADD", "add": "ADD"}.get(
        scalar("operation") or ""
    )
    change_kind = {
        "조건변경": "AMENDMENT",
        "amendment": "AMENDMENT",
        "갱신": "RENEWAL",
        "renewal": "RENEWAL",
    }.get(scalar("change_kind") or "")
    scope = {
        "계약전체": "CONTRACT",
        "contract": "CONTRACT",
        "특약": "RIDER",
        "담보": "RIDER",
        "rider": "RIDER",
        "조항": "CLAUSE",
        "clause": "CLAUSE",
    }.get(scalar("scope_kind") or "")
    required = ["contract_number", "insurer", "new_terms_code", "new_edition_code"]
    if operation == "REPLACE":
        required += ["previous_terms_code", "previous_edition_code"]
    if scope == "RIDER":
        required.append("rider_name")
    if scope == "CLAUSE" and (operation != "ADD" or scalar("new_clause_label") is None):
        required.append("clause_label")
    if (
        any(scalar(name) is None for name in required)
        or operation is None
        or change_kind is None
        or scope is None
    ):
        reasons.append("CHANGE_TARGET_OR_OPERATION_UNRESOLVED")
    if scope == "CONTRACT" and (
        scalar("rider_name") or scalar("clause_label") or scalar("new_clause_label")
    ):
        reasons.append("CHANGE_SCOPE_CONFLICT")
    if scope == "RIDER" and (scalar("clause_label") or scalar("new_clause_label")):
        reasons.append("CHANGE_SCOPE_CONFLICT")
    if "new_clause_label" in declared and scalar("new_clause_label") is None:
        reasons.append("NEW_CLAUSE_LABEL_UNRESOLVED")
    start, end = _date(scalar("effective_from")), _date(scalar("effective_through"))
    if start is None:
        reasons.append("EFFECTIVE_DATE_UNRESOLVED")
    if "effective_through" in declared and end is None:
        reasons.append("EFFECTIVE_END_UNRESOLVED")
    if start is not None and end is not None and end < start:
        reasons.append("EFFECTIVE_PERIOD_CONFLICT")
    number = scalar("contract_number")
    return ObservedTermsChange(
        status="NO_MATCH" if wrong_member else "UNKNOWN" if reasons else "MATCH",
        reason_codes=tuple(dict.fromkeys(reasons)),
        contract_number_sha256=hashlib.sha256(number.encode()).hexdigest() if number else None,
        family_member_id=member_id,
        member_version=member_version,
        insurer_key=scalar("insurer"),
        scope_kind=scope,
        rider_name_key=scalar("rider_name"),
        clause_label_key=scalar("clause_label"),
        new_clause_label_key=scalar("new_clause_label"),
        new_clause_label_declared="new_clause_label" in declared,
        operation=operation,
        change_kind=change_kind,
        effective_from=start,
        effective_through=end,
        previous_terms_code=scalar("previous_terms_code"),
        previous_edition_code=scalar("previous_edition_code"),
        new_terms_code=scalar("new_terms_code"),
        new_edition_code=scalar("new_edition_code"),
        source_fields=fields,
    )
