"""Expose retained private knowledge summaries without labeling them as originals."""

from typing import Any

import psycopg

from familycare_api.common.scope import HouseholdScope
from familycare_api.guidance.models import GuidanceEvidence
from familycare_api.guidance_evidence.models import (
    GuidanceEvidenceDetail,
    bounded_text,
    unavailable,
)


def read_private_evidence(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    reference: GuidanceEvidence,
) -> GuidanceEvidenceDetail:
    if reference.publication_id is None or reference.source_sha256 is None:
        return unavailable(reference, "EVIDENCE_PRIVATE_SOURCE_UNAVAILABLE")
    rows = connection.execute(
        "WITH citations AS ("
        "SELECT c.* FROM private_knowledge_rule_citations c "
        "JOIN private_knowledge_rule_publications p "
        "ON p.id=c.rule_publication_id AND p.household_space_id=c.household_space_id "
        "AND p.knowledge_import_run_id=c.knowledge_import_run_id "
        "WHERE p.id=%(publication)s AND p.household_space_id=%(household)s), selected AS ("
        "SELECT knowledge_import_run_id,terms_section_id,source_clause_id,"
        "fact_id,page_start,page_end,"
        "source_text_sha256 FROM citations UNION ALL "
        "SELECT c.knowledge_import_run_id,c.terms_section_id,c.source_clause_id,"
        "c.fact_id,c.page_start,"
        "c.page_end,c.source_text_sha256 FROM private_knowledge_calculation_citations c "
        "JOIN private_knowledge_calculation_publications p ON p.id=c.calculation_publication_id "
        "AND p.household_space_id=c.household_space_id "
        "AND p.knowledge_import_run_id=c.knowledge_import_run_id "
        "WHERE p.id=%(publication)s AND p.household_space_id=%(household)s) "
        "SELECT DISTINCT substring(s.heading FROM 1 FOR 160) AS heading, "
        "substring(review.section_summary FROM 1 FOR 2049) AS summary, "
        "char_length(review.section_summary)>2048 AS clipped,b.id AS binding_id, "
        "CASE WHEN b.binding_decision='MATCH' AND NOT b.binding_conflict AND d.deleted_at IS NULL "
        "AND b.expected_content_sha256=v.content_sha256 THEN v.id END AS document_version_id, "
        "substring(clause.clause_label FROM 1 FOR 160) AS clause_label "
        "FROM selected c JOIN private_knowledge_terms_sections s ON s.id=c.terms_section_id "
        "AND s.import_run_id=c.knowledge_import_run_id "
        "JOIN private_knowledge_import_runs run ON run.id=s.import_run_id "
        "AND run.household_space_id=%(household)s "
        "JOIN private_knowledge_semantic_reviews review ON review.terms_section_id=s.id "
        "AND review.import_run_id=s.import_run_id "
        "LEFT JOIN private_knowledge_source_clauses clause ON clause.id=c.source_clause_id "
        "AND clause.import_run_id=s.import_run_id "
        "LEFT JOIN private_knowledge_document_bindings b ON b.import_run_id=s.import_run_id "
        "AND b.household_space_id=run.household_space_id "
        "AND b.source_alias_digest_sha256=s.terms_source_alias_digest_sha256 "
        "LEFT JOIN document_versions v ON v.id=b.document_version_id "
        "LEFT JOIN documents d ON d.id=v.document_id "
        "WHERE c.terms_section_id=%(section)s AND c.page_start=%(start)s AND c.page_end=%(end)s "
        "AND c.source_text_sha256=%(digest)s "
        "AND ((c.source_clause_id IS NULL AND c.fact_id IS NULL "
        "AND s.source_record_digest_sha256=c.source_text_sha256 "
        "AND s.page_start=c.page_start AND s.page_end=c.page_end) "
        "OR (clause.terms_section_id=s.id AND clause.page_start=c.page_start "
        "AND clause.page_end=c.page_end "
        "AND clause.source_text_sha256=c.source_text_sha256 AND (c.fact_id IS NULL OR EXISTS("
        "SELECT 1 FROM private_knowledge_fact_citations f WHERE f.import_run_id=s.import_run_id "
        "AND f.fact_id=c.fact_id AND f.source_clause_id=clause.id AND f.page_start=c.page_start "
        "AND f.page_end=c.page_end AND f.source_text_sha256=c.source_text_sha256)))) LIMIT 3",
        {
            "household": scope.household_space_id,
            "publication": reference.publication_id,
            "section": reference.evidence_id,
            "start": reference.page_start,
            "end": reference.page_end,
            "digest": reference.source_sha256,
        },
    ).fetchall()
    if not rows or len(rows) > 2 or len({row["summary"] for row in rows}) != 1:
        return unavailable(reference, "EVIDENCE_PRIVATE_SOURCE_UNAVAILABLE")
    row = rows[0]
    if not isinstance(row["summary"], str) or not row["summary"].strip():
        return unavailable(reference, "EVIDENCE_PRIVATE_SOURCE_UNAVAILABLE")
    text, clipped = bounded_text(row["summary"])
    labels = {r["clause_label"] for r in rows}
    return GuidanceEvidenceDetail(
        evidence=reference,
        content_kind="SUMMARY",
        document_label="보관된 약관 지식",
        document_version_id=row["document_version_id"],
        source_document_ref=row["binding_id"],
        page_start=reference.page_start,
        page_end=reference.page_end,
        clause_label=(row["clause_label"] if len(labels) == 1 else None) or row["heading"] or None,
        text=text,
        truncated=clipped or row["clipped"],
        reason_codes=("EVIDENCE_PRIVATE_SUMMARY", "EVIDENCE_ORIGINAL_NOT_INCLUDED"),
    )
