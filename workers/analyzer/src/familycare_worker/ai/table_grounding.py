"""Local column proofs; their labels and values are never substituted into provider input."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from familycare_worker.ai.policy_ranges import RangeEvidenceSlice
from familycare_worker.ai.schemas import CandidateField, PolicyCandidate

_UNIT = r"백만원|억원|만원|천원|원|KRW|USD|EUR|JPY"
_AMOUNT_HEADER = re.compile(
    rf"(?:보험가입금액|가입금액|sum\s+assured)\s*(?:[（(\[]\s*({_UNIT})\s*[）)\]])?",
    re.IGNORECASE,
)
_LABELS = {
    "benefit_type": {"보장구분", "보장유형", "급부유형", "benefittype"},
    "coverage_start": {"보장개시일", "보장시작일", "coveragestart"},
    "coverage_end": {"보장종료일", "보장만기일", "coverageend"},
    "renewable": {"갱신여부", "갱신구분", "renewable"},
}
_RENDER_LABEL = {"coverage_start": "보장개시일", "coverage_end": "보장종료일"}
_NAME = {"담보명", "특약명", "보장명", "ridername"}


def explicitly_unenrolled(text: str) -> bool:
    """Recognize row statuses and scoped example notices, not conditional disclaimers."""
    for part in re.split(r"[\n\t|]", text):
        value = part.strip()
        if re.fullmatch(
            r"(?:미가입|미선택|가입\s*예시|not enrolled|example only)", value, re.IGNORECASE
        ):
            return True
        if re.search(
            r"\s(?:미가입|미선택|가입\s*예시|not\s+enrolled|example\s+only)$", value, re.IGNORECASE
        ):
            return True
        if re.search(r"\((?:미가입|미선택|가입\s*예시)\)", value):
            return True
        if re.match(
            r"(?:이|해당)\s*(?:표|담보|특약)(?:는|은)?\s*(?:가입\s*예시|미가입|가입하지\s*않)",
            value,
        ):
            return True
        if re.fullmatch(r"미가입\s*(?:담보|특약)\s*(?:목록|내역)?", value):
            return True
    return False


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _label(value: str) -> str:
    return re.sub(r"[\s:：]", "", _normalize(value))


def _cells(node: Mapping[str, Any]) -> dict[int, Mapping[str, Any]] | None:
    cells = node.get("cells", ())
    if any(
        cell.get("row_span") not in (None, 1)
        or cell.get("column_span") not in (None, 1)
        or cell.get("row_index") != node.get("row_index")
        for cell in cells
    ):
        return None
    result = {cell["column_index"]: cell for cell in cells}
    return result if len(result) == len(cells) else None


@dataclass(frozen=True)
class TableFieldProof:
    text: str
    evidence_ids: tuple[UUID, ...]
    issue_code: str | None = None


def table_field_proof(
    candidate: PolicyCandidate,
    field: CandidateField,
    cited: Sequence[RangeEvidenceSlice],
    evidence: Sequence[RangeEvidenceSlice],
    nodes: Mapping[str, Mapping[str, Any]],
) -> TableFieldProof | None:
    """None means plain text; an empty proof means unsupported/conflicting table structure."""
    if candidate.candidate_kind != "rider":
        return None
    name_ids = {
        key
        for field in candidate.fields
        if field.field_id == "rider_name"
        for key in field.evidence_ids
    }
    name_rows = {
        item.node_id
        for item in evidence
        if item.evidence_id in name_ids
        and item.primary
        and nodes.get(item.node_id, {}).get("kind") == "TABLE_ROW"
    }
    primary = [
        item
        for item in cited
        if item.primary
        and item.source_role == "policy"
        and nodes.get(item.node_id, {}).get("kind") == "TABLE_ROW"
        and nodes[item.node_id].get("row_role") == "data"
    ]
    if not primary:
        if name_rows:
            return TableFieldProof("", ())
        if any(
            item.primary and nodes.get(item.node_id, {}).get("kind") == "TABLE_ROW"
            for item in cited
        ):
            return TableFieldProof("", ())
        return None
    empty = TableFieldProof("", ())
    if len({item.node_id for item in primary}) != 1:
        return empty
    if {item.node_id for item in primary} != name_rows:
        return empty
    row = nodes[primary[0].node_id]
    if any(item.start != 0 or item.end != len(row["text"]) for item in primary):
        return empty
    if explicitly_unenrolled(row["text"]) or any(
        explicitly_unenrolled(nodes.get(key, {}).get("text", ""))
        for key in row.get("context_node_ids", ())
    ):
        return TableFieldProof("", (), "NOT_ENROLLED")
    cells = _cells(row)
    if cells is None:
        return empty
    name = next((f.value for f in candidate.fields if f.field_id == "rider_name"), None)
    if not isinstance(name, str):
        return empty
    available = {item.node_id: item for item in evidence}
    headers = []
    for key in row.get("context_node_ids", ()):
        header = nodes.get(key, {})
        if header.get("kind") == "TABLE_ROW" and header.get("row_role") == "header":
            if key not in available:
                return empty
            values = _cells(header)
            if (
                values is None
                or available[key].start != 0
                or available[key].end != len(header["text"])
            ):
                return empty
            headers.append((header, values, available[key]))
    name_columns = {
        column
        for _, columns, _ in headers
        for column, cell in columns.items()
        if _label(cell["text"]) in _NAME
    }
    if len(name_columns) != 1:
        return empty
    name_column = next(iter(name_columns))
    if name_column not in cells or _normalize(cells[name_column]["text"]) != _normalize(name):
        return empty
    if any(
        name_column in columns and _label(columns[name_column]["text"]) not in _NAME
        for _, columns, _ in headers
    ):
        return empty
    if field.field_id in {"rider_name", "rider_key"}:
        return TableFieldProof(name, tuple(item.evidence_id for _, _, item in headers))
    if field.field_id in {"rider_status", "benefit_type"} and field.value == "unknown":
        return TableFieldProof(name, ())
    matched = []
    for _header, columns, item in headers:
        for column, cell in columns.items():
            unit_match = _AMOUNT_HEADER.fullmatch(cell["text"].strip())
            if (field.field_id in {"sum_assured", "currency"} and unit_match is not None) or (
                _label(cell["text"]) in _LABELS.get(field.field_id, set())
            ):
                if column not in cells:
                    return empty
                matched.append((cells[column]["text"], unit_match, item, column))
    if not matched:
        if field.field_id == "benefit_type":
            return TableFieldProof(
                name, tuple(item.evidence_id for _, _, item in headers), "UNCLASSIFIED_BENEFIT_TYPE"
            )
        return empty
    if len({column for _, _, _, column in matched}) != 1:
        return empty
    value, amount_header, _, column = matched[0]
    for _, columns, _ in headers:
        if column not in columns:
            return empty
        label = columns[column]["text"].strip()
        if field.field_id in {"sum_assured", "currency"}:
            if _AMOUNT_HEADER.fullmatch(label) is None:
                return empty
        elif _label(label) not in _LABELS.get(field.field_id, set()):
            return empty
    support = {item.evidence_id for _, _, item, _ in matched}
    support.update(
        item.evidence_id
        for _, columns, item in headers
        if name_column in columns and _label(columns[name_column]["text"]) in _NAME
    )
    if field.field_id in {"sum_assured", "currency"}:
        assert amount_header is not None
        hints = set()
        for _, match, _, _ in matched:
            if match is not None and match.group(1):
                hints.add(match.group(1).upper())
        for key in row.get("context_node_ids", ()):
            node = nodes.get(key, {})
            unit = re.fullmatch(
                rf"\s*(?:단위|unit)\s*[:：]\s*({_UNIT})\s*", node.get("text", ""), re.IGNORECASE
            )
            if unit is not None:
                if (
                    key not in available
                    or available[key].start != 0
                    or available[key].end != len(node.get("text", ""))
                ):
                    return empty
                hints.add(unit.group(1).upper())
                support.add(available[key].evidence_id)
        cell_match = re.fullmatch(
            rf"\s*([0-9]+(?:,[0-9]{{3}})*(?:\.[0-9]+)?)\s*({_UNIT})?\s*", value, re.IGNORECASE
        )
        if cell_match is None:
            return empty
        if cell_match.group(2):
            hints.add(cell_match.group(2).upper())
        if len(hints) != 1:
            return empty
        text = f"{name} 가입금액 {cell_match.group(1)}{next(iter(hints))}"
    else:
        text = f"{name} {_RENDER_LABEL.get(field.field_id, '')} {value}"
    return TableFieldProof(text, tuple(sorted(support, key=str)))
