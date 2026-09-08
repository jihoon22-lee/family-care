"""Independently recognize a complete navigation page from retained source nodes.

The caller must provide the complete page of the selected immutable generation.
A navigation page neither ends an earlier reference document nor supplies a
component, insurer, edition or enrollment identity.
"""

import re
import unicodedata
from typing import Any

from familycare_api.insurance_documents.terms_body_validation import _box, _lineage_valid

_HEADING = re.compile(r"^(?:목\s*차|차\s*례|table\s+of\s+contents)$", re.I)
_ENTRY = re.compile(r"^(?P<label>\S[^\n]{0,238}?)(?:\.{2,}|…+|·{2,})\s*(?P<page>[1-9][0-9]{0,2})$")
_NUMBER = re.compile(r"^-?\s*(?P<page>[1-9][0-9]{0,2})\s*-?$")
_SENTENCE = re.compile(r"[.!?。]$|(?:합니다|한다|습니다|말한다|이다)$|\b(?:shall|must)\b", re.I)


def is_navigation_page(nodes: list[dict[str, Any]]) -> bool:
    try:
        return _is_navigation_page(nodes)
    except KeyError, TypeError, ValueError, AttributeError, IndexError, OverflowError:
        return False


def _is_navigation_page(nodes: list[dict[str, Any]]) -> bool:
    if not 1 <= len(nodes) <= 4096 or sum(len(node["text"]) for node in nodes) > 262144:
        return False
    by_id = {node["node_id"]: node for node in nodes}
    if len(by_id) != len(nodes):
        return False
    if len({(node["kind"], node["source_path"]) for node in nodes}) != len(nodes):
        return False
    pages = {node["page_number"] for node in nodes}
    layers = {node["source_layer"] for node in nodes}
    if (
        len(pages) != 1
        or any(type(page) is not int or not 1 <= page <= 500 for page in pages)
        or len(layers) != 1
        or not layers <= {"native", "ocr"}
    ):
        return False
    represented: set[str] = set()
    for node in nodes:
        if node["kind"] not in {"BLOCK", "TEXT_LINE"}:
            return False
        _box(node)
        if (
            not isinstance(node["node_id"], str)
            or not 1 <= len(node["node_id"]) <= 128
            or not isinstance(node["source_path"], str)
            or not node["source_path"]
            or type(node.get("schedulable")) is not bool
            or type(node["page_number"]) is not int
            or node.get("issue_codes", [])
            or type(node["reading_order"]) is not int
            or node["reading_order"] < 0
        ):
            return False
        if node["kind"] != "TEXT_LINE":
            continue
        if not _lineage_valid(node, by_id) or len(node["source_spans"]) > 4096:
            return False
        for span in node["source_spans"]:
            if any(
                type(span[key]) is not int
                for key in ("block_start", "block_end", "line_start", "line_end")
            ):
                return False
            identifier = span["block_node_id"]
            block = by_id[identifier]
            if (
                identifier in represented
                or block["text"][: span["block_start"]].strip()
                or block["text"][span["block_end"] :].strip()
            ):
                return False
            represented.add(identifier)
    active = sorted(
        (node for node in nodes if node["node_id"] not in represented and node["text"].strip()),
        key=lambda node: node["reading_order"],
    )
    if any(not node.get("schedulable", True) for node in active):
        return False
    for previous, current in zip(active, active[1:], strict=False):
        if (
            current["reading_order"] <= previous["reading_order"]
            or _box(current)[1] < _box(previous)[3]
        ):
            return False
    heading = False
    entries = 0
    lines = 0
    for node in active:
        for raw in node["text"].splitlines():
            text = unicodedata.normalize("NFKC", raw.strip()).strip("[](){}【】<> ")
            if not text:
                continue
            lines += 1
            if lines > 4096:
                return False
            number = _NUMBER.fullmatch(text)
            if number and int(number["page"]) <= 500:
                continue
            if _HEADING.fullmatch(text):
                if heading:
                    return False
                heading = True
                continue
            entry = _ENTRY.fullmatch(text)
            if (
                not heading
                or entry is None
                or int(entry["page"]) > 500
                or _SENTENCE.search(entry["label"].strip())
            ):
                return False
            entries += 1
    return heading and entries > 0
