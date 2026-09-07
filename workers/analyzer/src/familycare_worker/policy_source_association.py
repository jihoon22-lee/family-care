"""Resolve insured and contract scope locally without provider-visible identifiers."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid5

import psycopg

from familycare_worker.document_structure import DocumentStructure, StructureNode

_TYPE_LABELS = {
    "insured": re.compile(r"^(?:피보험자(?: 성명)?|insured(?: name)?)$", re.IGNORECASE),
    "contract": re.compile(r"^(?:증권번호|계약번호|policy number|contract number)$", re.IGNORECASE),
}


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


def _anchors(node: StructureNode) -> list[tuple[str, str, AnchorRef]]:
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
    anchors = {node.node_id: _anchors(node) for node in structure.nodes}
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
            matches = [names.get(value, set()) for value, _ in insured]
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
