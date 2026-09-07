"""API-owned, source-bound publication of raw enrollment facts.

The retained analysis is the durable inbox. Publication never asserts current
validity or a payable benefit; those remain separate decision use cases.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any, cast
from uuid import UUID, uuid5

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from familycare_api.policies.contract_source_locator import contract_source_locator
from familycare_api.policies.enrollment_locator import physical_enrollment_locator


def _key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _policy_identity(
    connection: psycopg.Connection[dict[str, Any]], source: dict[str, Any]
) -> UUID | None:
    """Reuse one proven contract across imports while retaining every source alias."""
    association = source["association_json"]
    locator = contract_source_locator(source["structure_json"], association)
    previous = connection.execute(
        "SELECT DISTINCT p.policy_contract_id,s.association_json,g.id,g.structure_json "
        "FROM range_enrollment_publications p JOIN policy_range_candidate_sources s ON "
        "s.candidate_version_id=p.source_candidate_version_id "
        "JOIN document_policy_range_plans plan ON plan.job_id=s.job_id "
        "JOIN document_structure_generations g ON g.id=plan.generation_id "
        "JOIN document_versions v ON v.id=g.document_version_id "
        "WHERE p.household_space_id=%s AND v.content_sha256=%s",
        (source["household_space_id"], source["structure_json"]["lineage"]["content_sha256"]),
    ).fetchall()
    matches: set[UUID] = set()
    for row in previous:
        old_association = row["association_json"]
        old = contract_source_locator(row["structure_json"], old_association)
        if old_association["contract_scope_id"] == association["contract_scope_id"]:
            # This also routes a changed member to the existing party guard.
            matches.add(row["policy_contract_id"])
        elif row["structure_json"]["lineage"]["document_version_id"] == str(
            source["document_version_id"]
        ):
            # Distinct local scopes already distinguish contracts in one document.
            continue
        elif locator is None or old is None:
            return None
        elif old["contract_number_sha256"] == locator["contract_number_sha256"]:
            if old != locator:
                return None
            matches.add(row["policy_contract_id"])
    if len(matches) > 1:
        return None
    return next(iter(matches)) if matches else UUID(association["contract_scope_id"])


def _physical_rider_identity(
    connection: psycopg.Connection[dict[str, Any]],
    source: dict[str, Any],
    policy_id: UUID,
    name: str,
    refs: list[dict[str, Any]],
) -> tuple[bool, UUID | None]:
    locator = physical_enrollment_locator(source["structure_json"], name, refs)
    previous = connection.execute(
        "SELECT DISTINCT p.rider_id,s.source_refs,plan.generation_id,f.value AS name, "
        "ARRAY(SELECT e.evidence_id::text FROM analysis_candidate_evidence e "
        "WHERE e.candidate_version_id=s.candidate_version_id AND e.field_id='rider_name') name_ids "
        "FROM range_enrollment_publications p "
        "JOIN policy_range_candidate_sources s ON "
        "s.candidate_version_id=p.source_candidate_version_id "
        "JOIN document_policy_range_plans plan ON plan.job_id=s.job_id "
        "JOIN document_structure_generations g ON g.id=plan.generation_id "
        "JOIN document_versions v ON v.id=g.document_version_id "
        "JOIN analysis_candidate_fields f ON f.candidate_version_id=s.candidate_version_id "
        "AND f.field_id='rider_name' "
        "WHERE p.household_space_id=%s AND p.policy_contract_id=%s AND p.rider_id IS NOT NULL "
        "AND v.content_sha256=%s",
        (
            source["household_space_id"],
            policy_id,
            source["structure_json"]["lineage"]["content_sha256"],
        ),
    ).fetchall()
    structures = {source["generation_id"]: source["structure_json"]}
    matches: set[UUID] = set()
    for item in previous:
        generation_id = item["generation_id"]
        if generation_id not in structures:
            row = connection.execute(
                "SELECT structure_json FROM document_structure_generations WHERE id=%s "
                "AND household_space_id=%s",
                (generation_id, source["household_space_id"]),
            ).fetchone()
            if row is None:
                return True, None
            structures[generation_id] = row["structure_json"]
        old = physical_enrollment_locator(
            structures[generation_id],
            item["name"],
            [ref for ref in item["source_refs"] if ref["evidence_id"] in item["name_ids"]],
        )
        if locator is not None and old == locator:
            matches.add(item["rider_id"])
        elif (
            (locator is None or old is None)
            and generation_id != source["generation_id"]
            and _key(item["name"]) == _key(name)
        ):
            # An uncertain repeat is retained for review, never made additive.
            return True, None
    if len(matches) > 1:
        return True, None
    if matches:
        return True, next(iter(matches))
    if locator is not None:
        return True, uuid5(policy_id, json.dumps(locator, sort_keys=True, separators=(",", ":")))
    return False, None


def _rider_identity(
    connection: psycopg.Connection[dict[str, Any]],
    source: dict[str, Any],
    policy_id: UUID,
    version: dict[str, Any],
) -> UUID | None:
    """Address the original enrollment mention, independent of later corrections."""
    bound = connection.execute(
        "SELECT rider_id FROM range_enrollment_publications WHERE source_candidate_version_id=%s "
        "AND household_space_id=%s AND policy_contract_id=%s AND rider_id IS NOT NULL "
        "ORDER BY created_at LIMIT 1",
        (source["candidate_version_id"], source["household_space_id"], policy_id),
    ).fetchone()
    if bound is not None:
        return cast(UUID, bound["rider_id"])
    candidate_ids = [source["candidate_version_id"]]
    if version["status"] == "USER_CONFIRMED" and version["id"] not in candidate_ids:
        candidate_ids.append(version["id"])
    for candidate_id in candidate_ids:
        root_name = connection.execute(
            "SELECT value FROM analysis_candidate_fields WHERE candidate_version_id=%s "
            "AND field_id='rider_name'",
            (candidate_id,),
        ).fetchone()
        if root_name is None or not isinstance(root_name["value"], str):
            continue
        root_evidence = connection.execute(
            "SELECT evidence_id FROM analysis_candidate_evidence WHERE candidate_version_id=%s "
            "AND field_id='rider_name'",
            (candidate_id,),
        ).fetchall()
        keys = {str(row["evidence_id"]) for row in root_evidence}
        addressed, physical_id = _physical_rider_identity(
            connection,
            source,
            policy_id,
            root_name["value"],
            [ref for ref in source["source_refs"] if ref["evidence_id"] in keys],
        )
        if addressed:
            return physical_id
        nodes = {node["node_id"]: node for node in source["structure_json"]["nodes"]}
        table_rows = {
            ref["node_id"]
            for ref in source["source_refs"]
            if ref["evidence_id"] in keys
            and ref["primary"]
            and ref["source_role"] == "policy"
            and nodes.get(ref["node_id"], {}).get("kind") == "TABLE_ROW"
            and nodes[ref["node_id"]].get("row_role") == "data"
        }
        if len(table_rows) == 1:
            return uuid5(policy_id, "enrollment-table-row-v1:" + next(iter(table_rows)))
        if table_rows:
            continue
        pattern = (
            r"(?<!\w)"
            + r"\s+".join(re.escape(word) for word in root_name["value"].split())
            + r"(?!\w)"
        )
        mentions = set()
        for ref in source["source_refs"]:
            if (
                ref["evidence_id"] not in keys
                or not ref["primary"]
                or ref["source_role"] != "policy"
            ):
                continue
            node = nodes.get(ref["node_id"])
            if node is None:
                return None
            text = node["text"][ref["start"] : ref["end"]]
            for match in re.finditer(pattern, text, re.IGNORECASE):
                mentions.add(
                    (ref["node_id"], ref["start"] + match.start(), ref["start"] + match.end())
                )
        if len(mentions) != 1:
            continue
        return uuid5(policy_id, "enrollment-mention-v1:" + json.dumps(sorted(mentions)))
    return None


def _insured_evidence(
    connection: psycopg.Connection[dict[str, Any]], source: dict[str, Any]
) -> UUID | None:
    association = source["association_json"]
    anchors = [ref for ref in association.get("anchor_refs", ()) if ref.get("kind") == "insured"]
    if len(anchors) != 1:
        return None
    anchor = anchors[0]
    structure = source["structure_json"]
    nodes = [node for node in structure["nodes"] if node["node_id"] == anchor["node_id"]]
    if len(nodes) != 1:
        return None
    node = nodes[0]
    if not (
        node["page_number"] == anchor["page"]
        and 0 <= anchor["start"] < anchor["end"] <= len(node["text"])
    ):
        return None
    evidence_id = uuid5(source["generation_id"], "insured:" + json.dumps(anchor, sort_keys=True))
    connection.execute(
        "INSERT INTO evidence(id,household_space_id,document_version_id,extraction_id,"
        "content_sha256,physical_page,x0,y0,x1,y1,review_state) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'AI_VERIFIED') ON CONFLICT (id) DO NOTHING",
        (
            evidence_id,
            source["household_space_id"],
            source["document_version_id"],
            source["extraction_id"],
            structure["lineage"]["content_sha256"],
            anchor["page"],
            *(node["bbox"] or (None, None, None, None)),
        ),
    )
    return evidence_id


def project_range_candidate(
    connection: psycopg.Connection[dict[str, Any]],
    version: dict[str, Any],
    context: dict[str, Any],
) -> bool:
    """Use the caller's transaction and household/current-candidate locks."""
    from familycare_api.policies.candidate_models import validate_candidate_field_value
    from familycare_api.policies.candidate_repository import _as_date, _validate_date_ranges

    household = version["household_space_id"]
    active_household = connection.execute(
        "SELECT id FROM household_spaces WHERE id=%s AND deleted_at IS NULL", (household,)
    ).fetchone()
    if active_household is None:
        return False
    existing = connection.execute(
        "SELECT candidate_version_id FROM range_enrollment_publications "
        "WHERE candidate_version_id=%s AND household_space_id=%s",
        (version["id"], household),
    ).fetchone()
    if existing is not None:
        return True
    if not version["is_current"] or version["deleted_at"] is not None:
        return False
    source = connection.execute(
        "SELECT s.*, j.household_space_id,j.document_version_id,j.extraction_id, "
        "p.associations_json,p.generation_id,g.structure_json "
        "FROM policy_range_candidate_sources s "
        "JOIN analysis_candidate_versions root ON root.id=s.candidate_version_id "
        "JOIN policy_structuring_jobs j ON j.id=s.job_id "
        "JOIN document_policy_range_plans p ON p.job_id=j.id "
        "JOIN document_structure_generations g ON g.id=p.generation_id "
        "JOIN document_versions dv ON dv.id=j.document_version_id "
        "JOIN documents d ON d.id=dv.document_id AND d.deleted_at IS NULL "
        "WHERE root.review_item_id=%s AND root.household_space_id=%s AND j.id=%s FOR SHARE OF d",
        (version["review_item_id"], household, context["id"]),
    ).fetchone()
    if source is None:
        return False
    association = source["association_json"]
    if association.get("state") != "RESOLVED" or association.get("family_member_id") != str(
        context["family_member_id"]
    ):
        return False
    members = connection.execute(
        "SELECT id,display_name,internal_alias,version FROM family_members "
        "WHERE household_space_id=%s AND deleted_at IS NULL ORDER BY id FOR SHARE",
        (household,),
    ).fetchall()
    identities = [
        (str(m["id"]), m["display_name"], m["internal_alias"], m["version"]) for m in members
    ]
    fingerprint = hashlib.sha256(json.dumps(identities, ensure_ascii=True).encode()).hexdigest()
    if fingerprint != source["associations_json"].get("member_fingerprint"):
        return False
    fields = connection.execute(
        "SELECT field_id,value FROM analysis_candidate_fields WHERE candidate_version_id=%s",
        (version["id"],),
    ).fetchall()
    values = {row["field_id"]: row["value"] for row in fields}
    try:
        for name, value in values.items():
            validate_candidate_field_value(cast(Any, name), value)
        _validate_date_ranges(values)
        amount = values.get("sum_assured")
        if amount is not None:
            money = Decimal(str(amount))
            if (
                not money.is_finite()
                or money >= Decimal("1e16")
                or money != money.quantize(Decimal(".01"))
            ):
                return False
    except ValueError, InvalidOperation:
        return False
    refs = {ref["evidence_id"]: ref for ref in source["source_refs"]}
    evidence = connection.execute(
        "SELECT ce.field_id,ce.evidence_id FROM analysis_candidate_evidence ce "
        "JOIN evidence e ON e.id=ce.evidence_id AND e.document_version_id=ce.document_version_id "
        "JOIN document_versions v ON v.id=e.document_version_id "
        "AND v.content_sha256=e.content_sha256 "
        "WHERE ce.candidate_version_id=%s AND e.household_space_id=%s "
        "AND e.document_version_id=%s AND e.extraction_id=%s ORDER BY ce.field_id,e.id",
        (version["id"], household, context["document_version_id"], context["extraction_id"]),
    ).fetchall()
    if not values or {row["field_id"] for row in evidence} != values.keys():
        return False
    name_field = "product_name" if version["candidate_kind"] == "policy_contract" else "rider_name"
    primary = [
        row
        for row in evidence
        if row["field_id"] == name_field
        and refs.get(str(row["evidence_id"]), {}).get("primary")
        and refs[str(row["evidence_id"])]["source_role"] == "policy"
    ]
    if not primary:
        return False
    policy_id = _policy_identity(connection, source)
    if policy_id is None:
        return False
    enrolled_members = connection.execute(
        "SELECT p.id, ARRAY(SELECT party.family_member_id FROM policy_parties party "
        "WHERE party.policy_contract_id=p.id AND party.household_space_id=p.household_space_id "
        "AND party.role='primary_insured' AND party.deleted_at IS NULL) member_ids "
        "FROM policy_contracts p "
        "WHERE p.id=%s AND p.household_space_id=%s FOR SHARE OF p",
        (policy_id, household),
    ).fetchone()
    if enrolled_members is not None and set(enrolled_members["member_ids"]) != {
        context["family_member_id"]
    }:
        return False
    rider_id = None
    if version["candidate_kind"] == "rider":
        rider_id = _rider_identity(connection, source, policy_id, version)
        if rider_id is None:
            return False
    elif version["candidate_kind"] != "policy_contract":
        return False
    target_table = "riders" if rider_id else "policy_contracts"
    target_id = rider_id or policy_id
    target = connection.execute(
        f"SELECT * FROM {target_table} WHERE id=%s AND household_space_id=%s FOR UPDATE",
        (target_id, household),
    ).fetchone()
    previous = connection.execute(
        "SELECT * FROM range_enrollment_publications WHERE household_space_id=%s "
        "AND policy_contract_id=%s AND rider_id IS NOT DISTINCT FROM %s "
        "ORDER BY created_at DESC,candidate_version_id DESC LIMIT 1",
        (household, policy_id, rider_id),
    ).fetchone()
    authority = "USER_CONFIRMED" if version["status"] == "USER_CONFIRMED" else "PROGRAM_VERIFIED"
    update = False
    if target is not None:
        if (
            target["deleted_at"] is not None
            or previous is None
            or target["version"] != previous["ledger_version"]
        ):
            return False
        if values != previous["field_values"]:
            if authority != "USER_CONFIRMED":
                return False
            lineage_publication = connection.execute(
                "SELECT candidate_version_id FROM range_enrollment_publications "
                "WHERE source_candidate_version_id=%s AND household_space_id=%s "
                "AND policy_contract_id=%s AND rider_id IS NOT DISTINCT FROM %s LIMIT 1",
                (source["candidate_version_id"], household, policy_id, rider_id),
            ).fetchone()
            last_user = connection.execute(
                "SELECT source_candidate_version_id FROM range_enrollment_publications "
                "WHERE household_space_id=%s AND policy_contract_id=%s "
                "AND rider_id IS NOT DISTINCT FROM %s AND authority='USER_CONFIRMED' "
                "ORDER BY created_at DESC LIMIT 1",
                (household, policy_id, rider_id),
            ).fetchone()
            if lineage_publication is None or (
                last_user is not None
                and last_user["source_candidate_version_id"] != source["candidate_version_id"]
            ):
                return False
            update = True
    elif previous is not None:
        return False
    if rider_id is not None:
        policy = connection.execute(
            "SELECT id FROM policy_contracts WHERE id=%s AND household_space_id=%s "
            "AND deleted_at IS NULL FOR SHARE",
            (policy_id, household),
        ).fetchone()
        if policy is None:
            return False
    common = {"source_evidence_id": primary[0]["evidence_id"]}
    if rider_id is None:
        if (
            target is not None
            and target["source_document_version_id"] != context["document_version_id"]
        ):
            # Keep the policy and its party on their original document. The
            # appended publication retains the corrected import's field proof.
            common["source_evidence_id"] = target["source_evidence_id"]
        insurer, product = values.get("insurer"), values.get("product_name")
        if not isinstance(insurer, str) or not 1 <= len(insurer) <= 160:
            return False
        if not isinstance(product, str) or not 1 <= len(product) <= 200:
            return False
        columns = dict(
            common,
            insurer_display=insurer,
            insurer_key=_key(insurer)[:160],
            product_display=product,
            product_key=_key(product)[:200],
            contract_date=_as_date(values.get("contract_start")),
            coverage_start_date=_as_date(values.get("contract_start")),
            coverage_end_date=_as_date(values.get("contract_end")),
        )
    else:
        name, benefit = values.get("rider_name"), values.get("benefit_type")
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= 200
            or benefit not in {"fixed", "indemnity", "unknown"}
        ):
            return False
        amount = values.get("sum_assured")
        columns = dict(
            common,
            display_name=name,
            normalized_key=_key(name)[:240],
            benefit_type=benefit,
            insured_amount=Decimal(str(amount)) if amount is not None else None,
            currency=values.get("currency"),
            renewable=values.get("renewable"),
            coverage_start_date=_as_date(values.get("coverage_start")),
            coverage_end_date=_as_date(values.get("coverage_end")),
        )
    insured_evidence_id = _insured_evidence(connection, source)
    if insured_evidence_id is None:
        return False
    connection.execute(
        "UPDATE evidence SET review_state='AI_VERIFIED' "
        "WHERE id=%s AND household_space_id=%s AND document_version_id=%s AND extraction_id=%s "
        "AND review_state='NEEDS_REVIEW'",
        (
            primary[0]["evidence_id"],
            household,
            context["document_version_id"],
            context["extraction_id"],
        ),
    )
    if target is None:
        columns.update(id=target_id, household_space_id=household, status="unknown")
        columns["policy_contract_id" if rider_id else "source_document_version_id"] = (
            policy_id if rider_id else context["document_version_id"]
        )
        target = connection.execute(
            f"INSERT INTO {target_table} ({','.join(columns)}) "
            f"VALUES ({','.join(['%s'] * len(columns))}) RETURNING *",
            tuple(columns.values()),
        ).fetchone()
        if rider_id is None:
            connection.execute(
                "INSERT INTO policy_parties(household_space_id,policy_contract_id,family_member_id,"
                "role,evidence_id) VALUES (%s,%s,%s,'primary_insured',%s)",
                (household, policy_id, context["family_member_id"], insured_evidence_id),
            )
    elif update:
        target = connection.execute(
            f"UPDATE {target_table} SET "
            + ",".join(f"{col}=%s" for col in columns)
            + ",version=version+1,updated_at=clock_timestamp() "
            "WHERE id=%s AND household_space_id=%s "
            "RETURNING *",
            (*columns.values(), target_id, household),
        ).fetchone()
    assert target is not None
    connection.execute(
        "INSERT INTO range_enrollment_publications(candidate_version_id,"
        "source_candidate_version_id,"
        "household_space_id,policy_contract_id,rider_id,ledger_version,field_values,authority,"
        "insured_evidence_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            version["id"],
            source["candidate_version_id"],
            household,
            policy_id,
            rider_id,
            target["version"],
            Jsonb(values),
            authority,
            insured_evidence_id,
        ),
    )
    connection.execute(
        "UPDATE analysis_candidate_versions SET aggregate_id=%s,published_at=clock_timestamp() "
        "WHERE id=%s AND household_space_id=%s",
        (policy_id, version["id"], household),
    )
    return True


class RangeEnrollmentProjector:
    """Drain bounded retained facts; a failed transaction leaves its input retryable."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    def project_pending(
        self, *, limit: int = 100, stop_requested: Callable[[], bool] = lambda: False
    ) -> int:
        from familycare_api.policies.candidate_errors import InvalidCandidateCorrection
        from familycare_api.policies.candidate_repository import CandidateRepository

        repository = CandidateRepository(self.database_url)
        with psycopg.connect(
            self.database_url,
            row_factory=dict_row,
            connect_timeout=5,
            options="-c statement_timeout=5000",
        ) as connection:
            pending = connection.execute(
                "SELECT c.id,c.household_space_id FROM analysis_candidate_versions c "
                "JOIN analysis_candidate_versions root ON root.review_item_id=c.review_item_id "
                "JOIN policy_range_candidate_sources s ON s.candidate_version_id=root.id "
                "LEFT JOIN range_enrollment_attempts a ON a.candidate_version_id=c.id "
                "WHERE c.is_current AND c.deleted_at IS NULL AND c.published_at IS NULL "
                "AND c.status IN ('AI_VERIFIED','USER_CONFIRMED') "
                "AND s.association_json->>'state'='RESOLVED' "
                "ORDER BY a.attempted_at NULLS FIRST,c.candidate_kind,c.created_at,c.id LIMIT %s",
                (max(1, min(limit, 1000)),),
            ).fetchall()
        count = 0
        for item in pending:
            if stop_requested():
                break
            with psycopg.connect(
                self.database_url, row_factory=dict_row, connect_timeout=5
            ) as connection:
                connection.execute("SET LOCAL statement_timeout='5s'")
                connection.execute(
                    "SELECT id FROM household_spaces WHERE id=%s FOR UPDATE",
                    (item["household_space_id"],),
                )
                current = connection.execute(
                    "SELECT id FROM analysis_candidate_versions "
                    "WHERE id=%s AND household_space_id=%s "
                    "AND is_current AND published_at IS NULL AND deleted_at IS NULL FOR UPDATE",
                    (item["id"], item["household_space_id"]),
                ).fetchone()
                if current is None:
                    continue
                try:
                    with connection.transaction():
                        applied = repository._publish_projection(
                            connection, item["household_space_id"], item["id"]
                        )
                except (
                    InvalidCandidateCorrection,
                    psycopg.DataError,
                    psycopg.IntegrityError,
                    ValueError,
                    InvalidOperation,
                ):
                    applied = False
                connection.execute(
                    "INSERT INTO range_enrollment_attempts(candidate_version_id,outcome) "
                    "VALUES (%s,%s) ON CONFLICT (candidate_version_id) DO UPDATE "
                    "SET attempted_at=clock_timestamp(),outcome=EXCLUDED.outcome",
                    (item["id"], "APPLIED" if applied else "DEFERRED"),
                )
                count += int(applied)
        return count
