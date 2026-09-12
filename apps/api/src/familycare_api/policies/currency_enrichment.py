"""Recover only an absent Rider currency from the same verified amount source."""

from decimal import Decimal
from typing import Any

import psycopg

from familycare_api.guidance.amount_source import _money_witness
from familycare_api.policies.contract_source_locator import contract_source_locator

_REASONS = {"CURRENCY_DERIVED_FROM_AMOUNT", "CURRENCY_EVIDENCE_REALIGNED"}


def currency_enrichment_proven(
    connection: psycopg.Connection[dict[str, Any]],
    version: dict[str, Any],
    source: dict[str, Any],
    values: dict[str, Any],
    evidence: list[dict[str, Any]],
    target: dict[str, Any],
    previous: dict[str, Any],
) -> bool:
    """The caller holds current-candidate/ledger locks and excludes user publications."""
    try:
        if (
            version["candidate_kind"] != "rider"
            or version["status"] != "AI_VERIFIED"
            or version["id"] != source["candidate_version_id"]
            or version["generator_version"] != "policy-draft-normalization-v4"
            or source["pipeline_version"] != "retained-policy-association-v9"
            or target["currency"] is not None
            or target["version"] != previous["ledger_version"]
            or previous["authority"] != "PROGRAM_VERIFIED"
            or "currency" in previous["field_values"]
            or {key: value for key, value in values.items() if key != "currency"}
            != previous["field_values"]
            or values.get("currency") not in {"KRW", "USD", "EUR", "JPY"}
            or values.get("rider_name") != target["display_name"]
            or isinstance(values.get("sum_assured"), bool)
            or Decimal(str(values["sum_assured"])) != target["insured_amount"]
            or contract_source_locator(source["structure_json"], source["association_json"]) is None
        ):
            return False
        old = connection.execute(
            "SELECT s.source_refs,s.association_json,p.generation_id,j.document_version_id,"
            "j.extraction_id,ARRAY(SELECT e.evidence_id::text FROM analysis_candidate_evidence e "
            "WHERE e.candidate_version_id=c.id AND e.field_id='rider_name' ORDER BY e.evidence_id) "
            "AS name_evidence FROM analysis_candidate_versions c "
            "JOIN policy_range_candidate_sources s ON s.candidate_version_id=c.id "
            "JOIN document_policy_range_plans p ON p.job_id=s.job_id "
            "JOIN policy_structuring_jobs j ON j.id=s.job_id "
            "WHERE c.id=%s AND c.household_space_id=%s AND c.is_current "
            "AND c.deleted_at IS NULL AND c.status='AI_VERIFIED' AND c.actor_id IS NULL "
            "AND policy_structuring_source_current(j.id) FOR SHARE OF c,s,p,j",
            (previous["candidate_version_id"], version["household_space_id"]),
        ).fetchone()
        if old is None or any(
            old[key] != source[key]
            for key in ("association_json", "generation_id", "document_version_id", "extraction_id")
        ):
            return False
        citations = {
            field: {str(item["evidence_id"]) for item in evidence if item["field_id"] == field}
            for field in values
        }
        if citations.get("rider_name") != set(old["name_evidence"]):
            return False
        old_refs = {r["evidence_id"]: r for r in old["source_refs"]}
        refs = {r["evidence_id"]: r for r in source["source_refs"]}
        if any(refs[key] != old_refs[key] for key in citations["rider_name"]):
            return False
        row = connection.execute(
            "SELECT r.result_json,receipt.normalized_batch_json,receipt.adjustments_json "
            "FROM document_policy_ranges r JOIN policy_range_replay_sources receipt "
            "ON receipt.job_id=r.job_id AND receipt.envelope_id=r.envelope_id "
            "WHERE r.job_id=%s AND r.generation_id=%s AND r.envelope_id=%s "
            "AND r.state IN ('REVIEW','COMPLETE') "
            "AND receipt.normalization_revision='policy-draft-normalization-v4' "
            "FOR SHARE OF r,receipt",
            (source["job_id"], source["generation_id"], source["envelope_id"]),
        ).fetchone()
        if (
            row is None
            or row["result_json"].get("program_validation_version") != "range-grounding-v4"
        ):
            return False
        identifier = str(source["provider_candidate_id"])
        if not any(
            item.get("candidate_id") == identifier
            and item.get("field_id") == "currency"
            and item.get("reason") in _REASONS
            for item in row["adjustments_json"]
        ):
            return False
        checked = [
            c for c in row["result_json"]["result"]["candidates"] if c["candidate_id"] == identifier
        ]
        drafts = [
            c for c in row["normalized_batch_json"]["candidates"] if c["candidate_id"] == identifier
        ]
        if len(checked) != 1 or len(drafts) != 1:
            return False
        candidate, draft = checked[0], drafts[0]
        if (
            candidate["status"] != "AI_VERIFIED"
            or candidate["candidate_kind"] != "rider"
            or draft["candidate_kind"] != "rider"
            or not candidate["provider_request_ids"]
            or len({f["field_id"] for f in candidate["fields"]}) != len(candidate["fields"])
            or {f["field_id"]: f["value"] for f in candidate["fields"]} != values
            or any(set(f["evidence_ids"]) != citations[f["field_id"]] for f in candidate["fields"])
        ):
            return False
        drafted = {f["field_id"]: f for f in draft["fields"]}
        if (
            len(drafted) != len(draft["fields"])
            or drafted["currency"]["value"] != values["currency"]
            or drafted["sum_assured"]["value"] != values["sum_assured"]
            or not drafted["currency"]["evidence_ids"]
            or set(drafted["currency"]["evidence_ids"])
            != set(drafted["sum_assured"]["evidence_ids"])
            or not set(drafted["currency"]["evidence_ids"]) <= citations["currency"]
        ):
            return False
        nodes = {node["node_id"]: node for node in source["structure_json"]["nodes"]}
        for field in ("sum_assured", "currency"):
            addresses = [refs[key] for key in citations[field]]
            if any(
                ref["source_role"] != "policy"
                or ref["page"] != nodes[ref["node_id"]]["page_number"]
                or nodes[ref["node_id"]]["source_layer"] != "native"
                or type(ref["start"]) is not int
                or type(ref["end"]) is not int
                or not 0 <= ref["start"] < ref["end"] <= len(nodes[ref["node_id"]]["text"])
                for ref in addresses
            ):
                return False
            witness = _money_witness(nodes, addresses, values["rider_name"])
            if witness != (target["insured_amount"], values["currency"]):
                return False
        return True
    except KeyError, TypeError, ValueError, ArithmeticError:
        return False
