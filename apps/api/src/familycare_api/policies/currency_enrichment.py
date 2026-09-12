"""Fill absent enrollment money fields from the same independently verified source."""

from decimal import Decimal
from typing import Any
from uuid import UUID

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
    return _money_enrichment_proven(
        connection, version, source, values, evidence, target, previous, fill_amount=False
    )


def amount_enrichment_proven(
    connection: psycopg.Connection[dict[str, Any]],
    version: dict[str, Any],
    source: dict[str, Any],
    values: dict[str, Any],
    evidence: list[dict[str, Any]],
    target: dict[str, Any],
    previous: dict[str, Any],
) -> bool:
    """Only an old program-removed amount and originally absent currency may be filled."""
    return _money_enrichment_proven(
        connection, version, source, values, evidence, target, previous, fill_amount=True
    )


def _money_enrichment_proven(
    connection: psycopg.Connection[dict[str, Any]],
    version: dict[str, Any],
    source: dict[str, Any],
    values: dict[str, Any],
    evidence: list[dict[str, Any]],
    target: dict[str, Any],
    previous: dict[str, Any],
    *,
    fill_amount: bool,
) -> bool:
    """The caller holds current-candidate/ledger locks and excludes user publications."""
    try:
        pipeline = source["pipeline_version"] if fill_amount else "retained-policy-association-v9"
        revision = (
            {
                "retained-policy-association-v12": "policy-draft-normalization-v6",
                "retained-policy-association-v13": "policy-draft-normalization-v7",
            }.get(pipeline)
            if fill_amount
            else "policy-draft-normalization-v4"
        )
        if revision is None:
            return False
        additions = {"sum_assured", "currency"} if fill_amount else {"currency"}
        if (
            version["candidate_kind"] != "rider"
            or version["status"] != "AI_VERIFIED"
            or version["id"] != source["candidate_version_id"]
            or version["generator_version"] != revision
            or source["pipeline_version"] != pipeline
            or target["currency"] is not None
            or target["version"] != previous["ledger_version"]
            or previous["authority"] != "PROGRAM_VERIFIED"
            or bool(additions & previous["field_values"].keys())
            or {key: value for key, value in values.items() if key not in additions}
            != previous["field_values"]
            or values.get("currency") not in {"KRW", "USD", "EUR", "JPY"}
            or values.get("rider_name") != target["display_name"]
            or isinstance(values.get("sum_assured"), bool)
            or (
                target["insured_amount"] is not None
                if fill_amount
                else Decimal(str(values["sum_assured"])) != target["insured_amount"]
            )
            or contract_source_locator(source["structure_json"], source["association_json"]) is None
        ):
            return False
        amount = Decimal(str(values["sum_assured"]))
        if fill_amount and (
            not amount.is_finite()
            or not 0 <= amount < Decimal("1e16")
            or amount != amount.quantize(Decimal(".01"))
        ):
            return False
        old = connection.execute(
            "SELECT s.source_refs,s.association_json,p.generation_id,j.document_version_id,"
            "j.extraction_id,s.job_id AS old_job_id,s.envelope_id AS old_envelope_id,"
            "s.provider_candidate_id AS "
            "old_provider_candidate_id,c.generator_version AS "
            "old_generator_revision,ARRAY(SELECT e.evidence_id::text FROM "
            "analysis_candidate_evidence e "
            "WHERE e.candidate_version_id=c.id AND e.field_id='rider_name' "
            "ORDER BY e.evidence_id) "
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
            "SELECT r.result_json,receipt.normalized_batch_json,receipt.adjustments_json,"
            "receipt.source_provider_request_id "
            "FROM document_policy_ranges r JOIN policy_range_replay_sources receipt "
            "ON receipt.job_id=r.job_id AND receipt.envelope_id=r.envelope_id "
            "WHERE r.job_id=%s AND r.generation_id=%s AND r.envelope_id=%s "
            "AND r.state IN ('REVIEW','COMPLETE') "
            f"AND receipt.normalization_revision='{revision}' "
            "FOR SHARE OF r,receipt",
            (source["job_id"], source["generation_id"], source["envelope_id"]),
        ).fetchone()
        if row is None or row["result_json"].get("program_validation_version") != (
            "range-grounding-v5"
            if pipeline == "retained-policy-association-v13"
            else "range-grounding-v4"
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
        if fill_amount and not _previous_amount_was_removed(
            connection, old, source, row, candidate, drafted
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
            if fill_amount:
                rows = {
                    ref["node_id"]
                    for ref in addresses
                    if ref["primary"]
                    and nodes[ref["node_id"]].get("kind") == "TABLE_ROW"
                    and nodes[ref["node_id"]].get("row_role") == "data"
                }
                if len(rows) != 1:
                    return False
                node_id = next(iter(rows))
                if not {ref["node_id"] for ref in addresses} <= {
                    node_id,
                    *nodes[node_id].get("context_node_ids", ()),
                }:
                    return False
            witness = _money_witness(nodes, addresses, values["rider_name"])
            if witness != (amount if fill_amount else target["insured_amount"], values["currency"]):
                return False
        return True
    except KeyError, TypeError, ValueError, ArithmeticError:
        return False


def _previous_amount_was_removed(
    connection: psycopg.Connection[dict[str, Any]],
    old: dict[str, Any],
    source: dict[str, Any],
    receipt: dict[str, Any],
    checked: dict[str, Any],
    drafted: dict[str, dict[str, Any]],
) -> bool:
    identifier = str(source["provider_candidate_id"])
    if (
        old["old_generator_revision"] != "policy-draft-normalization-v1"
        or old["old_envelope_id"] != source["envelope_id"]
        or str(old["old_provider_candidate_id"]) != identifier
        or not any(
            item.get("candidate_id") == identifier
            and item.get("field_id") == "sum_assured"
            and item.get("reason") == "AMOUNT_SCALED_FROM_EXPLICIT_UNIT"
            for item in receipt["adjustments_json"]
        )
        or len(set(checked["provider_request_ids"])) < 2
    ):
        return False
    prior = connection.execute(
        "SELECT "
        "receipt.normalized_batch_json,receipt.adjustments_json,raw.response_json,raw.request_id,"
        "verification.response_json AS verifier_response FROM "
        "policy_range_replay_sources receipt "
        "JOIN policy_provider_requests raw ON raw.id=receipt.source_provider_request_id "
        "AND raw.state='SUCCEEDED' "
        "JOIN document_policy_range_plans old_plan ON old_plan.job_id=receipt.job_id "
        "JOIN document_policy_range_plans current_plan ON current_plan.job_id=%s "
        "AND current_plan.generation_id=old_plan.generation_id "
        "AND current_plan.associations_json=old_plan.associations_json "
        "JOIN document_policy_ranges old_range ON old_range.job_id=receipt.job_id "
        "AND old_range.envelope_id=receipt.envelope_id AND "
        "old_range.generation_id=old_plan.generation_id "
        "JOIN document_policy_ranges current_range ON "
        "current_range.job_id=current_plan.job_id "
        "AND current_range.generation_id=current_plan.generation_id "
        "AND current_range.envelope_id=old_range.envelope_id "
        "AND current_range.envelope_json=old_range.envelope_json "
        "JOIN policy_provider_requests verification "
        "ON verification.job_id=%s AND verification.request_id=%s AND "
        "verification.state='SUCCEEDED' "
        "WHERE receipt.job_id=%s AND receipt.envelope_id=%s "
        "AND receipt.normalization_revision='policy-draft-normalization-v1' "
        "AND receipt.source_provider_request_id=%s "
        "FOR SHARE OF receipt,raw,verification,old_range,current_range",
        (
            source["job_id"],
            source["job_id"],
            checked["provider_request_ids"][-1],
            old["old_job_id"],
            old["old_envelope_id"],
            receipt["source_provider_request_id"],
        ),
    ).fetchall()
    if len(prior) != 1:
        return False
    previous = prior[0]
    if previous["request_id"] == checked["provider_request_ids"][-1] or not any(
        item.get("candidate_id") == identifier
        and item.get("field_id") == "sum_assured"
        and item.get("reason") == "OPTIONAL_FIELD_UNSUPPORTED"
        for item in previous["adjustments_json"]
    ):
        return False
    original = [
        c
        for c in previous["response_json"]["candidates"]
        if UUID(c["candidate_id"]) == UUID(identifier)
    ]
    normalized = [
        c
        for c in previous["normalized_batch_json"]["candidates"]
        if c["candidate_id"] == identifier
    ]
    decisions = [
        c
        for c in previous["verifier_response"]["decisions"]
        if UUID(c["candidate_id"]) == UUID(identifier)
    ]
    if len(original) != 1 or len(normalized) != 1 or len(decisions) != 1:
        return False
    fields = {f["field_id"]: f for f in original[0]["fields"]}
    return (
        original[0]["candidate_kind"] == normalized[0]["candidate_kind"] == "rider"
        and len(fields) == len(original[0]["fields"])
        and "currency" not in fields
        and all(f["field_id"] not in {"sum_assured", "currency"} for f in normalized[0]["fields"])
        and type(fields["sum_assured"]["value"]) in (int, float)
        and fields["sum_assured"]["value"] != drafted["sum_assured"]["value"]
        and (
            set(fields["sum_assured"]["evidence_ids"])
            <= set(drafted["sum_assured"]["evidence_ids"])
            if source["pipeline_version"] == "retained-policy-association-v13"
            else set(fields["sum_assured"]["evidence_ids"])
            == set(drafted["sum_assured"]["evidence_ids"])
        )
        and decisions[0]["decision"] == "approved"
        and not decisions[0]["issue_codes"]
        and all(
            {UUID(key) for key in field["evidence_ids"]}
            <= {UUID(key) for key in decisions[0]["evidence_ids"]}
            for field in drafted.values()
        )
    )
