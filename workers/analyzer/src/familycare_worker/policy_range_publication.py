"""Publish retained range facts for review in the same source transaction."""

from __future__ import annotations

from typing import Any
from uuid import uuid4, uuid5

import psycopg
from psycopg.types.json import Jsonb

from familycare_worker.ai.policy_ranges import PolicyRangeEnvelope
from familycare_worker.ai.schemas import CandidatePipelineResult
from familycare_worker.policy_jobs import PolicyStructuringJobRecord
from familycare_worker.policy_source_association import (
    load_local_members,
    member_identity_fingerprint,
)


def publish_range_candidates(
    connection: psycopg.Connection[dict[str, Any]],
    job: PolicyStructuringJobRecord,
    envelope: PolicyRangeEnvelope,
    result: CandidatePipelineResult,
) -> None:
    """Keep full source spans in provenance and bounded excerpts in the review API."""
    sources = {item.evidence_id: item for item in envelope.evidence}
    plan = connection.execute(
        "SELECT associations_json FROM document_policy_range_plans WHERE job_id = %s",
        (job.id,),
    ).fetchone()
    assert plan is not None
    associations = plan["associations_json"]
    members = load_local_members(connection, job.household_space_id)
    current_identity = member_identity_fingerprint(members) == associations.get(
        "member_fingerprint"
    )
    replay = (
        connection.execute(
            "SELECT normalization_revision FROM policy_range_replay_sources "
            "WHERE job_id=%s AND envelope_id=%s",
            (job.id, envelope.envelope_id),
        ).fetchone()
        if job.pipeline_version
        in {
            "retained-policy-association-v4",
            "retained-policy-association-v6",
            "policy-range-normalized-v1",
            "retained-policy-association-v7",
            "policy-range-normalized-v2",
        }
        else None
    )
    generator = "policy-range-structurer-v3" if replay is None else replay["normalization_revision"]
    for candidate in result.candidates:
        version_id = uuid4()
        scoped_source_id = uuid5(job.id, f"{envelope.envelope_id}:{candidate.candidate_id}")
        connection.execute(
            "INSERT INTO analysis_candidate_versions (id, review_item_id, household_space_id, "
            "candidate_kind, aggregate_id, version, is_current, status, schema_version, "
            "generator_version, verifier_version, provider_request_id, issues, "
            "structuring_job_id, source_candidate_id) "
            "VALUES (%s,%s,%s,%s,%s,1,true,%s,'1',%s, "
            "'policy-batch-verifier-v2',%s,%s,%s,%s)",
            (
                version_id,
                uuid4(),
                job.household_space_id,
                candidate.candidate_kind,
                job.policy_aggregate_id,
                candidate.status,
                generator,
                candidate.provider_request_ids[-1] if candidate.provider_request_ids else None,
                Jsonb([{"code": code, "field_id": None} for code in candidate.issue_codes[:8]]),
                job.id,
                scoped_source_id,
            ),
        )
        cited = set()
        for position, field in enumerate(candidate.fields):
            connection.execute(
                "INSERT INTO analysis_candidate_fields "
                "(candidate_version_id,field_id,position,value) "
                "VALUES (%s,%s,%s,%s)",
                (version_id, field.field_id, position, Jsonb(field.value)),
            )
            for key in field.evidence_ids:
                source = sources[key]
                coordinates = source.bbox or (None, None, None, None)
                if key not in cited:
                    inserted = connection.execute(
                        "INSERT INTO evidence(id, household_space_id, document_version_id, "
                        "extraction_id, content_sha256, physical_page, x0,y0,x1,y1,review_state) "
                        "SELECT %s,%s,v.id,%s,v.content_sha256,%s,%s,%s,%s,%s,'NEEDS_REVIEW' "
                        "FROM document_versions v WHERE v.id = %s ON CONFLICT (id) DO NOTHING",
                        (
                            key,
                            job.household_space_id,
                            job.extraction_id,
                            source.page,
                            *coordinates,
                            job.document_version_id,
                        ),
                    )
                    if inserted.rowcount == 0:
                        stored = connection.execute(
                            "SELECT id FROM evidence WHERE id = %s AND household_space_id = %s "
                            "AND document_version_id = %s AND extraction_id = %s "
                            "AND content_sha256 = (SELECT content_sha256 FROM document_versions "
                            "WHERE id = evidence.document_version_id) "
                            "AND physical_page = %s AND x0 IS NOT DISTINCT FROM %s "
                            "AND y0 IS NOT DISTINCT FROM %s AND x1 IS NOT DISTINCT FROM %s "
                            "AND y1 IS NOT DISTINCT FROM %s",
                            (
                                key,
                                job.household_space_id,
                                job.document_version_id,
                                job.extraction_id,
                                source.page,
                                *coordinates,
                            ),
                        ).fetchone()
                        if stored is None:
                            raise ValueError("POLICY_RANGE_EVIDENCE_CONFLICT")
                    cited.add(key)
                # Full minimized text and raw offsets remain in the immutable envelope.
                # An excerpt is only a preview, never the basis of value validation.
                text = source.text
                value = str(field.value)
                offset = max(0, text.casefold().find(value.casefold()) - 40)
                excerpt = text[offset : offset + 240]
                connection.execute(
                    "INSERT INTO analysis_candidate_evidence(candidate_version_id,field_id, "
                    "document_version_id,evidence_id,physical_page,bounded_excerpt,x0,y0,x1,y1) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        version_id,
                        field.field_id,
                        job.document_version_id,
                        key,
                        source.page,
                        excerpt,
                        *coordinates,
                    ),
                )
        refs = [
            {
                "evidence_id": str(key),
                "document_version_id": str(job.document_version_id),
                "extraction_id": str(job.extraction_id),
                "node_id": sources[key].node_id,
                "page": sources[key].page,
                "start": sources[key].start,
                "end": sources[key].end,
                "source_role": sources[key].source_role,
                "primary": sources[key].primary,
            }
            for key in sorted(cited, key=str)
        ]
        node_associations = [
            associations.get("nodes", {}).get(sources[key].node_id, {})
            for key in cited
            if sources[key].primary
        ]
        association = {"state": "UNRESOLVED"}
        if current_identity and node_associations:
            first = node_associations[0]
            if (
                first.get("state") == "RESOLVED"
                and first.get("family_member_id") == str(job.family_member_id)
                and all(
                    item.get("state") == "RESOLVED"
                    and item.get("contract_scope_id") == first.get("contract_scope_id")
                    and item.get("family_member_id") == first.get("family_member_id")
                    for item in node_associations
                )
            ):
                association = first
        connection.execute(
            "INSERT INTO policy_range_candidate_sources(candidate_version_id,job_id,envelope_id, "
            "provider_candidate_id,source_refs,association_json) VALUES (%s,%s,%s,%s,%s,%s)",
            (
                version_id,
                job.id,
                envelope.envelope_id,
                candidate.candidate_id,
                Jsonb(refs),
                Jsonb(association),
            ),
        )
