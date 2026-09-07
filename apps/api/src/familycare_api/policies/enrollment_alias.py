"""Read only source aliases whose retained publication proves the same contract."""

from typing import Any
from uuid import UUID

import psycopg

from familycare_api.policies.contract_source_locator import contract_source_locator


def proven_rider_source_alias(
    connection: psycopg.Connection[dict[str, Any]], household: UUID, row: dict[str, Any]
) -> bool:
    publications = connection.execute(
        "SELECT s.association_json,g.structure_json,j.document_version_id,p.rider_id "
        "FROM range_enrollment_publications p JOIN policy_range_candidate_sources s ON "
        "s.candidate_version_id=p.source_candidate_version_id "
        "JOIN policy_structuring_jobs j ON j.id=s.job_id "
        "AND j.household_space_id=p.household_space_id "
        "JOIN document_policy_range_plans plan ON plan.job_id=j.id "
        "JOIN document_structure_generations g ON g.id=plan.generation_id "
        "AND g.household_space_id=p.household_space_id "
        "JOIN document_versions v ON v.id=j.document_version_id "
        "JOIN documents d ON d.id=v.document_id AND d.document_kind='policy' "
        "AND d.deleted_at IS NULL "
        "WHERE p.household_space_id=%s AND p.policy_contract_id=%s "
        "AND v.content_sha256=%s AND g.structure_json->'lineage'->>'content_sha256'=%s "
        "AND (SELECT array_agg(party.family_member_id::text) FROM policy_parties party "
        "WHERE party.household_space_id=p.household_space_id "
        "AND party.policy_contract_id=p.policy_contract_id "
        "AND party.role='primary_insured' AND party.deleted_at IS NULL)="
        "ARRAY[s.association_json->>'family_member_id'] "
        "AND ((p.rider_id IS NULL AND j.document_version_id=%s) OR "
        "(p.rider_id=%s AND j.document_version_id=%s AND EXISTS "
        "(SELECT 1 FROM analysis_candidate_evidence ce JOIN evidence e ON e.id=ce.evidence_id "
        "WHERE ce.candidate_version_id=p.candidate_version_id AND ce.field_id='rider_name' "
        "AND ce.evidence_id=%s AND e.household_space_id=p.household_space_id "
        "AND e.document_version_id=j.document_version_id AND e.extraction_id=j.extraction_id "
        "AND e.content_sha256=v.content_sha256)))",
        (
            household,
            row["policy_contract_id"],
            row["source_content_sha256"],
            row["source_content_sha256"],
            row["policy_source_document_version_id"],
            row["id"],
            row["source_document_version_id"],
            row["source_id"],
        ),
    ).fetchall()
    original: set[tuple[tuple[str, str], ...]] = set()
    aliases: set[tuple[tuple[str, str], ...]] = set()
    for publication in publications:
        locator = contract_source_locator(
            publication["structure_json"], publication["association_json"]
        )
        if locator is None:
            continue
        key = tuple(sorted(locator.items()))
        (original if publication["rider_id"] is None else aliases).add(key)
    return len(original) == 1 and aliases == original
