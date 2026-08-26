"""Map validated internal OCR models to the strict versioned contract."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from .models import SelectiveOcrResult


def to_contract(result: SelectiveOcrResult, extraction_id: UUID) -> dict[str, Any]:
    """Return path-free OCR evidence associated with one native extraction."""

    return {
        "schema_version": "1",
        "extraction_id": str(extraction_id),
        "source_layer": "ocr",
        "engine_name": result.engine_name,
        "engine_version": result.engine_version,
        "language_codes": list(result.language_codes),
        "quality_rule_version": result.quality_rule_version,
        "pages": [
            {
                "page_number": page.page_number,
                "rendered_dpi": page.rendered_dpi,
                "image_width_pixels": page.image_width_pixels,
                "image_height_pixels": page.image_height_pixels,
                "selected_classification": "OCR_REQUIRED",
                "status": page.status,
                "warning_codes": list(page.warning_codes),
                "blocks": [
                    {
                        "text": block.text,
                        "bbox": list(block.bbox),
                        "reading_order": block.reading_order,
                        "confidence": block.confidence,
                        "source_layer": block.source_layer,
                        "review_state": block.review_state,
                    }
                    for block in page.blocks
                ],
                "evidence": {
                    "document_version_id": str(result.document_version_id),
                    "page_number": page.page_number,
                    "content_sha256": result.content_sha256,
                    "source_layer": "ocr",
                },
            }
            for page in result.pages
        ],
        "warning_codes": list(result.warning_codes),
    }


__all__ = ["to_contract"]
