"""PostgreSQL reader for bounded, household-scoped Evidence disclosure."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.errors import (
    DecisionRepositoryUnavailable,
    EvidenceNotFound,
)
from familycare_api.decisions.evidence_service import EvidenceDetail

_WHITESPACE = re.compile(r"\s+")


def _database_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise DecisionRepositoryUnavailable
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


class EvidenceRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def get_evidence(self, scope: HouseholdScope, evidence_id: UUID) -> EvidenceDetail:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    SELECT evidence.id, evidence.document_version_id,
                           evidence.physical_page,
                           evidence.x0, evidence.y0, evidence.x1, evidence.y1,
                           evidence.review_state,
                           COALESCE(
                             terms.product_display,
                             CASE document.document_kind
                               WHEN 'terms' THEN '약관 문서'
                               WHEN 'policy' THEN '증권 문서'
                               ELSE '보험 근거 문서'
                             END
                           ) AS document_label,
                           clause.label AS clause_label,
                           COALESCE(clause.normalized_text, excerpt.text, '') AS excerpt
                    FROM evidence
                    JOIN document_versions AS version
                      ON version.id = evidence.document_version_id
                     AND version.content_sha256 = evidence.content_sha256
                    JOIN documents AS document
                      ON document.id = version.document_id
                     AND document.deleted_at IS NULL
                    JOIN extractions AS current_extraction
                      ON current_extraction.id = evidence.extraction_id
                     AND current_extraction.document_version_id = evidence.document_version_id
                     AND current_extraction.status = 'succeeded'
                    JOIN extraction_pages AS evidence_page
                      ON evidence_page.extraction_id = current_extraction.id
                     AND evidence_page.page_number = evidence.physical_page
                    LEFT JOIN terms_editions AS terms
                      ON terms.document_version_id = evidence.document_version_id
                     AND terms.household_space_id = evidence.household_space_id
                     AND terms.deleted_at IS NULL
                    LEFT JOIN LATERAL (
                      SELECT item.label, item.normalized_text
                      FROM clauses AS item
                      WHERE item.terms_edition_id = terms.id
                        AND item.household_space_id = evidence.household_space_id
                        AND item.deleted_at IS NULL
                        AND evidence.physical_page BETWEEN
                            item.physical_page_start AND item.physical_page_end
                      ORDER BY
                        item.physical_page_end - item.physical_page_start,
                        item.id
                      LIMIT 1
                    ) AS clause ON true
                    LEFT JOIN LATERAL (
                      SELECT string_agg(block.text, ' ' ORDER BY block.reading_order) AS text
                      FROM (
                        SELECT value.text, value.reading_order
                        FROM extraction_pages AS page
                        JOIN extraction_blocks AS value ON value.page_id = page.id
                        WHERE page.extraction_id = evidence.extraction_id
                          AND page.page_number = evidence.physical_page
                        ORDER BY value.reading_order
                        LIMIT 8
                      ) AS block
                    ) AS excerpt ON true
                    WHERE evidence.id = %s
                      AND evidence.household_space_id = %s
                      AND evidence.physical_page BETWEEN 1 AND version.page_count
                      AND (
                        evidence.x0 IS NULL OR (
                          evidence.x0 >= 0 AND evidence.y0 >= 0
                          AND evidence.x1 <= evidence_page.width_points
                          AND evidence.y1 <= evidence_page.height_points
                        )
                      )
                    LIMIT 1
                    """,
                    (evidence_id, scope.household_space_id),
                ).fetchone()
        except psycopg.Error:
            raise DecisionRepositoryUnavailable from None
        if row is None:
            raise EvidenceNotFound
        return _detail(row)


def _detail(row: Mapping[str, Any]) -> EvidenceDetail:
    try:
        excerpt = row["excerpt"]
        document_label = row["document_label"]
        page = row["physical_page"]
        review_state = row["review_state"]
        if (
            not isinstance(excerpt, str)
            or not isinstance(document_label, str)
            or not document_label.strip()
            or isinstance(page, bool)
            or not isinstance(page, int)
            or not 1 <= page <= 500
            or review_state not in {"AI_VERIFIED", "NEEDS_REVIEW", "USER_CONFIRMED"}
        ):
            raise ValueError
        normalized_excerpt = _WHITESPACE.sub(" ", excerpt).strip()[:480]
        if not normalized_excerpt:
            normalized_excerpt = "근거 발췌문을 사용할 수 없습니다."
        coordinates = tuple(row.get(name) for name in ("x0", "y0", "x1", "y1"))
        bbox = None
        if coordinates != (None, None, None, None):
            if any(value is None for value in coordinates):
                raise ValueError
            numeric_coordinates = cast(tuple[Any, Any, Any, Any], coordinates)
            bbox = cast(
                tuple[float, float, float, float],
                tuple(float(value) for value in numeric_coordinates),
            )
        clause_label = row.get("clause_label")
        if clause_label is not None and not isinstance(clause_label, str):
            raise ValueError
        return EvidenceDetail(
            evidence_id=cast(UUID, row["id"]),
            document_version_id=cast(UUID, row["document_version_id"]),
            document_label=document_label[:200],
            physical_page=page,
            clause_label=clause_label[:160] if clause_label else None,
            bounded_excerpt=normalized_excerpt,
            bbox=bbox,
            review_state=cast(Any, review_state),
        )
    except KeyError, TypeError, ValueError:
        raise DecisionRepositoryUnavailable from None


__all__ = ["EvidenceRepository"]
