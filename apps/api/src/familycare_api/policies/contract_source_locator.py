"""Recover opaque same-content contract identity from a resolved local anchor.

The caller owns household authorization and enrollment authority. This helper
only checks the immutable source association and never emits contract text.
OCR is permitted: exact resolved text plus its original scope UUID is required,
and identity equivalence does not promote OCR quality or enrollment status.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping
from itertools import pairwise
from typing import Any
from uuid import UUID, uuid5

_CONTRACT_LABEL = re.compile(
    r"^(?:증\s*권\s*번\s*호|계\s*약\s*번\s*호|policy\s+number|contract\s+number)$",
    re.IGNORECASE,
)


def _normalize(value: str) -> str:
    """Match the Worker's contract association normalization exactly."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _line_numbers(text: str) -> list[tuple[int, int, str]]:
    result = []
    offset = 0
    for line in text.splitlines(keepends=True):
        label, separator, value = line.replace("：", ":").partition(":")
        if separator and _CONTRACT_LABEL.fullmatch(label.strip()) and value.strip():
            result.append((offset, offset + len(line.rstrip()), _normalize(value)))
        offset += len(line)
    return result


def _table_numbers(node: Mapping[str, Any]) -> list[tuple[int, int, str]] | None:
    if node.get("row_role") not in {None, "data", "unresolved"}:
        return None
    cells = node.get("cells", ())
    positions = []
    for cell in cells:
        if (
            type(cell.get("row_index")) is not int
            or type(cell.get("column_index")) is not int
            or cell["row_index"] != node.get("row_index")
            or cell["column_index"] < 0
            or cell.get("row_span") not in (None, 1)
            or cell.get("column_span") not in (None, 1)
            or not isinstance(cell.get("text"), str)
        ):
            return None
        positions.append(cell["column_index"])
    if positions != sorted(set(positions)):
        return None
    result = []
    for cell, value_cell in pairwise(cells):
        if (
            value_cell["column_index"] == cell["column_index"] + 1
            and _CONTRACT_LABEL.fullmatch(cell["text"].strip())
            and value_cell["text"].strip()
        ):
            result.append((0, len(node["text"]), _normalize(value_cell["text"])))
    # Multiple label/value pairs share one row-level AnchorRef and cannot be
    # distinguished by its character range, even when their values coincide.
    return result if len(result) <= 1 else None


def contract_source_locator(
    structure: Mapping[str, Any], association: Mapping[str, Any]
) -> dict[str, str] | None:
    """Return version/content/member/opaque-number identity, or no proven key.

    References must exactly reproduce a Worker line or complete table-row anchor;
    a cropped prefix that happens to resemble a contract number is insufficient.
    Non-contract refs remain the caller's insured/continuation evidence.
    """
    try:
        if association.get("state") != "RESOLVED":
            return None
        member_id = UUID(str(association["family_member_id"]))
        scope_id = UUID(str(association["contract_scope_id"]))
        document_id = UUID(str(structure["lineage"]["document_version_id"]))
        digest = structure["lineage"]["content_sha256"]
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            return None
        anchors = [ref for ref in association["anchor_refs"] if ref.get("kind") == "contract"]
        if len(anchors) != 1:
            return None
        anchor = anchors[0]
        nodes = {node["node_id"]: node for node in structure["nodes"]}
        if len(nodes) != len(structure["nodes"]):
            return None
        node = nodes.get(anchor["node_id"])
        if (
            node is None
            or node.get("kind") not in {"BLOCK", "TEXT_LINE", "TABLE_ROW"}
            or node.get("source_layer") not in {"native", "ocr"}
            or "LINE_COLUMN_CONTEXT_UNRESOLVED" in node.get("issue_codes", ())
            or type(anchor.get("page")) is not int
            or anchor["page"] < 1
            or anchor["page"] != node.get("page_number")
            or not isinstance(node.get("text"), str)
        ):
            return None
        start, end = anchor.get("start"), anchor.get("end")
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(node["text"])
        ):
            return None
        numbers = _line_numbers(node["text"])
        if node["kind"] == "TABLE_ROW":
            table_numbers = _table_numbers(node)
            if table_numbers is None:
                return None
            numbers.extend(table_numbers)
        selected = {number for low, high, number in numbers if low == start and high == end}
        if len(selected) != 1:
            return None
        number = next(iter(selected))
        if uuid5(document_id, "local-contract-v1:" + number) != scope_id:
            return None
        return {
            "schema_version": "contract-source-v1",
            "content_sha256": digest,
            "family_member_id": str(member_id),
            "contract_number_sha256": hashlib.sha256(number.encode("utf-8")).hexdigest(),
        }
    except KeyError, TypeError, ValueError, AttributeError, OverflowError:
        return None
