"""Read field-specific enrollment amounts without upgrading generic ledger Evidence.

Program values must replay a supported original amount/unit witness. A published
human correction has separate authority and requires its real household approver.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import psycopg

from familycare_api.common.evidence import EvidenceRef
from familycare_api.common.scope import HouseholdScope
from familycare_api.policies.source_projection import StructureProjectionReader

AMOUNT_SOURCE_REVISION = "operational-amount-source-v1"
_LIMIT = 32
_UNIT = r"백만원|억원|만원|천원|원|KRW|USD|EUR|JPY"
_NUMBER = r"[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?"
_AMOUNT = re.compile(
    rf"(?:보험가입금액|가입금액|sum assured)\s*[:：|]?\s*({_NUMBER})\s*({_UNIT})(?!\w)",
    re.IGNORECASE,
)
_AMOUNT_HEADER = re.compile(
    rf"(?:보험가입금액|가입금액|sum\s+assured)\s*(?:[（(\[]\s*({_UNIT})\s*[）)\]])?", re.IGNORECASE
)
_UNITS = {"원": 1, "천원": 1000, "만원": 10000, "백만원": 1000000, "억원": 100000000}
_NAMES = {"담보명", "특약명", "보장명", "ridername"}
type Authority = Literal["PROGRAM_VERIFIED", "USER_CONFIRMED"]
type Decision = Literal["MATCH", "UNKNOWN"]


@dataclass(frozen=True, slots=True, repr=False)
class OperationalAmountSource:
    rider_id: UUID
    ledger_version: int | None
    amount: Decimal | None
    currency: str | None
    amount_decision: Decision
    currency_decision: Decision
    amount_authority: Authority | None
    currency_authority: Authority | None
    amount_evidence: tuple[EvidenceRef, ...]
    currency_evidence: tuple[EvidenceRef, ...]
    publication_ids: tuple[UUID, ...]
    digest_sha256: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True, repr=False)
class _Field:
    value: Decimal | str | None = None
    authority: Authority | None = None
    evidence: tuple[EvidenceRef, ...] = ()


def _digest(value: object) -> str:
    def scalar(item: object) -> str:
        if isinstance(item, datetime):
            return item.astimezone(UTC).isoformat()
        if isinstance(item, Decimal):
            return str(item.normalize())
        return str(item)

    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), default=scalar, allow_nan=False
        ).encode()
    ).hexdigest()


def _normal(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _number(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, Decimal | str | int | float):
        return None
    try:
        result = Decimal(str(value))
        return (
            result
            if result.is_finite()
            and 0 <= result < Decimal("1e16")
            and result == result.quantize(Decimal(".01"))
            else None
        )
    except ArithmeticError, ValueError:
        return None


def _currency(value: object) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[A-Z]{3}", value) else None


def _cells(node: dict[str, Any]) -> dict[int, dict[str, Any]] | None:
    cells = node.get("cells", [])
    if not cells or any(
        c.get("row_span") not in (None, 1)
        or c.get("column_span") not in (None, 1)
        or c.get("row_index") != node.get("row_index")
        or type(c.get("column_index")) is not int
        for c in cells
    ):
        return None
    values = {c["column_index"]: c for c in cells}
    return values if len(values) == len(cells) else None


def _not_enrolled(text: str) -> bool:
    return any(
        re.search(
            r"(?:^|\s|\()(?:미가입|미선택|가입\s*예시|not enrolled|example only)(?:\)|\s|$)",
            part,
            re.IGNORECASE,
        )
        for part in re.split(r"[\n\t|]", text)
    )


def _table_money(
    nodes: dict[str, dict[str, Any]], refs: list[dict[str, Any]], name: str
) -> tuple[Decimal, str] | None:
    rows = {
        r["node_id"]
        for r in refs
        if r["primary"]
        and nodes[r["node_id"]].get("kind") == "TABLE_ROW"
        and nodes[r["node_id"]].get("row_role") == "data"
    }
    if len(rows) != 1:
        return None
    row = nodes[next(iter(rows))]
    if row.get("row_role") != "data" or _not_enrolled(row["text"]):
        return None
    cells = _cells(row)
    supplied = {r["node_id"]: r for r in refs}
    headers = []
    hints = set()
    for key in row.get("context_node_ids", ()):
        node = nodes.get(key, {})
        if _not_enrolled(node.get("text", "")):
            return None
        if node.get("kind") == "TABLE_ROW" and node.get("row_role") == "header":
            if (
                key not in supplied
                or supplied[key]["start"] != 0
                or supplied[key]["end"] != len(node["text"])
            ):
                return None
            header = _cells(node)
            if header is None:
                return None
            headers.append(header)
        unit_hint = re.fullmatch(
            rf"\s*(?:단위|unit)\s*[:：]\s*({_UNIT})\s*", node.get("text", ""), re.IGNORECASE
        )
        if unit_hint:
            if (
                key not in supplied
                or supplied[key]["start"] != 0
                or supplied[key]["end"] != len(node["text"])
            ):
                return None
            hints.add(unit_hint[1].upper())
    if (
        cells is None
        or not headers
        or any(
            supplied[key]["start"] != 0 or supplied[key]["end"] != len(nodes[key]["text"])
            for key in rows
        )
    ):
        return None
    name_columns = {
        col
        for header in headers
        for col, cell in header.items()
        if re.sub(r"[\s:：]", "", _normal(cell["text"])) in _NAMES
    }
    amount_columns = {
        col
        for header in headers
        for col, cell in header.items()
        if _AMOUNT_HEADER.fullmatch(cell["text"].strip())
    }
    if len(name_columns) != 1 or len(amount_columns) != 1:
        return None
    name_col, amount_col = next(iter(name_columns)), next(iter(amount_columns))
    if (
        name_col not in cells
        or amount_col not in cells
        or _normal(cells[name_col]["text"]) != _normal(name)
    ):
        return None
    for header in headers:
        if (
            name_col not in header
            or re.sub(r"[\s:：]", "", _normal(header[name_col]["text"])) not in _NAMES
            or amount_col not in header
        ):
            return None
        match = _AMOUNT_HEADER.fullmatch(header[amount_col]["text"].strip())
        if match is None:
            return None
        if match[1]:
            hints.add(match[1].upper())
    match = re.fullmatch(
        rf"\s*({_NUMBER})\s*({_UNIT})?\s*", cells[amount_col]["text"], re.IGNORECASE
    )
    if match is None:
        return None
    if match[2]:
        hints.add(match[2].upper())
    if len(hints) != 1:
        return None
    unit = next(iter(hints))
    amount = _number(Decimal(match[1].replace(",", "")) * _UNITS.get(unit, 1))
    return (amount, "KRW" if unit in _UNITS else unit) if amount is not None else None


def _money_witness(
    nodes: dict[str, dict[str, Any]], refs: list[dict[str, Any]], name: str
) -> tuple[Decimal, str] | None:
    if any(nodes[r["node_id"]].get("kind") == "TABLE_ROW" for r in refs if r["primary"]):
        return _table_money(nodes, refs, name)
    lines = {
        line
        for ref in refs
        if ref["primary"]
        for line in nodes[ref["node_id"]]["text"][ref["start"] : ref["end"]].splitlines()
        if re.search(r"(?<!\w)" + re.escape(_normal(name)) + r"(?!\w)", _normal(line))
    }
    if len(lines) != 1:
        return None
    line = next(iter(lines))
    matches = list(_AMOUNT.finditer(line))
    if len(matches) != 1 or _not_enrolled(line):
        return None
    number, unit = matches[0][1].replace(",", ""), matches[0][2].upper()
    amount = _number(Decimal(number) * _UNITS.get(unit, 1))
    return (amount, "KRW" if unit in _UNITS else unit) if amount is not None else None


def _base_valid(rider: dict[str, Any], pub: dict[str, Any], household: UUID) -> bool:
    return (
        pub["household_space_id"]
        == pub["candidate_household"]
        == pub["source_household"]
        == pub["job_household"]
        == pub["generation_household"]
        == household
        and pub["rider_id"] == rider["id"]
        and pub["policy_contract_id"] == pub["aggregate_id"] == rider["policy_contract_id"]
        and pub["ledger_version"] == rider["version"]
        and pub["candidate_kind"] == pub["source_kind"] == "rider"
        and pub["published_at"] is not None
        and pub["candidate_deleted"] is None
        and pub["review_item_id"] == pub["source_review_item_id"]
        and pub["lineage_valid"]
        and pub["generation_document"] == pub["document_version_id"]
        and pub["generation_extraction"] == pub["extraction_id"]
        and pub["document_kind"] == "policy"
        and pub["source_deleted"] is None
        and not pub["cancelled"]
        and pub["extraction_status"] == "succeeded"
        and pub["range_state"] in {"COMPLETE", "REVIEW"}
        and pub["association_json"].get("state") == "RESOLVED"
        and pub["subject_present"]
        and pub["association_json"].get("family_member_id") == str(pub["family_member_id"])
    )


def _field_evidence(
    pub: dict[str, Any],
    key: str,
    rows: list[dict[str, Any]],
    nodes: dict[str, dict[str, Any]],
    household: UUID,
) -> tuple[tuple[EvidenceRef, ...], list[dict[str, Any]]]:
    selected = [
        r
        for r in rows
        if r["candidate_version_id"] == pub["candidate_version_id"] and r["field_id"] == key
    ]
    refs = {r["evidence_id"]: r for r in pub["source_refs"]}
    envelope = {r["evidence_id"]: r for r in pub["envelope_json"]["evidence"]}
    if (
        not 1 <= len(selected) <= 16
        or len({r["evidence_id"] for r in selected}) != len(selected)
        or len(refs) != len(pub["source_refs"])
    ):
        return (), []
    evidence = []
    addresses = []
    for row in selected:
        ref, item = refs.get(str(row["evidence_id"])), envelope.get(str(row["evidence_id"]))
        if ref is None or item is None or ref.get("node_id") not in nodes:
            return (), []
        node = nodes[ref["node_id"]]
        bbox = tuple(row[k] for k in ("x0", "y0", "x1", "y1"))
        box = (
            None
            if bbox == (None, None, None, None)
            else (
                Decimal(str(bbox[0])),
                Decimal(str(bbox[1])),
                Decimal(str(bbox[2])),
                Decimal(str(bbox[3])),
            )
        )
        source_box = item.get("bbox")
        if (
            row["household_space_id"] != household
            or row["document_kind"] != "policy"
            or row["deleted_at"] is not None
            or row["document_version_id"] != row["evidence_document"]
            or row["evidence_document"] != pub["document_version_id"]
            or row["extraction_id"] != pub["extraction_id"]
            or row["extraction_document"] != pub["document_version_id"]
            or row["content_sha256"] != row["document_hash"]
            or row["content_sha256"] != pub["content_sha256"]
            or row["extraction_status"] != "succeeded"
            or row["physical_page"] != row["evidence_page"]
            or not 1 <= row["physical_page"] <= row["page_count"]
            or (box is not None and (box[2] > row["width_points"] or box[3] > row["height_points"]))
            or ref["document_version_id"] != str(pub["document_version_id"])
            or ref["extraction_id"] != str(pub["extraction_id"])
            or any(
                ref[name] != item.get(name)
                for name in (
                    "node_id",
                    "page",
                    "start",
                    "end",
                    "primary",
                    "source_role",
                    "document_version_id",
                )
            )
            or ref["source_role"] != "policy"
            or type(ref["primary"]) is not bool
            or ref["page"] != node["page_number"]
            or ref["page"] != row["physical_page"]
            or type(ref["start"]) is not int
            or type(ref["end"]) is not int
            or not 0 <= ref["start"] < ref["end"] <= len(node["text"])
            or (None if source_box is None else tuple(Decimal(str(v)) for v in source_box)) != box
            or source_box != node.get("bbox")
            or node.get("source_layer") not in {"native", "ocr"}
        ):
            return (), []
        evidence.append(
            EvidenceRef(
                row["evidence_id"],
                pub["document_version_id"],
                pub["extraction_id"],
                pub["content_sha256"],
                row["physical_page"],
                box,
                row["review_state"],
            )
        )
        addresses.append(ref)
    return tuple(sorted(evidence, key=lambda e: str(e.evidence_id))), addresses


def _evaluate(
    pub: dict[str, Any],
    rider: dict[str, Any],
    fields: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    projection: dict[str, Any] | None,
    household: UUID,
) -> dict[str, _Field]:
    result = {"sum_assured": _Field(), "currency": _Field()}
    try:
        if not _base_valid(rider, pub, household) or projection is None:
            return result
        lineage = projection["lineage"]
        if (
            lineage["document_version_id"] != str(pub["document_version_id"])
            or lineage["extraction_id"] != str(pub["extraction_id"])
            or lineage["content_sha256"] != pub["content_sha256"]
        ):
            return result
        if (
            len(projection["nodes"]) > 4096
            or sum(len(n["text"]) for n in projection["nodes"]) > 262144
        ):
            return result
        nodes = {n["node_id"]: n for n in projection["nodes"]}
        if len(nodes) != len(projection["nodes"]):
            return result
        selected = [r for r in fields if r["candidate_version_id"] == pub["candidate_version_id"]]
        values = {r["field_id"]: r["value"] for r in selected}
        if len(values) != len(selected) or values != pub["field_values"]:
            return result
        program = (
            pub["authority"] == "PROGRAM_VERIFIED"
            and pub["candidate_status"] == "AI_VERIFIED"
            and pub["candidate_version_id"] == pub["source_candidate_version_id"]
            and pub["actor_id"] is None
        )
        manual = (
            pub["authority"] == "USER_CONFIRMED"
            and pub["candidate_status"] == "USER_CONFIRMED"
            and isinstance(pub["actor_id"], UUID)
            and pub["actor_id"].int != 0
            and pub["actor_household"] == household
            and pub["parent_version_id"] is not None
        )
        if not program and not manual:
            return result
        retained: dict[str, Any] = {}
        if program:
            document = pub["result_json"]
            matches = [
                c
                for c in document["result"]["candidates"]
                if c["candidate_id"] == str(pub["provider_candidate_id"])
            ]
            if (
                document.get("program_validation_version")
                not in {"range-grounding-v2", "range-grounding-v3", "range-grounding-v4"}
                or len(matches) != 1
                or matches[0]["status"] != "AI_VERIFIED"
                or matches[0]["candidate_kind"] != "rider"
            ):
                return result
            retained = {f["field_id"]: f for f in matches[0]["fields"]}
            if (
                len(retained) != len(matches[0]["fields"])
                or {k: f["value"] for k, f in retained.items()} != values
            ):
                return result
        name = values.get("rider_name")
        if not isinstance(name, str) or name != rider["display_name"]:
            return result
        for key, value in (
            ("sum_assured", _number(rider["insured_amount"])),
            ("currency", _currency(rider["currency"])),
        ):
            candidate_value = (
                _number(values.get(key)) if key == "sum_assured" else _currency(values.get(key))
            )
            if value is None or candidate_value != value:
                continue
            try:
                refs, addresses = _field_evidence(pub, key, evidence, nodes, household)
                if not refs:
                    continue
                if program:
                    if key not in retained or {str(ref.evidence_id) for ref in refs} != set(
                        retained[key]["evidence_ids"]
                    ):
                        continue
                    witness = _money_witness(nodes, addresses, name)
                    if witness is None or witness[0 if key == "sum_assured" else 1] != value:
                        continue
                elif any(ref.review_state not in {"AI_VERIFIED", "USER_CONFIRMED"} for ref in refs):
                    continue
                result[key] = _Field(
                    value, "PROGRAM_VERIFIED" if program else "USER_CONFIRMED", refs
                )
            except KeyError, TypeError, ValueError, ArithmeticError:
                continue
    except KeyError, TypeError, ValueError, ArithmeticError:
        return {"sum_assured": _Field(), "currency": _Field()}
    return result


def read_operational_amount_source(
    connection: psycopg.Connection[dict[str, Any]], scope: HouseholdScope, rider_id: UUID
) -> OperationalAmountSource:
    """Return independently proved fields from one current ledger publication, never mix units."""
    if not isinstance(rider_id, UUID) or rider_id.int == 0:
        raise ValueError("GUIDANCE_AMOUNT_SOURCE_SCOPE_INVALID")
    rider = connection.execute(
        "/* amount-ledger */ SELECT r.id,r.household_space_id,r.policy_contract_id,r.version,"
        "r.insured_amount,r.currency,r.display_name,r.source_evidence_id FROM riders r "
        "JOIN policy_contracts p ON p.id=r.policy_contract_id AND "
        "p.household_space_id=r.household_space_id "
        "WHERE r.id=%s AND r.household_space_id=%s AND r.deleted_at IS NULL AND p.deleted_at IS "
        "NULL",
        (rider_id, scope.household_space_id),
    ).fetchone()
    publications = (
        []
        if rider is None
        else connection.execute(
            "/* amount-publications */ SELECT p.*,c.status AS candidate_status,c.candidate_kind,"
            "c.household_space_id AS candidate_household,c.aggregate_id,c.published_at,"
            "c.deleted_at AS candidate_deleted,c.actor_id,c.parent_version_id,c.review_item_id,"
            "actor.household_space_id AS actor_household,root.household_space_id AS "
            "source_household,"
            "root.review_item_id AS source_review_item_id,root.candidate_kind AS source_kind,"
            "s.provider_candidate_id,s.source_refs,s.association_json,j.family_member_id,"
            "j.document_version_id,j.extraction_id,j.household_space_id AS job_household,"
            "plan.generation_id,g.identity_sha256,g.cancelled,g.household_space_id AS "
            "generation_household,"
            "g.document_version_id AS generation_document,g.extraction_id AS generation_extraction,"
            "v.content_sha256,d.deleted_at AS source_deleted,d.document_kind,x.status AS "
            "extraction_status,"
            "range.envelope_json,range.result_json,range.state AS range_state,"
            "EXISTS(SELECT 1 FROM policy_parties party WHERE "
            "party.policy_contract_id=p.policy_contract_id "
            "AND party.household_space_id=p.household_space_id AND "
            "party.family_member_id=j.family_member_id "
            "AND party.role='primary_insured' AND party.deleted_at IS NULL) AS subject_present,"
            "EXISTS(WITH RECURSIVE lineage AS (SELECT c.id,c.parent_version_id,ARRAY[c.id] AS path "
            "UNION ALL SELECT ancestor.id,ancestor.parent_version_id,child.path||ancestor.id "
            "FROM analysis_candidate_versions ancestor JOIN lineage child ON "
            "ancestor.id=child.parent_version_id "
            "WHERE ancestor.household_space_id=c.household_space_id AND "
            "ancestor.review_item_id=c.review_item_id "
            "AND NOT ancestor.id=ANY(child.path) AND cardinality(child.path)<128) "
            "SELECT 1 FROM lineage WHERE id=root.id) AS lineage_valid "
            "FROM range_enrollment_publications p JOIN analysis_candidate_versions c ON "
            "c.id=p.candidate_version_id "
            "JOIN analysis_candidate_versions root ON root.id=p.source_candidate_version_id "
            "JOIN policy_range_candidate_sources s ON s.candidate_version_id=root.id "
            "JOIN policy_structuring_jobs j ON j.id=s.job_id "
            "JOIN document_policy_range_plans plan ON plan.job_id=j.id "
            "JOIN document_policy_ranges range ON range.job_id=s.job_id AND "
            "range.envelope_id=s.envelope_id "
            "JOIN document_structure_generations g ON g.id=plan.generation_id "
            "JOIN document_versions v ON v.id=j.document_version_id JOIN documents d ON "
            "d.id=v.document_id "
            "JOIN extractions x ON x.id=j.extraction_id LEFT JOIN app_users actor ON "
            "actor.id=c.actor_id "
            "WHERE p.household_space_id=%s AND p.rider_id=%s AND p.policy_contract_id=%s "
            "AND p.ledger_version=%s ORDER BY p.created_at DESC,p.candidate_version_id DESC "
            "LIMIT %s",
            (
                scope.household_space_id,
                rider_id,
                rider["policy_contract_id"],
                rider["version"],
                _LIMIT + 1,
            ),
        ).fetchall()
    )
    evaluated = []
    inputs: list[Any] = [rider, publications]
    if rider is not None and len(publications) <= _LIMIT:
        candidates = sorted(
            {
                p[k]
                for p in publications
                for k in ("candidate_version_id", "source_candidate_version_id")
            },
            key=str,
        )
        fields = (
            connection.execute(
                "/* amount-fields */ SELECT f.candidate_version_id,f.field_id,f.value "
                "FROM analysis_candidate_fields f JOIN analysis_candidate_versions c ON "
                "c.id=f.candidate_version_id "
                "WHERE c.household_space_id=%s AND f.candidate_version_id=ANY(%s) ORDER BY "
                "f.candidate_version_id,f.field_id LIMIT 2049",
                (scope.household_space_id, candidates),
            ).fetchall()
            if candidates
            else []
        )
        evidence = (
            connection.execute(
                "/* amount-evidence */ SELECT ce.candidate_version_id,ce.field_id,ce.evidence_id,"
                "ce.document_version_id,ce.physical_page,e.document_version_id AS "
                "evidence_document,"
                "e.extraction_id,e.household_space_id,e.content_sha256,e.physical_page AS "
                "evidence_page,"
                "e.review_state,e.x0,e.y0,e.x1,e.y1,v.content_sha256 AS document_hash,v.page_count,"
                "x.document_version_id AS extraction_document,x.status AS extraction_status,"
                "d.document_kind,d.deleted_at,page.width_points,page.height_points "
                "FROM analysis_candidate_evidence ce JOIN analysis_candidate_versions c ON "
                "c.id=ce.candidate_version_id "
                "JOIN evidence e ON e.id=ce.evidence_id JOIN document_versions v ON "
                "v.id=e.document_version_id "
                "JOIN extractions x ON x.id=e.extraction_id JOIN documents d ON d.id=v.document_id "
                "JOIN extraction_pages page ON page.extraction_id=x.id AND "
                "page.page_number=e.physical_page "
                "WHERE c.household_space_id=%s AND ce.candidate_version_id=ANY(%s) "
                "ORDER BY ce.candidate_version_id,ce.field_id,ce.evidence_id LIMIT 4097",
                (scope.household_space_id, candidates),
            ).fetchall()
            if candidates
            else []
        )
        if len(fields) <= 2048 and len(evidence) <= 4096:
            reader = StructureProjectionReader(connection, scope.household_space_id)
            for pub in publications:
                try:
                    pages = tuple(sorted({int(ref["page"]) for ref in pub["source_refs"]}))
                    projection = (
                        reader.read(pub["generation_id"], pages) if 1 <= len(pages) <= 64 else None
                    )
                    proof = _evaluate(
                        pub, rider, fields, evidence, projection, scope.household_space_id
                    )
                    # Input witness digests include the actual source replay and field state.
                    inputs.append(
                        [
                            pub,
                            sorted(
                                fields,
                                key=lambda f: (str(f["candidate_version_id"]), f["field_id"]),
                            ),
                            sorted(
                                evidence,
                                key=lambda e: (
                                    str(e["candidate_version_id"]),
                                    e["field_id"],
                                    str(e["evidence_id"]),
                                ),
                            ),
                            projection,
                        ]
                    )
                    evaluated.append((pub, proof))
                except KeyError, TypeError, ValueError, ArithmeticError:
                    inputs.append([str(pub.get("candidate_version_id")), "INVALID_SOURCE"])
    best = max(
        evaluated,
        key=lambda item: (
            sum(field.authority is not None for field in item[1].values()),
            item[0]["authority"] == "USER_CONFIRMED",
            str(item[0]["created_at"]),
            str(item[0]["candidate_version_id"]),
        ),
        default=None,
    )
    amount, currency = (
        (_Field(), _Field()) if best is None else (best[1]["sum_assured"], best[1]["currency"])
    )
    publications_used = (
        (best[0]["candidate_version_id"],)
        if best is not None and (amount.authority or currency.authority)
        else ()
    )
    reasons = tuple(
        code
        for field, code in (
            (amount, "AMOUNT_FIELD_UNVERIFIED"),
            (currency, "CURRENCY_FIELD_UNVERIFIED"),
        )
        if field.authority is None
    )
    return OperationalAmountSource(
        rider_id,
        None if rider is None else rider["version"],
        amount.value if isinstance(amount.value, Decimal) else None,
        currency.value if isinstance(currency.value, str) else None,
        "MATCH" if amount.authority else "UNKNOWN",
        "MATCH" if currency.authority else "UNKNOWN",
        amount.authority,
        currency.authority,
        amount.evidence,
        currency.evidence,
        publications_used,
        _digest(
            [AMOUNT_SOURCE_REVISION, str(scope.household_space_id), str(rider_id), inputs, reasons]
        ),
        reasons,
    )
