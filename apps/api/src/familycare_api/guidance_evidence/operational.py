"""Disclose bounded retained extraction content, never editable Clause prose."""

import math
from typing import Any

import psycopg

from familycare_api.common.scope import HouseholdScope
from familycare_api.guidance.models import GuidanceEvidence
from familycare_api.guidance_evidence.models import (
    GuidanceEvidenceDetail,
    bounded_text,
    unavailable,
)


def _box(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        return None
    try:
        box = tuple(float(n) for n in value)
    except TypeError, ValueError, OverflowError:
        return None
    if any(not math.isfinite(n) or n < 0 for n in box) or box[0] > box[2] or box[1] > box[3]:
        return None
    return box[0], box[1], box[2], box[3]


def read_operational_evidence(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    reference: GuidanceEvidence,
) -> GuidanceEvidenceDetail:
    row = connection.execute(
        "SELECT e.id,e.document_version_id,e.extraction_id,e.physical_page,e.content_sha256,"
        "e.x0,e.y0,e.x1,e.y1,p.id AS page_id,p.width_points,p.height_points,d.document_kind "
        "FROM evidence e JOIN document_versions v ON v.id=e.document_version_id "
        "AND v.content_sha256=e.content_sha256 JOIN documents d ON d.id=v.document_id "
        "AND d.deleted_at IS NULL JOIN extractions x ON x.id=e.extraction_id "
        "AND x.document_version_id=v.id AND x.status='succeeded' "
        "JOIN extraction_pages p ON p.extraction_id=x.id AND p.page_number=e.physical_page "
        "WHERE e.id=%s AND e.household_space_id=%s AND e.physical_page BETWEEN 1 AND v.page_count",
        (reference.evidence_id, scope.household_space_id),
    ).fetchone()
    if (
        row is None
        or reference.page_start != row["physical_page"]
        or reference.page_end != row["physical_page"]
        or (
            reference.source_sha256 is not None and reference.source_sha256 != row["content_sha256"]
        )
    ):
        return unavailable(reference)
    raw_box = tuple(row[key] for key in ("x0", "y0", "x1", "y1"))
    box = None if raw_box == (None, None, None, None) else _box(raw_box)
    if raw_box != (None, None, None, None) and (
        box is None or box[2] > row["width_points"] or box[3] > row["height_points"]
    ):
        return unavailable(reference, "EVIDENCE_SOURCE_ADDRESS_UNAVAILABLE")
    # Extraction content is immutable source data. Clause.normalized_text is editable
    # and cannot be presented as an original quote merely because its page overlaps.
    records = connection.execute(
        "SELECT substring(item.text FROM 1 FOR 2049) AS text, "
        "char_length(item.text)>2048 AS clipped "
        "FROM (SELECT b.text,b.bbox,0 AS source_order,b.reading_order AS row_order,"
        "0 AS column_order "
        "FROM extraction_blocks b WHERE b.page_id=%(page)s UNION ALL "
        "SELECT c.text,c.bbox,1,c.row_index,c.column_index FROM extraction_cells c "
        "JOIN extraction_tables t ON t.id=c.table_id WHERE t.page_id=%(page)s) item "
        "WHERE %(x0)s::numeric IS NULL OR (jsonb_typeof(item.bbox)='array' "
        "AND jsonb_array_length(item.bbox)=4 AND (item.bbox->>0)::numeric<=%(x1)s "
        "AND (item.bbox->>2)::numeric>=%(x0)s AND (item.bbox->>1)::numeric<=%(y1)s "
        "AND (item.bbox->>3)::numeric>=%(y0)s) "
        "ORDER BY item.source_order,item.row_order,item.column_order LIMIT 33",
        {
            "page": row["page_id"],
            "x0": box[0] if box else None,
            "y0": box[1] if box else None,
            "x1": box[2] if box else None,
            "y1": box[3] if box else None,
        },
    ).fetchall()
    text = "\n".join(record["text"] for record in records[:32] if record["text"].strip())
    if not text:
        return unavailable(reference)
    shown, clipped = bounded_text(text)
    labels = {"policy": "증권 문서", "terms": "약관 문서", "amendment": "계약 변경 문서"}
    return GuidanceEvidenceDetail(
        evidence=reference,
        content_kind="ORIGINAL",
        document_label=labels.get(row["document_kind"], "보험 근거 문서"),
        document_version_id=row["document_version_id"],
        page_start=reference.page_start,
        page_end=reference.page_end,
        text=shown,
        truncated=clipped or len(records) > 32 or any(record["clipped"] for record in records[:32]),
        bbox=box,
        reason_codes=("EVIDENCE_RETAINED_EXTRACTION",),
    )
