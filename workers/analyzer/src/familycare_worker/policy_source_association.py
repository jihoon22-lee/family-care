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
_DATE_QUALIFIER = re.compile(
    r"(?P<year>[0-9]{4})(?P<separator>[-./])(?P<month>[0-9]{2})"
    r"(?P=separator)(?P<day>[0-9]{2})"
)
_MASKED_ID_QUALIFIER = re.compile(
    r"(?P<year>[0-9]{2})(?P<month>[0-9]{2})(?P<day>[0-9]{2})-"
    r"(?P<code>[1-8*])\*{6}"
)
_DEMOGRAPHIC_TOKEN = re.compile(
    r"(?P<identity>[0-9]{4}[-./][0-9]{2}[-./][0-9]{2}|[0-9]{6}-[1-8*]\*{6})"
    r"|(?P<gender>남성|여성|남자|여자|남|여|male|female)"
    r"|(?P<age>(?:만\s*)?(?P<years>[0-9]{1,3})\s*세)"
)
_CERTIFICATE_PERSON_FIELDS = re.compile(
    r"(?P<name>[^()]+)\((?P<identity>[0-9]{6}-[1-8*]\*{6})\)\s*/\s*"
    r"(?:만\s*)?(?P<age>[0-9]{1,3})\s*세\s*/\s*"
    r"(?:남성|여성|남자|여자|남|여|male|female)\s*/\s*"
    r"\((?P<class>[0-9]{1,2})(?:급|종)\)"
    r"(?:\s+(?P<description>(?:[^()]|\([^()]*\))*))?"
)
_ADDITIONAL_PERSON_FIELD = re.compile(
    "|".join(
        r"\s*".join(label)
        for label in ("피보험자", "계약자", "수익자", "성명", "이름", "생년월일", "주민등록번호")
    )
    + r"|\b(?:insured|policyholder|beneficiary|name|birth|resident)\b|"
    r"[0-9]{6}\s*-\s*[0-9*]|[0-9]{4}\s*[-./년]\s*[0-9]{1,2}\s*[-./월]"
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
    direct = names.get(_normalize(value))
    if direct is not None:
        return direct
    certificate = _CERTIFICATE_PERSON_FIELDS.fullmatch(value) if "\n" not in value else None
    if certificate is not None:
        description = certificate["description"] or ""
        if (
            not _recognized_insured_qualifier(certificate["identity"])
            or int(certificate["age"]) > 130
            or int(certificate["class"]) == 0
            or len(description) > 500
            or _ADDITIONAL_PERSON_FIELD.search(description)
            or any(
                re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", description) for name in names
            )
        ):
            return set()
        # The complete name is an independently delimited field. The final
        # class description is not identity evidence and creates no new facts.
        return names.get(certificate["name"].strip(), set())
    identities: set[UUID] = set()
    for name, members in names.items():
        if not value.startswith(name):
            continue
        suffix = value[len(name) :]
        if (
            suffix
            and (suffix[0].isspace() or suffix[0] == "(")
            and _recognized_demographic_suffix(suffix.strip())
        ):
            identities.update(members)
    # Only the lookup changes. The caller retains the complete original anchor
    # and never stores a parsed identifier, date, or new member alias.
    return identities


def _recognized_demographic_suffix(value: str) -> bool:
    """Consume the complete suffix; typed fields are discarded, never identity facts."""
    kinds: set[str] = set()
    position = 0
    group_open = False
    group_has_token = False
    previous = "start"
    while position < len(value):
        char = value[position]
        if char.isspace():
            if previous == "token":
                previous = "space"
            position += 1
            continue
        if char == "(":
            if group_open:
                return False
            group_open, group_has_token, previous = True, False, "open"
        elif char == ")":
            if not group_open or not group_has_token or previous in {"open", "slash"}:
                return False
            group_open, previous = False, "close"
        elif char == "/":
            if previous not in {"token", "space", "close"}:
                return False
            previous = "slash"
        else:
            if previous == "token":
                return False
            token = _DEMOGRAPHIC_TOKEN.match(value, position)
            if token is None:
                return False
            kind = "age" if token["age"] is not None else token.lastgroup
            if kind is None or kind in kinds:
                return False
            if kind == "identity" and not _recognized_insured_qualifier(token[0]):
                return False
            if kind == "age" and int(token["years"]) > 130:
                return False
            kinds.add(kind)
            group_has_token, previous = True, "token"
            position = token.end()
            continue
        position += 1
    return not group_open and previous not in {"open", "slash"} and "identity" in kinds


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
                            "\n".join(
                                _normalize(line) for line in value_cell.text.strip().splitlines()
                            )
                            if kind == "insured"
                            else _normalize(value_cell.text),
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
