"""Replay a retained citation address against its original scoped generation."""

from typing import Any

import psycopg

from familycare_api.common.scope import HouseholdScope
from familycare_api.guidance.models import GuidanceSemanticEvidence
from familycare_api.guidance_evidence.models import (
    GuidanceEvidenceDetail,
    bounded_text,
    unavailable,
)
from familycare_api.insurance_documents.terms_body_validation import _layout_box


def _root(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    reference: GuidanceSemanticEvidence,
) -> dict[str, Any] | None:
    if reference.review_job_id is None:
        row = connection.execute(
            "SELECT r.root_json AS root FROM terms_semantic_root_publications r "
            "JOIN terms_semantic_publications p ON p.id=r.publication_id "
            "WHERE p.id=%s AND p.household_space_id=%s AND p.terms_edition_id=%s "
            "AND r.root_node_id=%s AND r.manifest_sha256=%s",
            (
                reference.publication_id,
                scope.household_space_id,
                reference.terms_edition_id,
                reference.root_node_id,
                reference.manifest_sha256,
            ),
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT root.value AS root FROM guidance_review_publications p "
            "JOIN guidance_review_jobs j ON j.id=p.review_job_id "
            "CROSS JOIN LATERAL jsonb_array_elements(p.compiled_json->'roots') root(value) "
            "WHERE p.id=%s AND p.review_job_id=%s AND j.household_space_id=%s "
            "AND j.state IN ('completed','partial','disagreement') "
            "AND root.value->>'root_node_id'=%s AND root.value->>'manifest_sha256'=%s",
            (
                reference.publication_id,
                reference.review_job_id,
                scope.household_space_id,
                reference.root_node_id,
                reference.manifest_sha256,
            ),
        ).fetchone()
    return row["root"] if row is not None and isinstance(row["root"], dict) else None


def read_semantic_evidence(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    reference: GuidanceSemanticEvidence,
) -> GuidanceEvidenceDetail:
    try:
        root = _root(connection, scope, reference)
        if root is None:
            return unavailable(reference)
        manifest = root["manifest"]
        citations = [
            c for c in manifest["citations"] if c["citation_id"] == str(reference.citation_id)
        ]
        if (
            len(citations) != 1
            or str(reference.citation_id) not in manifest["verified_citation_ids"]
            or reference.root_node_id not in manifest["verified_node_ids"]
        ):
            return unavailable(reference, "EVIDENCE_SOURCE_ADDRESS_UNAVAILABLE")
        citation = citations[0]
        sources = [s for s in manifest["sources"] if s["source_id"] == citation["source_id"]]
        if len(sources) != 1:
            return unavailable(reference)
        source = sources[0]
        if (
            source["document_version_id"] != str(reference.document_version_id)
            or source["terms_edition_id"] != str(reference.terms_edition_id)
            or source["generation_id"] != str(reference.generation_id)
            or source["content_sha256"] != reference.source_sha256
            or citation["node_id"] != reference.source_node_id
            or citation["page_number"] != reference.page_start
            or citation["start"] != reference.start
            or citation["end"] != reference.end
            or citation["source_layer"] != reference.source_layer
            or tuple(citation["bbox"]) != reference.bbox
        ):
            return unavailable(reference, "EVIDENCE_SOURCE_ADDRESS_UNAVAILABLE")
        identity = connection.execute(
            "SELECT e.product_display,e.edition_date FROM terms_editions e "
            "JOIN document_versions v ON v.id=e.document_version_id "
            "AND v.content_sha256=e.content_sha256 "
            "JOIN documents d ON d.id=v.document_id AND d.deleted_at IS NULL "
            "JOIN document_structure_generations g ON g.id=%s "
            "AND g.household_space_id=e.household_space_id "
            "AND g.document_version_id=v.id AND NOT g.cancelled AND g.identity_sha256=%s "
            "WHERE e.id=%s AND e.household_space_id=%s AND e.document_version_id=%s "
            "AND e.content_sha256=%s AND e.deleted_at IS NULL",
            (
                reference.generation_id,
                source["structure_identity_sha256"],
                reference.terms_edition_id,
                scope.household_space_id,
                reference.document_version_id,
                reference.source_sha256,
            ),
        ).fetchone()
        if identity is None:
            return unavailable(reference)
        # A single retained node is sufficient to replay this address. Do not load
        # every page/node in a large component just to open one optional citation.
        nodes = [
            row["node"]
            for row in connection.execute(
                "SELECT node FROM document_structure_nodes_by_ids(%s,%s,%s) LIMIT 2",
                (reference.generation_id, scope.household_space_id, [reference.source_node_id]),
            ).fetchall()
        ]
        if (
            len(nodes) != 1
            or nodes[0]["node_id"] != reference.source_node_id
            or nodes[0]["page_number"] != reference.page_start
            or nodes[0]["source_layer"] != reference.source_layer
            or tuple(_layout_box(nodes[0])) != reference.bbox
            or nodes[0]["text"][reference.start : reference.end] != citation["text"]
        ):
            return unavailable(reference, "EVIDENCE_SOURCE_ADDRESS_UNAVAILABLE")
        text, truncated = bounded_text(citation["text"])
        if not text:
            return unavailable(reference)
        return GuidanceEvidenceDetail(
            evidence=reference,
            content_kind="ORIGINAL",
            document_label=(identity["product_display"] or "약관 문서")[:200],
            document_version_id=reference.document_version_id,
            terms_edition_id=reference.terms_edition_id,
            terms_edition_label=f"{identity['edition_date'].isoformat()} 판본"
            if identity["edition_date"]
            else None,
            page_start=reference.page_start,
            page_end=reference.page_end,
            text=text,
            truncated=truncated,
            bbox=reference.bbox,
            reason_codes=("EVIDENCE_VERIFIED_CITATION",),
        )
    except KeyError, TypeError, ValueError, IndexError:
        return unavailable(reference, "EVIDENCE_SOURCE_ADDRESS_UNAVAILABLE")
