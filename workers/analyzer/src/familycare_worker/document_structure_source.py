"""Read complete, scoped stored extraction layers without opening files or AI."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

import psycopg

from familycare_worker.document_layout import annotate_extraction_layout
from familycare_worker.document_structure import (
    DocumentStructure,
    DocumentStructureError,
    build_document_structure,
)


def load_stored_structure(
    connection: psycopg.Connection[dict[str, Any]],
    *,
    household_space_id: UUID,
    family_member_id: UUID,
    batch_item_id: UUID,
) -> DocumentStructure:
    source = connection.execute(
        """
        SELECT version.id AS document_version_id, version.content_sha256, version.page_count,
               extraction.id AS extraction_id, extraction.extractor_name,
               extraction.extractor_version, extraction.extractor_config_hash,
               extraction.quality_rule_version
        FROM document_batch_items item
        JOIN document_batches batch ON batch.id = item.batch_id
        JOIN family_members member ON member.id = batch.family_member_id
        JOIN LATERAL (
          SELECT version.* FROM document_versions version
          WHERE version.document_id = item.document_id
            AND (item.processed_document_version_id IS NULL
                 OR version.id = item.processed_document_version_id)
          ORDER BY version.version_number DESC, version.id LIMIT 1
        ) version ON TRUE
        JOIN LATERAL (
          SELECT extraction.* FROM extractions extraction
          WHERE extraction.document_version_id = version.id AND extraction.status = 'succeeded'
          ORDER BY extraction.succeeded_at DESC, extraction.id LIMIT 1
        ) extraction ON TRUE
        WHERE item.id = %s AND batch.household_space_id = %s
          AND batch.family_member_id = %s AND member.household_space_id = %s
          AND member.deleted_at IS NULL AND item.state = 'succeeded'
          AND EXISTS (SELECT 1 FROM evidence WHERE evidence.household_space_id = %s
                      AND evidence.document_version_id = version.id
                      AND evidence.extraction_id = extraction.id)
        FOR UPDATE OF item
        """,
        (
            batch_item_id,
            household_space_id,
            family_member_id,
            household_space_id,
            household_space_id,
        ),
    ).fetchone()
    if source is None:
        raise DocumentStructureError
    extraction_id = source["extraction_id"]
    page_rows = connection.execute(
        "SELECT * FROM extraction_pages WHERE extraction_id = %s ORDER BY page_number",
        (extraction_id,),
    ).fetchall()
    if len(page_rows) > 500:
        raise DocumentStructureError
    pages: dict[UUID, dict[str, Any]] = {}
    for page in page_rows:
        pages[page["id"]] = {
            "page_number": page["page_number"],
            "width_points": float(page["width_points"]),
            "height_points": float(page["height_points"]),
            "quality": {
                "classification": page["classification"],
                "non_whitespace_chars": page["non_whitespace_chars"],
                "alphanumeric_ratio": float(page["alphanumeric_ratio"]),
                "replacement_character_ratio": float(page["replacement_character_ratio"]),
                "maximum_repeated_character_run": page["maximum_repeated_character_run"],
            },
            "warning_codes": page["warning_codes"],
            "blocks": [],
            "tables": [],
        }
    block_rows = connection.execute(
        """
        SELECT block.page_id, block.text, block.bbox, block.reading_order
        FROM extraction_blocks block JOIN extraction_pages page ON page.id = block.page_id
        WHERE page.extraction_id = %s ORDER BY page.page_number, block.reading_order
        """,
        (extraction_id,),
    ).fetchall()
    for block in block_rows:
        pages[block["page_id"]]["blocks"].append(
            {key: block[key] for key in ("text", "bbox", "reading_order")}
        )
    table_rows = connection.execute(
        """
        SELECT table_row.id, table_row.page_id, table_row.bbox,
               table_row.metadata_json, table_row.review_state
        FROM extraction_tables table_row JOIN extraction_pages page ON page.id = table_row.page_id
        WHERE page.extraction_id = %s
        ORDER BY page.page_number, (table_row.bbox->>1)::numeric,
                 (table_row.bbox->>0)::numeric, table_row.id
        """,
        (extraction_id,),
    ).fetchall()
    tables: dict[UUID, dict[str, Any]] = {}
    for table in table_rows:
        value: dict[str, Any] = {
            "bbox": table["bbox"],
            "metadata_json": table["metadata_json"],
            "review_state": table["review_state"],
            "cells": [],
        }
        tables[table["id"]] = value
        pages[table["page_id"]]["tables"].append(value)
    if tables:
        cells = connection.execute(
            """
            SELECT table_id, row_index, column_index, text, bbox, review_state
            FROM extraction_cells WHERE table_id = ANY(%s)
            ORDER BY table_id, row_index, column_index
            """,
            (list(tables),),
        ).fetchall()
        for cell in cells:
            tables[cell["table_id"]]["cells"].append(
                {key: value for key, value in cell.items() if key != "table_id"}
            )
    native: dict[str, object] = {
        "schema_version": "1",
        "document_version_id": str(source["document_version_id"]),
        "content_sha256": source["content_sha256"],
        "page_count": source["page_count"],
        "extractor_name": source["extractor_name"],
        "extractor_version": source["extractor_version"],
        "extractor_config_hash": source["extractor_config_hash"],
        "quality_rule_version": source["quality_rule_version"],
        "pages": list(pages.values()),
    }
    layer = connection.execute(
        """
        SELECT * FROM ocr_layers WHERE extraction_id = %s AND status = 'succeeded'
        ORDER BY created_at DESC, id DESC LIMIT 1
        """,
        (extraction_id,),
    ).fetchone()
    ocr_pages: list[dict[str, Any]] = []
    ocr_revision = None
    if layer is not None:
        ocr_revision = (
            "ocr-v1:"
            + hashlib.sha256(
                json.dumps(
                    [
                        str(layer["id"]),
                        layer["engine_name"],
                        layer["engine_version"],
                        layer["language_config_hash"],
                        layer["quality_rule_version"],
                    ]
                ).encode()
            ).hexdigest()
        )
        page_results = connection.execute(
            "SELECT * FROM ocr_pages WHERE ocr_layer_id = %s ORDER BY page_number",
            (layer["id"],),
        ).fetchall()
        for page in page_results:
            blocks = connection.execute(
                "SELECT text, bbox, reading_order, confidence FROM ocr_blocks "
                "WHERE ocr_page_id = %s ORDER BY reading_order",
                (page["id"],),
            ).fetchall()
            ocr_pages.append(
                {
                    "document_version_id": str(page["document_version_id"]),
                    "content_sha256": page["content_sha256"],
                    "evidence": {
                        "document_version_id": str(page["document_version_id"]),
                        "content_sha256": page["content_sha256"],
                        "page_number": page["page_number"],
                    },
                    "page_number": page["page_number"],
                    "status": page["status"],
                    "warning_codes": page["warning_codes"],
                    "blocks": [
                        {**block, "confidence": float(block["confidence"])} for block in blocks
                    ],
                    "tables": [],
                }
            )
    return build_document_structure(
        annotate_extraction_layout(native),
        extraction_id=extraction_id,
        extraction_revision="native-v1:"
        + hashlib.sha256(
            json.dumps(
                [
                    source["extractor_name"],
                    source["extractor_version"],
                    source["extractor_config_hash"],
                    source["quality_rule_version"],
                    "layout-relations-v1",
                ]
            ).encode()
        ).hexdigest(),
        document_version_id=source["document_version_id"],
        ocr_pages=ocr_pages,
        ocr_revision=ocr_revision,
    )
