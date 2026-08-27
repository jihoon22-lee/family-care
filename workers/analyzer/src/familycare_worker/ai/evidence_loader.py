"""Load bounded page Evidence text for policy candidate structuring."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from familycare_worker.ai.provider import EvidenceSlice
from familycare_worker.jobs import psycopg_database_url

_MAX_EVIDENCE_ROWS = 500
_MAX_EVIDENCE_SLICES = 64
_MAX_EVIDENCE_TEXT = 240
_ROW_KEYS = frozenset(
    {
        "document_kind",
        "document_version_id",
        "evidence_id",
        "evidence_text",
        "physical_page",
    }
)


class EvidenceLoadError(RuntimeError):
    """Fixed-message failure that never contains Evidence text or identifiers."""

    def __init__(self) -> None:
        super().__init__("EVIDENCE_LOAD_ERROR")


def _normalize_text(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise EvidenceLoadError
    return " ".join(value.split())[:_MAX_EVIDENCE_TEXT]


def _to_slices(
    rows: Sequence[Mapping[str, object]],
    *,
    expected_document_version_id: UUID,
) -> tuple[EvidenceSlice, ...]:
    """Validate ordered page rows and retain at most 64 provider-safe slices."""

    if (
        not isinstance(expected_document_version_id, UUID)
        or expected_document_version_id.int == 0
        or len(rows) > _MAX_EVIDENCE_ROWS
    ):
        raise EvidenceLoadError
    result: list[EvidenceSlice] = []
    evidence_ids: set[UUID] = set()
    previous_page = 0
    for row in rows:
        if set(row) != _ROW_KEYS:
            raise EvidenceLoadError
        evidence_id = row["evidence_id"]
        document_version_id = row["document_version_id"]
        physical_page = row["physical_page"]
        document_kind = row["document_kind"]
        if (
            not isinstance(evidence_id, UUID)
            or evidence_id.int == 0
            or evidence_id in evidence_ids
            or document_version_id != expected_document_version_id
            or isinstance(physical_page, bool)
            or not isinstance(physical_page, int)
            or not previous_page < physical_page <= 500
            or document_kind not in {"policy", "terms"}
        ):
            raise EvidenceLoadError
        evidence_ids.add(evidence_id)
        previous_page = physical_page
        text = _normalize_text(row["evidence_text"])
        if not text or len(result) == _MAX_EVIDENCE_SLICES:
            continue
        try:
            result.append(
                EvidenceSlice(
                    evidence_id=evidence_id,
                    document_version_id=expected_document_version_id,
                    page=physical_page,
                    text=text,
                    bbox=None,
                    document_kind=str(document_kind),
                )
            )
        except ValueError:
            raise EvidenceLoadError from None
    return tuple(result)


class PolicyEvidenceLoader:
    """Resolve scoped page Evidence while preferring OCR only where required."""

    def __init__(self, database_url: str) -> None:
        self.database_url = psycopg_database_url(database_url)

    def load(
        self,
        *,
        household_space_id: UUID,
        document_version_id: UUID,
        extraction_id: UUID,
    ) -> tuple[EvidenceSlice, ...]:
        if any(
            not isinstance(value, UUID) or value.int == 0
            for value in (household_space_id, document_version_id, extraction_id)
        ):
            raise EvidenceLoadError
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                rows = connection.execute(
                    """
                    SELECT evidence.id AS evidence_id,
                           evidence.document_version_id,
                           evidence.physical_page,
                           document.document_kind,
                           CASE
                               WHEN page.classification = 'OCR_REQUIRED'
                               THEN COALESCE(NULLIF(ocr.text, ''), native.text)
                               ELSE native.text
                           END AS evidence_text
                    FROM evidence
                    JOIN document_versions AS version
                      ON version.id = evidence.document_version_id
                     AND version.content_sha256 = evidence.content_sha256
                    JOIN documents AS document ON document.id = version.document_id
                    JOIN extractions AS extraction
                      ON extraction.id = evidence.extraction_id
                     AND extraction.document_version_id = evidence.document_version_id
                    JOIN extraction_pages AS page
                      ON page.extraction_id = extraction.id
                     AND page.page_number = evidence.physical_page
                    LEFT JOIN LATERAL (
                        SELECT left(
                            btrim(string_agg(block.text, ' ' ORDER BY block.reading_order)),
                            240
                        ) AS text
                        FROM (
                            SELECT reading_order,
                                   left(
                                       regexp_replace(text, '[[:space:]]+', ' ', 'g'),
                                       240
                                   ) AS text
                            FROM extraction_blocks
                            WHERE page_id = page.id
                            ORDER BY reading_order
                            LIMIT 64
                        ) AS block
                    ) AS native ON TRUE
                    LEFT JOIN LATERAL (
                        SELECT left(
                            btrim(string_agg(block.text, ' ' ORDER BY block.reading_order)),
                            240
                        ) AS text
                        FROM (
                            SELECT ocr_block.reading_order,
                                   left(
                                       regexp_replace(
                                           ocr_block.text,
                                           '[[:space:]]+',
                                           ' ',
                                           'g'
                                       ),
                                       240
                                   ) AS text
                            FROM ocr_layers AS layer
                            JOIN ocr_pages AS ocr_page
                              ON ocr_page.ocr_layer_id = layer.id
                            JOIN ocr_blocks AS ocr_block
                              ON ocr_block.ocr_page_id = ocr_page.id
                            WHERE layer.extraction_id = extraction.id
                              AND layer.status = 'succeeded'
                              AND ocr_page.document_version_id = evidence.document_version_id
                              AND ocr_page.page_number = evidence.physical_page
                              AND ocr_page.status IN ('completed', 'warning')
                            ORDER BY ocr_block.reading_order
                            LIMIT 64
                        ) AS block
                    ) AS ocr ON TRUE
                    WHERE evidence.household_space_id = %s
                      AND evidence.document_version_id = %s
                      AND evidence.extraction_id = %s
                      AND evidence.review_state = 'NEEDS_REVIEW'
                      AND evidence.x0 IS NULL
                      AND evidence.y0 IS NULL
                      AND evidence.x1 IS NULL
                      AND evidence.y1 IS NULL
                      AND extraction.status = 'succeeded'
                      AND document.document_kind IN ('policy', 'terms')
                      AND document.deleted_at IS NULL
                    ORDER BY evidence.physical_page, evidence.id
                    LIMIT 500
                    """,
                    (household_space_id, document_version_id, extraction_id),
                ).fetchall()
        except psycopg.Error:
            raise EvidenceLoadError from None
        return _to_slices(
            rows,
            expected_document_version_id=document_version_id,
        )


__all__ = ["EvidenceLoadError", "PolicyEvidenceLoader"]
