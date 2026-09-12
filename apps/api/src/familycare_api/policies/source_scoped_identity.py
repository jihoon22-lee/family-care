"""Admit missing issuer identity only for independently checked retained source facts."""

import re
import unicodedata
from typing import Any

import psycopg

from familycare_api.policies.contract_source_locator import contract_source_locator

SOURCE_IDENTITY_REVISION = "source-scoped-policy-identity-v1"
INSURER_UNRESOLVED_REASON = "INSURER_SOURCE_UNVERIFIED"


def _key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _product_proven(source: dict[str, Any], product: str, evidence_ids: set[str]) -> bool:
    nodes = {node["node_id"]: node for node in source["structure_json"]["nodes"]}
    normalized = re.escape(_key(product))
    for ref in source["source_refs"]:
        if (
            ref["evidence_id"] not in evidence_ids
            or not ref["primary"]
            or ref["source_role"] != "policy"
        ):
            continue
        node = nodes.get(ref["node_id"])
        if node is None or node["source_layer"] != "native" or node["page_number"] != ref["page"]:
            continue
        start, end = ref["start"], ref["end"]
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(node["text"])
        ):
            continue
        text = node["text"][start:end]
        if re.search(r"(?<!\w)" + normalized + r"(?!\w)", _key(text)) or any(
            re.search(r"(?<!\w)" + normalized + r"_보험증권$", _key(line))
            for line in text.splitlines()
        ):
            return True
    return False


def source_scoped_identity(
    connection: psycopg.Connection[dict[str, Any]],
    version: dict[str, Any],
    source: dict[str, Any],
    values: dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    allow_insurer: bool = False,
) -> dict[str, Any] | None:
    """Validate the original v8 verified candidate; user edits never grant this exception."""
    try:
        if (
            version["candidate_kind"] != "policy_contract"
            or version["status"] != "AI_VERIFIED"
            or version["id"] != source["candidate_version_id"]
            or version["generator_version"] != "policy-draft-normalization-v3"
            or source["pipeline_version"] != "retained-policy-association-v8"
            or ("insurer" in values and not allow_insurer)
            or not isinstance(values.get("product_name"), str)
            or not 1 <= len(values["product_name"]) <= 200
        ):
            return None
        locator = contract_source_locator(source["structure_json"], source["association_json"])
        nodes = {node["node_id"]: node for node in source["structure_json"]["nodes"]}
        if locator is None or any(
            nodes.get(ref["node_id"], {}).get("source_layer") != "native"
            for ref in source["association_json"]["anchor_refs"]
        ):
            return None
        row = connection.execute(
            "SELECT r.result_json,receipt.normalized_batch_json,receipt.normalization_revision "
            "FROM document_policy_ranges r JOIN policy_range_replay_sources receipt "
            "ON receipt.job_id=r.job_id AND receipt.envelope_id=r.envelope_id "
            "WHERE r.job_id=%s AND r.generation_id=%s AND r.envelope_id=%s "
            "AND r.state IN ('REVIEW','COMPLETE') FOR SHARE OF r,receipt",
            (source["job_id"], source["generation_id"], source["envelope_id"]),
        ).fetchone()
        if row is None or row["normalization_revision"] != "policy-draft-normalization-v3":
            return None
        payload = row["result_json"]
        if payload.get("program_validation_version") != "range-grounding-v4":
            return None
        candidate_id = str(source["provider_candidate_id"])
        checked = [c for c in payload["result"]["candidates"] if c["candidate_id"] == candidate_id]
        drafts = [
            c
            for c in row["normalized_batch_json"]["candidates"]
            if c["candidate_id"] == candidate_id
        ]
        if len(checked) != 1 or len(drafts) != 1:
            return None
        candidate, draft = checked[0], drafts[0]
        if (
            candidate["status"] != "AI_VERIFIED"
            or candidate["candidate_kind"] != "policy_contract"
            or draft["candidate_kind"] != "policy_contract"
            or candidate["fields"] != draft["fields"]
            or not candidate["provider_request_ids"]
        ):
            return None
        fields = candidate["fields"]
        if (
            len({f["field_id"] for f in fields}) != len(fields)
            or {f["field_id"]: f["value"] for f in fields} != values
        ):
            return None
        citations = {
            field: {str(item["evidence_id"]) for item in evidence if item["field_id"] == field}
            for field in values
        }
        if any(set(f["evidence_ids"]) != citations[f["field_id"]] for f in fields):
            return None
        if not _product_proven(source, values["product_name"], citations["product_name"]):
            return None
        if "insurer" in values:
            insurer = values["insurer"]
            if not isinstance(insurer, str) or not 1 <= len(insurer) <= 160:
                return None
            proven = False
            for ref in source["source_refs"]:
                node = nodes.get(ref["node_id"], {})
                if (
                    ref["evidence_id"] not in citations["insurer"]
                    or ref["source_role"] != "policy"
                    or not ref["primary"]
                    or node.get("source_layer") != "native"
                ):
                    continue
                for line in node["text"][ref["start"] : ref["end"]].splitlines():
                    pattern = (
                        r"^(?:보험사|보험회사|인수회사|발급회사|발급기관|발행기관|insurer|issuer)"
                        r"\s*[:：]\s*" + re.escape(_key(insurer)) + r"(?:$|\s*[;|])"
                    )
                    proven = proven or re.match(pattern, _key(line)) is not None
            if not proven:
                return None
        return {
            "revision": SOURCE_IDENTITY_REVISION,
            "insurer_state": "UNKNOWN",
            "reason_code": INSURER_UNRESOLVED_REASON,
            "contract_source": locator,
            "normalization_revision": row["normalization_revision"],
            "program_validation_version": "range-grounding-v4",
        }
    except KeyError, TypeError, ValueError, AttributeError:
        return None
