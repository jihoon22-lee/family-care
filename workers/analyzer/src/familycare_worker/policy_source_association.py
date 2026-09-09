"""Resolve insured and contract scope locally without provider-visible identifiers."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Literal
from uuid import UUID, uuid5

import psycopg

from familycare_worker.document_structure import DocumentStructure, StructureNode, node_source_roles

_TYPE_LABELS = {
    "insured": re.compile(r"^(?:피보험자(?: 성명)?|insured(?: name)?)$", re.IGNORECASE),
    "contract": re.compile(r"^(?:증권번호|계약번호|policy number|contract number)$", re.IGNORECASE),
}
_QUALIFIED_INSURED = re.compile(r"(?P<name>[^()]+)\((?P<qualifier>[^()]+)\)")
_DATE_QUALIFIER = re.compile(
    r"(?P<year>[0-9]{4})(?P<separator>[-./])(?P<month>[0-9]{2})"
    r"(?P=separator)(?P<day>[0-9]{2})"
)
_MASKED_ID_QUALIFIER = re.compile(
    r"(?P<year>[0-9]{2})(?P<month>[0-9]{2})(?P<day>[0-9]{2})-"
    r"(?P<code>[1-8*])\*{6}"
)


@dataclass(frozen=True, repr=False)
class LocalMember:
    id: UUID
    display_name: str
    internal_alias: str
    version: int


@dataclass(frozen=True)
class AnchorRef:
    node_id: str
    page: int
    start: int
    end: int
    kind: str

    def to_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "page": self.page,
            "start": self.start,
            "end": self.end,
            "kind": self.kind,
        }


@dataclass(frozen=True, repr=False)
class SourceAssociation:
    state: Literal["RESOLVED", "UNRESOLVED", "AMBIGUOUS", "WRONG_MEMBER"]
    contract_scope_id: UUID | None = None
    family_member_id: UUID | None = None
    member_version: int | None = None
    anchor_refs: tuple[AnchorRef, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "contract_scope_id": str(self.contract_scope_id) if self.contract_scope_id else None,
            "family_member_id": str(self.family_member_id) if self.family_member_id else None,
            "member_version": self.member_version,
            "anchor_refs": [item.to_dict() for item in self.anchor_refs],
        }


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _recognized_insured_qualifier(value: str) -> bool:
    """Recognize a complete date/masked-ID shape without emitting its values as facts."""
    matched = _DATE_QUALIFIER.fullmatch(value)
    if matched is not None:
        year = int(matched["year"])
    else:
        matched = _MASKED_ID_QUALIFIER.fullmatch(value)
        if matched is None:
            return False
        # A fully masked suffix leaves the century unknown. Use a leap-capable
        # century only for calendar-shape validation; no birth date is inferred.
        century = 1900 if matched["code"] in {"1", "2", "5", "6"} else 2000
        year = century + int(matched["year"])
    try:
        date(year, int(matched["month"]), int(matched["day"]))
    except ValueError:
        return False
    return True


def _insured_matches(value: str, names: dict[str, set[UUID]]) -> set[UUID]:
    direct = names.get(value)
    if direct is not None:
        return direct
    matched = _QUALIFIED_INSURED.fullmatch(value)
    if matched is None:
        return set()
    identities = names.get(matched["name"].strip())
    if identities is None or not _recognized_insured_qualifier(matched["qualifier"].strip()):
        return set()
    # Only the lookup changes. The caller retains the complete original anchor
    # and never stores a parsed identifier, date, or new member alias.
    return identities


def _anchors(node: StructureNode) -> list[tuple[str, str, AnchorRef]]:
    if "LINE_COLUMN_CONTEXT_UNRESOLVED" in node.issue_codes:
        return []
    result = []
    offset = 0
    for line in node.text.splitlines(keepends=True):
        label, separator, value = re.sub("：", ":", line).partition(":")
        if separator:
            for kind, pattern in _TYPE_LABELS.items():
                if pattern.fullmatch(label.strip()) and value.strip():
                    result.append(
                        (
                            kind,
                            _normalize(value),
                            AnchorRef(
                                node.node_id,
                                node.page_number,
                                offset,
                                offset + len(line.rstrip()),
                                kind,
                            ),
                        )
                    )
        offset += len(line)
    if node.kind == "TABLE_ROW":
        for position, cell in enumerate(node.cells[:-1]):
            value_cell = node.cells[position + 1]
            if (
                value_cell.column_index != cell.column_index + 1
                or value_cell.row_index != cell.row_index
                or any(
                    span not in (None, 1)
                    for span in (
                        cell.row_span,
                        cell.column_span,
                        value_cell.row_span,
                        value_cell.column_span,
                    )
                )
            ):
                continue
            for kind, pattern in _TYPE_LABELS.items():
                if pattern.fullmatch(cell.text.strip()) and node.cells[position + 1].text.strip():
                    # This reference addresses the complete local row; cells remain in the IR.
                    result.append(
                        (
                            kind,
                            _normalize(node.cells[position + 1].text),
                            AnchorRef(
                                node.node_id,
                                node.page_number,
                                0,
                                len(node.text),
                                kind,
                            ),
                        )
                    )
    return result


def associate_policy_sources(
    structure: DocumentStructure,
    *,
    members: Sequence[LocalMember],
    expected_member_id: UUID,
) -> dict[str, SourceAssociation]:
    names: dict[str, set[UUID]] = {}
    by_id = {item.id: item for item in members}
    for member in members:
        for value in (member.display_name, member.internal_alias):
            if value.strip():
                names.setdefault(_normalize(value), set()).add(member.id)
    line_children = {span.block_node_id for node in structure.nodes for span in node.source_spans}
    anchors = {
        node.node_id: [] if node.node_id in line_children else _anchors(node)
        for node in structure.nodes
    }
    page_anchors = {
        page.page_number: [anchor for key in page.node_ids for anchor in anchors[key]]
        for page in structure.pages
    }
    contracts = {
        page: {value for kind, value, _ in items if kind == "contract"}
        for page, items in page_anchors.items()
    }
    roles = {page.page_number: page.role for page in structure.pages}
    result: dict[str, SourceAssociation] = {}
    for page in structure.pages:
        numbers = contracts[page.page_number]
        association = SourceAssociation("UNRESOLVED")
        if len(numbers) > 1:
            association = SourceAssociation("AMBIGUOUS")
        elif len(numbers) == 1 and expected_member_id in by_id:
            number = next(iter(numbers))
            linked = [
                anchor
                for page_number, items in page_anchors.items()
                if contracts[page_number] == {number} and roles[page_number] == "policy"
                for anchor in items
            ]
            insured = [(value, ref) for kind, value, ref in linked if kind == "insured"]
            matches = [_insured_matches(value, names) for value, _ in insured]
            identities = set().union(*matches) if matches else set()
            if any(len(item) > 1 for item in matches) or len(identities) > 1:
                association = SourceAssociation("AMBIGUOUS")
            elif not matches or any(not item for item in matches):
                association = SourceAssociation("UNRESOLVED")
            elif identities != {expected_member_id}:
                association = SourceAssociation("WRONG_MEMBER")
            else:
                local_contract = next(
                    ref for kind, _, ref in page_anchors[page.page_number] if kind == "contract"
                )
                association = SourceAssociation(
                    "RESOLVED",
                    uuid5(structure.lineage.document_version_id, f"local-contract-v1:{number}"),
                    expected_member_id,
                    by_id[expected_member_id].version,
                    (local_contract, insured[0][1]),
                )
        for node_id in page.node_ids:
            result[node_id] = association
    effective_roles = node_source_roles(structure)
    tables: dict[str, list[StructureNode]] = {}
    for node in structure.nodes:
        if node.table_id is not None:
            tables.setdefault(node.table_id, []).append(node)
    for node in sorted(structure.nodes, key=lambda item: item.page_number):
        if (
            roles[node.page_number] != "unknown"
            or effective_roles[node.node_id] != "policy"
            or node.continuation_of is None
        ):
            continue
        parents = tables.get(node.continuation_of, ())
        links = [result[parent.node_id] for parent in parents]
        if not links or any(link.state != "RESOLVED" for link in links):
            continue
        first = links[0]
        if any(link.contract_scope_id != first.contract_scope_id for link in links):
            continue
        own_insured = [
            value for kind, value, _ in page_anchors[node.page_number] if kind == "insured"
        ]
        if any(_insured_matches(value, names) != {expected_member_id} for value in own_insured):
            result[node.node_id] = SourceAssociation("AMBIGUOUS")
            continue
        own = result[node.node_id]
        if contracts[node.page_number] and (
            own.state != "RESOLVED" or own.contract_scope_id != first.contract_scope_id
        ):
            result[node.node_id] = SourceAssociation("AMBIGUOUS")
            continue
        result[node.node_id] = replace(
            first,
            anchor_refs=(
                *first.anchor_refs,
                AnchorRef(node.node_id, node.page_number, 0, len(node.text), "continuation"),
            ),
        )
    return result


def load_local_members(
    connection: psycopg.Connection[dict[str, Any]],
    household_space_id: UUID,
) -> tuple[LocalMember, ...]:
    connection.execute(
        "SELECT id FROM household_spaces WHERE id = %s FOR UPDATE", (household_space_id,)
    )
    rows = connection.execute(
        "SELECT id, display_name, internal_alias, version FROM family_members "
        "WHERE household_space_id = %s AND deleted_at IS NULL ORDER BY id FOR SHARE",
        (household_space_id,),
    ).fetchall()
    return tuple(
        LocalMember(row["id"], row["display_name"], row["internal_alias"], row["version"])
        for row in rows
    )


def member_identity_fingerprint(members: Sequence[LocalMember]) -> str:
    values = [
        (str(item.id), item.display_name, item.internal_alias, item.version)
        for item in sorted(members, key=lambda item: str(item.id))
    ]
    return hashlib.sha256(json.dumps(values, ensure_ascii=True).encode()).hexdigest()
