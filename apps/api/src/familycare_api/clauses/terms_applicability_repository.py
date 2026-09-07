"""Evaluate retained metadata and preserve every source-bound applicability result."""

import logging
import unicodedata
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from familycare_api.clauses.terms_applicability import (
    TermsApplicabilityAssessment,
    assess_terms_applicability,
)
from familycare_api.common.document_locks import lock_document_content
from familycare_api.insurance_documents.repository import _database_url

REVISION = "terms-applicability-v1"
logger = logging.getLogger(__name__)
_APPLICATION_LABELS = {
    "terms_reference": "적용약관코드",
    "edition_reference": "적용판본코드",
}


def _key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _scalar(proof: dict[str, Any], name: str) -> str | None:
    if name in proof["conflicting_fields"] or name in proof["unresolved_fields"]:
        return None
    values = {_key(f["value"]) for f in proof["facts"] if f["field"] == name}
    return next(iter(values)) if len(values) == 1 else None


def _explicit_application_fields(
    connection: psycopg.Connection[dict[str, Any]], source: dict[str, Any]
) -> frozenset[str]:
    """Read the original validated anchors; a generic citation is not applicability."""
    found: set[str] = set()
    pages: dict[int, list[tuple[str, dict[str, Any]]]] = {}
    for fact in source["proof_json"]["facts"]:
        name = fact["field"]
        if name in _APPLICATION_LABELS:
            for span in fact["spans"]:
                pages.setdefault(span["page_number"], []).append((name, span))
    for number, spans in sorted(pages.items()):
        if all(name in found for name, _ in spans):
            continue
        row = connection.execute(
            "SELECT document_structure_projection(%s,%s,%s) AS source",
            (source["generation_id"], source["household_space_id"], [number]),
        ).fetchone()
        if row is None or row["source"] is None:
            continue
        nodes = {node["node_id"]: node for node in row["source"]["nodes"]}
        for name, span in spans:
            node = nodes.get(span["node_id"])
            if node is None or node["text"][span["start"] : span["end"]] != span["text"]:
                continue
            prefix = node["text"][span["anchor_start"] : span["start"]].strip(" :：|\t\n")
            if "".join(_key(prefix).split()) == _APPLICATION_LABELS[name]:
                found.add(name)
    return frozenset(found)


def _unknown(result: TermsApplicabilityAssessment, reason: str) -> TermsApplicabilityAssessment:
    return replace(
        result, status="UNKNOWN", matched_by=None, reason_codes=(*result.reason_codes, reason)
    )


class TermsApplicabilityProjector:
    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def refresh_pending(
        self, *, limit: int = 5, stop_requested: Callable[[], bool] | None = None
    ) -> int:
        if type(limit) is not int or not 1 <= limit <= 25:
            raise ValueError("invalid terms applicability limit")
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            candidates = connection.execute(
                "SELECT DISTINCT "
                "p.id,p.household_space_id,party.family_member_id,checked.checked_at "
                "FROM policy_contracts p JOIN policy_parties party ON "
                "party.policy_contract_id=p.id "
                "AND party.household_space_id=p.household_space_id "
                "JOIN family_members m ON m.id=party.family_member_id "
                "AND m.household_space_id=p.household_space_id "
                "LEFT JOIN policy_terms_refresh_checks checked ON checked.policy_contract_id=p.id "
                "AND checked.family_member_id=party.family_member_id "
                "WHERE p.deleted_at IS NULL AND party.deleted_at IS NULL AND m.deleted_at IS NULL "
                "AND (checked.retry_after IS NULL OR checked.retry_after<=clock_timestamp()) "
                "AND party.role IN ('primary_insured','additional_insured') "
                "ORDER BY checked.checked_at NULLS FIRST,p.id,party.family_member_id LIMIT %s",
                (limit,),
            ).fetchall()
        completed = 0
        for policy in candidates:
            if stop_requested and stop_requested():
                break
            try:
                with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                    connection.execute("SET LOCAL statement_timeout='30s'")
                    if self._refresh(connection, policy):
                        completed += 1
            except Exception:
                # Source failures are isolated after the assessment transaction rolls back.
                # Never log exception text, source metadata or identifiers.
                logger.warning("Terms applicability refresh deferred")
                with psycopg.connect(self.database_url) as connection:
                    connection.execute("SET LOCAL statement_timeout='5s'")
                    connection.execute(
                        "INSERT INTO policy_terms_refresh_checks("
                        "policy_contract_id,family_member_id,retry_after) "
                        "VALUES(%s,%s,clock_timestamp()+interval '30 seconds') "
                        "ON CONFLICT(policy_contract_id,family_member_id) DO UPDATE SET "
                        "checked_at=clock_timestamp(),input_digest=NULL,"
                        "retry_after=clock_timestamp()+interval '30 seconds'",
                        (policy["id"], policy["family_member_id"]),
                    )
        return completed

    @staticmethod
    def _context(
        connection: psycopg.Connection[dict[str, Any]], policy: dict[str, Any]
    ) -> dict[str, Any]:
        row = connection.execute(
            "SELECT context,encode(sha256(convert_to(context::text,'UTF8')),'hex') AS digest,"
            "octet_length(context::text) AS bytes "
            "FROM (SELECT policy_terms_input_context(%s,%s,%s) AS context) source",
            (policy["id"], policy["family_member_id"], policy["household_space_id"]),
        ).fetchone()
        assert row is not None
        return row

    @staticmethod
    def _touch(
        connection: psycopg.Connection[dict[str, Any]], policy: dict[str, Any], digest: str | None
    ) -> None:
        connection.execute(
            "INSERT INTO "
            "policy_terms_refresh_checks(policy_contract_id,family_member_id,input_digest) "
            "VALUES(%s,%s,%s) ON CONFLICT(policy_contract_id,family_member_id) DO UPDATE SET "
            "checked_at=clock_timestamp(),input_digest=excluded.input_digest,retry_after=NULL",
            (policy["id"], policy["family_member_id"], digest),
        )

    @classmethod
    def _refresh(
        cls, connection: psycopg.Connection[dict[str, Any]], policy: dict[str, Any]
    ) -> bool:
        # This lock only coordinates refreshers; all source locks are acquired afterwards
        # in the same content-first order as publication and user attachment.
        claimed = connection.execute(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS claimed",
            (f"terms-applicability:{policy['id']}:{policy['family_member_id']}",),
        ).fetchone()
        if claimed is None or not claimed["claimed"]:
            return False
        observed = cls._context(connection, policy)
        context = observed["context"]
        if context is None or observed["bytes"] > 1048576 or len(context["editions"]) > 500:
            cls._touch(connection, policy, None)
            return False
        prior = connection.execute(
            "SELECT input_digest FROM policy_terms_refresh_checks "
            "WHERE policy_contract_id=%s AND family_member_id=%s",
            (policy["id"], policy["family_member_id"]),
        ).fetchone()
        if prior is not None and prior["input_digest"] == observed["digest"]:
            cls._touch(connection, policy, observed["digest"])
            return False
        if len(context["policy_components"]) != 1:
            cls._touch(connection, policy, observed["digest"])
            return False
        edition_ids = [edition[0] for edition in context["editions"]]
        hashes = connection.execute(
            "SELECT content_sha256 FROM terms_editions WHERE id=ANY(%s::uuid[])", (edition_ids,)
        ).fetchall()
        for digest in sorted(
            {context["policy"]["content_sha256"], *(r["content_sha256"] for r in hashes)}
        ):
            lock_document_content(connection, policy["household_space_id"], digest)
        connection.execute(
            "SELECT id FROM policy_contracts WHERE id=%s FOR UPDATE", (policy["id"],)
        )
        component_id = context["policy_components"][0][0]
        connection.execute(
            "SELECT c.id FROM insurance_document_components c WHERE c.id=%s FOR SHARE",
            (component_id,),
        )
        connection.execute(
            "SELECT e.id FROM terms_editions e JOIN insurance_document_components c "
            "ON c.id=e.source_component_id WHERE e.id=ANY(%s::uuid[]) FOR SHARE OF e,c",
            (edition_ids,),
        )
        if cls._context(connection, policy)["context"] != context:
            return False
        source = connection.execute(
            "SELECT * FROM terms_applicability_component_sources WHERE id=%s", (component_id,)
        ).fetchone()
        if source is None:
            return False
        references = _explicit_application_fields(connection, source)
        assessments: list[tuple[dict[str, Any], TermsApplicabilityAssessment]] = []
        for edition_id in edition_ids:
            terms = connection.execute(
                "SELECT e.id AS edition_id,c.id AS component_id,c.metadata_publication_id,"
                "c.proof_json,c.content_sha256,c.page_start,c.page_end "
                "FROM terms_editions e JOIN terms_applicability_component_sources c "
                "ON c.id=e.source_component_id WHERE e.id=%s",
                (edition_id,),
            ).fetchone()
            if terms is None:
                return False
            result = assess_terms_applicability(
                source["proof_json"], terms["proof_json"], explicit_application_fields=references
            )
            corrected = any(
                (value := _scalar(source["proof_json"], field)) is not None
                and value != _key(context["policy"][column])
                for field, column in (
                    ("insurer", "insurer_display"),
                    ("product_name", "product_display"),
                )
            )
            printed_contract_date = _scalar(source["proof_json"], "contract_date")
            corrected = corrected or (
                context["policy"]["contract_date_origin"] == "EXPLICIT_LEDGER"
                and printed_contract_date is not None
                and printed_contract_date != context["policy"]["contract_date"]
            )
            if corrected and result.status != "NO_MATCH":
                result = _unknown(result, "POLICY_SOURCE_CORRECTED")
            ledger_date = context["policy"]["contract_date"]
            if (
                result.status != "NO_MATCH"
                and context["policy"]["contract_date_origin"] == "EXPLICIT_LEDGER"
                and printed_contract_date is None
                and (
                    (
                        (start := _scalar(terms["proof_json"], "applicability_start")) is not None
                        and ledger_date < start
                    )
                    or (
                        (end := _scalar(terms["proof_json"], "applicability_end")) is not None
                        and ledger_date > end
                    )
                )
            ):
                result = _unknown(result, "LEDGER_CONTRACT_DATE_CONFLICT")
            user_selected = any(
                decision[2] is None
                and decision[5] == "USER_CONFIRMED"
                and decision[6] is None
                and decision[7] == str(terms["component_id"])
                for decision in context["user_decisions"]
            )
            if user_selected:
                terms["selection_state"] = "USER_SELECTED"
            elif context["user_decisions"]:
                terms["selection_state"] = "USER_OWNED"
                if result.status != "NO_MATCH":
                    result = _unknown(result, "USER_DOCUMENT_DECISION_EXISTS")
            else:
                terms["selection_state"] = "AUTOMATIC" if result.status == "MATCH" else "UNRESOLVED"
            terms["signature"] = (
                terms["content_sha256"],
                terms["page_start"],
                terms["page_end"],
                *(
                    _scalar(terms["proof_json"], name)
                    for name in (
                        "terms_code",
                        "edition_code",
                        "product_code",
                        "applicability_start",
                        "applicability_end",
                    )
                ),
            )
            del terms["proof_json"]
            assessments.append((terms, result))
        matches = [
            terms
            for terms, result in assessments
            if result.status == "MATCH" and terms["selection_state"] == "AUTOMATIC"
        ]
        if len(matches) > 1:
            signatures = {term["signature"] for term in matches}
            if len(signatures) > 1:
                assessments = [
                    (
                        term,
                        _unknown(result, "AMBIGUOUS_MATCHING_EDITIONS")
                        if result.status == "MATCH" and term["selection_state"] == "AUTOMATIC"
                        else result,
                    )
                    for term, result in assessments
                ]
        for terms, result in assessments:
            if result.status != "MATCH" and terms["selection_state"] == "AUTOMATIC":
                terms["selection_state"] = "UNRESOLVED"
            connection.execute(
                "INSERT INTO policy_terms_applicability(household_space_id,family_member_id,"
                "policy_contract_id,policy_component_id,terms_edition_id,policy_publication_id,"
                "terms_publication_id,revision,status,selection_state,matched_by,reason_codes,evidence_fields,"
                "input_context,input_digest) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(policy_contract_id,family_member_id,"
                "terms_edition_id,revision,input_digest) "
                "DO NOTHING",
                (
                    policy["household_space_id"],
                    policy["family_member_id"],
                    policy["id"],
                    component_id,
                    terms["edition_id"],
                    source["metadata_publication_id"],
                    terms["metadata_publication_id"],
                    REVISION,
                    result.status,
                    terms["selection_state"],
                    result.matched_by,
                    Jsonb(result.reason_codes),
                    Jsonb(result.evidence_fields),
                    Jsonb(context),
                    observed["digest"],
                ),
            )
        cls._touch(connection, policy, observed["digest"])
        return True
