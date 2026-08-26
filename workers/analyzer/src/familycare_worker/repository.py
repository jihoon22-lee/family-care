"""Transactional persistence for validated Phase 1 extraction results."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from familycare_worker.archive.crypto import ArchiveMetadata
from familycare_worker.generated_contracts import ExtractionResult
from familycare_worker.jobs import (
    AnalysisJobRecord,
    JobNotFound,
    JobStateConflict,
    psycopg_database_url,
)
from familycare_worker.ocr.models import SelectiveOcrResult
from familycare_worker.pdf.intake import ValidatedPdf

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_BATCH_EXTRACTOR_CONFIG_HASH = hashlib.sha256(
    b'{"profile":"quality-v1","quality_rule_version":"quality-v1","table_strategy":"auto"}'
).hexdigest()
_REVIEW_STATES = frozenset({"candidate", "confirmed", "rejected"})
_OCR_WARNING_CODES = frozenset({"LOW_CONFIDENCE", "NO_TEXT_DETECTED"})
_TOP_LEVEL_KEYS = frozenset(
    {
        "content_sha256",
        "evidence",
        "extractor_config_hash",
        "extractor_name",
        "extractor_version",
        "pages",
        "quality_rule_version",
        "schema_version",
    }
)


class RepositoryError(RuntimeError):
    """Base repository error with a non-sensitive fixed message."""


class InvalidExtractionResult(RepositoryError):
    def __init__(self) -> None:
        super().__init__("INVALID_EXTRACTION_RESULT")


class DocumentStateConflict(RepositoryError):
    def __init__(self) -> None:
        super().__init__("DOCUMENT_STATE_CONFLICT")


@dataclass(frozen=True)
class BatchItemRecord:
    id: UUID
    batch_id: UUID
    source_id: str
    source_key: str
    document_id: UUID
    document_version_id: UUID
    state: str
    attempts: int
    max_attempts: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    error_code: str | None


def _exact_mapping(value: object, keys: set[str] | frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise InvalidExtractionResult
    return cast(Mapping[str, Any], value)


def _integer(value: object, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise InvalidExtractionResult
    return value


def _finite_number(
    value: object,
    *,
    minimum: float = 0.0,
    maximum: float = 1_000_000.0,
    exclusive_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidExtractionResult
    number = float(value)
    if not math.isfinite(number) or number > maximum:
        raise InvalidExtractionResult
    if (exclusive_minimum and number <= minimum) or (not exclusive_minimum and number < minimum):
        raise InvalidExtractionResult
    return number


def _bbox(value: object, *, width: float, height: float) -> list[float]:
    if not isinstance(value, list) or len(value) != 4:
        raise InvalidExtractionResult
    result = [_finite_number(item) for item in value]
    x0, top, x1, bottom = result
    if x0 > x1 or top > bottom or x1 > width or bottom > height:
        raise InvalidExtractionResult
    if any(abs(item - round(item, 3)) > 1e-9 for item in result):
        raise InvalidExtractionResult
    return result


def _validate_result(
    value: object,
    *,
    document_version_id: UUID,
    expected_content_sha256: str,
    expected_config_hash: str,
) -> ExtractionResult:
    result = _exact_mapping(value, _TOP_LEVEL_KEYS)
    if (
        result["schema_version"] != "1"
        or result["quality_rule_version"] != "quality-v1"
        or result["content_sha256"] != expected_content_sha256
        or result["extractor_config_hash"] != expected_config_hash
        or _SHA256_PATTERN.fullmatch(str(result["content_sha256"])) is None
        or _SHA256_PATTERN.fullmatch(str(result["extractor_config_hash"])) is None
        or not isinstance(result["extractor_name"], str)
        or not 1 <= len(result["extractor_name"]) <= 128
        or not isinstance(result["extractor_version"], str)
        or not 1 <= len(result["extractor_version"]) <= 64
    ):
        raise InvalidExtractionResult

    pages = result["pages"]
    if not isinstance(pages, list) or not 1 <= len(pages) <= 500:
        raise InvalidExtractionResult
    page_dimensions: dict[int, tuple[float, float]] = {}
    for expected_page_number, raw_page in enumerate(pages, start=1):
        page = _exact_mapping(
            raw_page,
            {
                "blocks",
                "height_points",
                "page_number",
                "quality",
                "tables",
                "warning_codes",
                "width_points",
            },
        )
        page_number = _integer(page["page_number"], minimum=1)
        if page_number != expected_page_number:
            raise InvalidExtractionResult
        width = _finite_number(page["width_points"], exclusive_minimum=True)
        height = _finite_number(page["height_points"], exclusive_minimum=True)
        page_dimensions[page_number] = (width, height)

        quality = _exact_mapping(
            page["quality"],
            {
                "alphanumeric_ratio",
                "classification",
                "maximum_repeated_character_run",
                "non_whitespace_chars",
                "replacement_character_ratio",
                "rule_version",
            },
        )
        non_whitespace = _integer(quality["non_whitespace_chars"])
        alphanumeric_ratio = _finite_number(quality["alphanumeric_ratio"], maximum=1.0)
        replacement_ratio = _finite_number(quality["replacement_character_ratio"], maximum=1.0)
        maximum_run = _integer(quality["maximum_repeated_character_run"])
        expected_classification = (
            "OCR_REQUIRED"
            if non_whitespace < 20
            or alphanumeric_ratio < 0.25
            or replacement_ratio > 0.05
            or maximum_run > 20
            else "TEXT_SUFFICIENT"
        )
        if (
            quality["rule_version"] != "quality-v1"
            or quality["classification"] != expected_classification
        ):
            raise InvalidExtractionResult

        warnings = page["warning_codes"]
        if (
            not isinstance(warnings, list)
            or len(warnings) > 100
            or any(not isinstance(item, str) or len(item) > 64 for item in warnings)
        ):
            raise InvalidExtractionResult
        expected_warnings = ["OCR_REQUIRED"] if expected_classification == "OCR_REQUIRED" else []
        if warnings != expected_warnings:
            raise InvalidExtractionResult

        blocks = page["blocks"]
        if not isinstance(blocks, list):
            raise InvalidExtractionResult
        for reading_order, raw_block in enumerate(blocks):
            block = _exact_mapping(
                raw_block,
                {"bbox", "page_number", "reading_order", "text"},
            )
            if (
                _integer(block["page_number"], minimum=1) != page_number
                or _integer(block["reading_order"]) != reading_order
                or not isinstance(block["text"], str)
            ):
                raise InvalidExtractionResult
            _bbox(block["bbox"], width=width, height=height)

        tables = page["tables"]
        if not isinstance(tables, list):
            raise InvalidExtractionResult
        for raw_table in tables:
            table = _exact_mapping(raw_table, {"bbox", "cells", "review_state"})
            if table["review_state"] not in _REVIEW_STATES:
                raise InvalidExtractionResult
            _bbox(table["bbox"], width=width, height=height)
            cells = table["cells"]
            if not isinstance(cells, list):
                raise InvalidExtractionResult
            coordinates: set[tuple[int, int]] = set()
            for raw_cell in cells:
                cell = _exact_mapping(
                    raw_cell,
                    {"bbox", "column_index", "review_state", "row_index", "text"},
                )
                coordinate = (
                    _integer(cell["row_index"]),
                    _integer(cell["column_index"]),
                )
                if (
                    coordinate in coordinates
                    or cell["review_state"] not in _REVIEW_STATES
                    or not isinstance(cell["text"], str)
                ):
                    raise InvalidExtractionResult
                coordinates.add(coordinate)
                _bbox(cell["bbox"], width=width, height=height)

    evidence = result["evidence"]
    if not isinstance(evidence, list) or not evidence:
        raise InvalidExtractionResult
    for raw_evidence in evidence:
        if not isinstance(raw_evidence, Mapping):
            raise InvalidExtractionResult
        allowed_keys = {"content_sha256", "document_version_id", "page_number", "review_state"}
        if "bbox" in raw_evidence:
            allowed_keys.add("bbox")
        item = _exact_mapping(raw_evidence, allowed_keys)
        page_number = _integer(item["page_number"], minimum=1)
        if (
            item["document_version_id"] != str(document_version_id)
            or item["content_sha256"] != expected_content_sha256
            or item["review_state"] not in _REVIEW_STATES
            or page_number not in page_dimensions
        ):
            raise InvalidExtractionResult
        if "bbox" in item:
            width, height = page_dimensions[page_number]
            _bbox(item["bbox"], width=width, height=height)
    return cast(ExtractionResult, result)


def _ocr_config_hash(engine_version: str) -> str:
    canonical = json.dumps(
        {
            "dpi": 300,
            "engine_name": "tesseract",
            "engine_version": engine_version,
            "language_codes": ["kor", "eng"],
            "quality_rule_version": "quality-v1",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _validate_ocr_result(
    value: SelectiveOcrResult | None,
    *,
    native: ExtractionResult,
    document_version_id: UUID,
    content_sha256: str,
) -> SelectiveOcrResult | None:
    selected: list[tuple[int, float, float]] = []
    for raw_page in native["pages"]:
        page = cast(Mapping[str, Any], raw_page)
        quality = cast(Mapping[str, Any], page["quality"])
        if quality["classification"] == "OCR_REQUIRED":
            selected.append(
                (
                    cast(int, page["page_number"]),
                    float(cast(float, page["width_points"])),
                    float(cast(float, page["height_points"])),
                )
            )
    if not selected:
        if value is not None:
            raise InvalidExtractionResult
        return None
    if not isinstance(value, SelectiveOcrResult):
        raise InvalidExtractionResult
    if (
        value.document_version_id != document_version_id
        or value.content_sha256 != content_sha256
        or value.engine_name != "tesseract"
        or not 1 <= len(value.engine_version) <= 64
        or value.language_codes != ("kor", "eng")
        or value.language_config_hash != _ocr_config_hash(value.engine_version)
        or value.quality_rule_version != "quality-v1"
        or len(value.pages) != len(selected)
        or len(set(value.warning_codes)) != len(value.warning_codes)
        or any(code not in _OCR_WARNING_CODES for code in value.warning_codes)
    ):
        raise InvalidExtractionResult
    aggregate_warnings: list[str] = []
    for ocr_page, (page_number, page_width, page_height) in zip(value.pages, selected, strict=True):
        if (
            ocr_page.page_number != page_number
            or ocr_page.rendered_dpi != 300
            or not 1 <= ocr_page.image_width_pixels <= 20_000
            or not 1 <= ocr_page.image_height_pixels <= 20_000
            or ocr_page.image_width_pixels * ocr_page.image_height_pixels > 25_000_000
            or len(set(ocr_page.warning_codes)) != len(ocr_page.warning_codes)
            or any(code not in _OCR_WARNING_CODES for code in ocr_page.warning_codes)
            or ocr_page.status != ("warning" if ocr_page.warning_codes else "completed")
        ):
            raise InvalidExtractionResult
        for code in ocr_page.warning_codes:
            if code not in aggregate_warnings:
                aggregate_warnings.append(code)
        for expected_order, block in enumerate(ocr_page.blocks):
            if (
                block.reading_order != expected_order
                or not 1 <= len(block.text) <= 8192
                or block.source_layer != "ocr"
                or block.review_state != "candidate"
                or not math.isfinite(block.confidence)
                or not 0 <= block.confidence <= 100
            ):
                raise InvalidExtractionResult
            _bbox(list(block.bbox), width=page_width, height=page_height)
    if tuple(aggregate_warnings) != value.warning_codes:
        raise InvalidExtractionResult
    return value


def _persist_ocr(
    connection: Connection[dict[str, Any]],
    *,
    extraction_id: UUID,
    result: SelectiveOcrResult,
) -> None:
    layer = connection.execute(
        """
        INSERT INTO ocr_layers (
            extraction_id, source_layer, engine_name, engine_version,
            language_config_hash, quality_rule_version, status, warning_codes
        )
        VALUES (%s, 'ocr', %s, %s, %s, %s, 'succeeded', %s)
        RETURNING id
        """,
        (
            extraction_id,
            result.engine_name,
            result.engine_version,
            result.language_config_hash,
            result.quality_rule_version,
            Jsonb(list(result.warning_codes)),
        ),
    ).fetchone()
    if layer is None:
        raise DocumentStateConflict
    layer_id = cast(UUID, layer["id"])
    for page in result.pages:
        page_row = connection.execute(
            """
            INSERT INTO ocr_pages (
                ocr_layer_id, document_version_id, content_sha256, page_number,
                rendered_dpi, image_width_pixels, image_height_pixels,
                selected_classification, status, warning_codes
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'OCR_REQUIRED', %s, %s)
            RETURNING id
            """,
            (
                layer_id,
                result.document_version_id,
                result.content_sha256,
                page.page_number,
                page.rendered_dpi,
                page.image_width_pixels,
                page.image_height_pixels,
                page.status,
                Jsonb(list(page.warning_codes)),
            ),
        ).fetchone()
        if page_row is None:
            raise DocumentStateConflict
        page_id = cast(UUID, page_row["id"])
        for block in page.blocks:
            connection.execute(
                """
                INSERT INTO ocr_blocks (
                    ocr_page_id, text, bbox, reading_order, confidence,
                    source_layer, review_state
                )
                VALUES (%s, %s, %s, %s, %s, 'ocr', 'candidate')
                """,
                (
                    page_id,
                    block.text,
                    Jsonb(list(block.bbox)),
                    block.reading_order,
                    block.confidence,
                ),
            )


def _lock_owned_job(
    connection: Connection[dict[str, Any]],
    job_id: UUID,
    worker_id: str,
) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT *, lease_expires_at > clock_timestamp() AS lease_valid
        FROM analysis_jobs
        WHERE id = %s
        FOR UPDATE
        """,
        (job_id,),
    ).fetchone()
    if row is None:
        raise JobNotFound
    if row["state"] != "running" or row["lease_owner"] != worker_id or not row["lease_valid"]:
        raise JobStateConflict
    return row


class ExtractionRepository:
    """Persist content identity and one atomic successful extraction."""

    def __init__(self, database_url: str):
        self.database_url = psycopg_database_url(database_url)

    def prepare_document_version(
        self,
        job: AnalysisJobRecord,
        worker_id: str,
        validated: ValidatedPdf,
    ) -> UUID:
        """Create or reuse the intake identity before the parser needs its UUID."""

        if (
            _SHA256_PATTERN.fullmatch(validated.content_sha256) is None
            or validated.byte_size < 0
            or validated.page_count < 1
            or validated.media_type != "application/pdf"
            or validated.encrypted
        ):
            raise DocumentStateConflict
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            job_row = _lock_owned_job(connection, job.id, worker_id)
            document = connection.execute(
                "SELECT * FROM documents WHERE id = %s FOR UPDATE",
                (job.document_id,),
            ).fetchone()
            if (
                document is None
                or document["deleted_at"] is not None
                or document["source_key"] != job.source_key
                or document["document_kind"] != job.settings["document_kind"]
                or job_row["document_id"] != job.document_id
            ):
                raise DocumentStateConflict
            existing = connection.execute(
                """
                SELECT id, byte_size, page_count
                FROM document_versions
                WHERE document_id = %s AND content_sha256 = %s
                """,
                (job.document_id, validated.content_sha256),
            ).fetchone()
            if existing is not None and (
                existing["byte_size"] != validated.byte_size
                or existing["page_count"] != validated.page_count
            ):
                raise DocumentStateConflict
            if existing is None:
                next_version = connection.execute(
                    """
                    SELECT COALESCE(MAX(version_number), 0) + 1 AS version_number
                    FROM document_versions
                    WHERE document_id = %s
                    """,
                    (job.document_id,),
                ).fetchone()
                if next_version is None:
                    raise DocumentStateConflict
                existing = connection.execute(
                    """
                    INSERT INTO document_versions (
                        document_id,
                        version_number,
                        content_sha256,
                        byte_size,
                        page_count
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        job.document_id,
                        next_version["version_number"],
                        validated.content_sha256,
                        validated.byte_size,
                        validated.page_count,
                    ),
                ).fetchone()
                if existing is None:
                    raise DocumentStateConflict
            connection.execute(
                """
                UPDATE documents
                SET media_type = %s,
                    byte_size = %s,
                    page_count = %s,
                    status = 'ready',
                    updated_at = clock_timestamp()
                WHERE id = %s
                """,
                (
                    validated.media_type,
                    validated.byte_size,
                    validated.page_count,
                    job.document_id,
                ),
            )
            return cast(UUID, existing["id"])

    def find_succeeded_extraction(
        self,
        document_version_id: UUID,
        extractor_config_hash: str,
    ) -> UUID | None:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT id
                FROM extractions
                WHERE document_version_id = %s
                  AND extractor_config_hash = %s
                  AND status = 'succeeded'
                  AND (
                    NOT EXISTS (
                      SELECT 1
                      FROM extraction_pages AS page
                      WHERE page.extraction_id = extractions.id
                        AND page.classification = 'OCR_REQUIRED'
                    )
                    OR EXISTS (
                      SELECT 1
                      FROM ocr_layers AS layer
                      WHERE layer.extraction_id = extractions.id
                        AND layer.status = 'succeeded'
                    )
                  )
                """,
                (document_version_id, extractor_config_hash),
            ).fetchone()
        return cast(UUID, row["id"]) if row is not None else None

    def complete_with_existing(
        self,
        job: AnalysisJobRecord,
        worker_id: str,
        extraction_id: UUID,
    ) -> None:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            _lock_owned_job(connection, job.id, worker_id)
            existing = connection.execute(
                """
                SELECT extraction.id
                FROM extractions AS extraction
                JOIN document_versions AS version
                  ON version.id = extraction.document_version_id
                WHERE extraction.id = %s
                  AND extraction.status = 'succeeded'
                  AND version.document_id = %s
                  AND extraction.extractor_config_hash = %s
                """,
                (extraction_id, job.document_id, job.extractor_config_hash),
            ).fetchone()
            if existing is None:
                raise DocumentStateConflict
            self._mark_job_succeeded(connection, job.id, worker_id)

    def persist_success(
        self,
        job: AnalysisJobRecord,
        worker_id: str,
        document_version_id: UUID,
        raw_result: object,
        *,
        ocr: SelectiveOcrResult | None = None,
        ocr_attempted: bool = False,
    ) -> UUID:
        """Validate child JSON and atomically store or reuse one success."""

        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            _lock_owned_job(connection, job.id, worker_id)
            version = connection.execute(
                """
                SELECT id, content_sha256
                FROM document_versions
                WHERE id = %s AND document_id = %s
                FOR UPDATE
                """,
                (document_version_id, job.document_id),
            ).fetchone()
            if version is None:
                raise DocumentStateConflict
            result = _validate_result(
                raw_result,
                document_version_id=document_version_id,
                expected_content_sha256=version["content_sha256"],
                expected_config_hash=job.extractor_config_hash,
            )
            ocr_result = (
                _validate_ocr_result(
                    ocr,
                    native=result,
                    document_version_id=document_version_id,
                    content_sha256=version["content_sha256"],
                )
                if ocr_attempted
                else None
            )
            existing = connection.execute(
                """
                SELECT id
                FROM extractions
                WHERE document_version_id = %s
                  AND extractor_config_hash = %s
                  AND status = 'succeeded'
                """,
                (document_version_id, job.extractor_config_hash),
            ).fetchone()
            if existing is not None:
                extraction_id = cast(UUID, existing["id"])
                if ocr_result is not None:
                    persisted_layer = connection.execute(
                        """
                        SELECT 1
                        FROM ocr_layers
                        WHERE extraction_id = %s AND status = 'succeeded'
                        """,
                        (extraction_id,),
                    ).fetchone()
                    if persisted_layer is None:
                        _persist_ocr(
                            connection,
                            extraction_id=extraction_id,
                            result=ocr_result,
                        )
                self._mark_job_succeeded(connection, job.id, worker_id)
                return extraction_id

            extraction = connection.execute(
                """
                INSERT INTO extractions (
                    document_version_id,
                    extractor_name,
                    extractor_version,
                    extractor_config_hash,
                    quality_rule_version,
                    status
                )
                VALUES (%s, %s, %s, %s, %s, 'running')
                RETURNING id
                """,
                (
                    document_version_id,
                    result["extractor_name"],
                    result["extractor_version"],
                    result["extractor_config_hash"],
                    result["quality_rule_version"],
                ),
            ).fetchone()
            if extraction is None:
                raise RepositoryError("EXTRACTION_INSERT_FAILED")
            extraction_id = cast(UUID, extraction["id"])
            for page in result["pages"]:
                quality = page["quality"]
                page_row = connection.execute(
                    """
                    INSERT INTO extraction_pages (
                        extraction_id,
                        page_number,
                        width_points,
                        height_points,
                        non_whitespace_chars,
                        alphanumeric_ratio,
                        replacement_character_ratio,
                        maximum_repeated_character_run,
                        classification,
                        warning_codes
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        extraction_id,
                        page["page_number"],
                        page["width_points"],
                        page["height_points"],
                        quality["non_whitespace_chars"],
                        quality["alphanumeric_ratio"],
                        quality["replacement_character_ratio"],
                        quality["maximum_repeated_character_run"],
                        quality["classification"],
                        Jsonb(page["warning_codes"]),
                    ),
                ).fetchone()
                if page_row is None:
                    raise RepositoryError("EXTRACTION_PAGE_INSERT_FAILED")
                page_id = cast(UUID, page_row["id"])
                for block in page["blocks"]:
                    connection.execute(
                        """
                        INSERT INTO extraction_blocks (page_id, text, bbox, reading_order)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (
                            page_id,
                            block["text"],
                            Jsonb(block["bbox"]),
                            block["reading_order"],
                        ),
                    )
                for table in page["tables"]:
                    table_row = connection.execute(
                        """
                        INSERT INTO extraction_tables (
                            page_id,
                            bbox,
                            metadata_json,
                            review_state
                        )
                        VALUES (%s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            page_id,
                            Jsonb(table["bbox"]),
                            Jsonb({}),
                            table["review_state"],
                        ),
                    ).fetchone()
                    if table_row is None:
                        raise RepositoryError("EXTRACTION_TABLE_INSERT_FAILED")
                    table_id = cast(UUID, table_row["id"])
                    for cell in table["cells"]:
                        connection.execute(
                            """
                            INSERT INTO extraction_cells (
                                table_id,
                                row_index,
                                column_index,
                                text,
                                bbox,
                                review_state
                            )
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            (
                                table_id,
                                cell["row_index"],
                                cell["column_index"],
                                cell["text"],
                                Jsonb(cell["bbox"]),
                                cell["review_state"],
                            ),
                        )
            connection.execute(
                """
                UPDATE extractions
                SET status = 'succeeded', succeeded_at = clock_timestamp()
                WHERE id = %s
                """,
                (extraction_id,),
            )
            if ocr_result is not None:
                _persist_ocr(
                    connection,
                    extraction_id=extraction_id,
                    result=ocr_result,
                )
            self._mark_job_succeeded(connection, job.id, worker_id)
            return extraction_id

    @staticmethod
    def _mark_job_succeeded(
        connection: Connection[dict[str, Any]],
        job_id: UUID,
        worker_id: str,
    ) -> None:
        row = connection.execute(
            """
            UPDATE analysis_jobs
            SET state = 'succeeded',
                lease_owner = NULL,
                lease_expires_at = NULL,
                heartbeat_at = NULL,
                error_code = NULL,
                updated_at = clock_timestamp()
            WHERE id = %s
              AND state = 'running'
              AND lease_owner = %s
              AND lease_expires_at > clock_timestamp()
            RETURNING id
            """,
            (job_id, worker_id),
        ).fetchone()
        if row is None:
            raise JobStateConflict


_BATCH_WORKER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_BATCH_RETRYABLE_CODES = frozenset({"EXTRACTION_TIMEOUT", "OCR_TIMEOUT", "RESOURCE_LIMIT_EXCEEDED"})


def _lock_owned_batch_item(
    connection: Connection[dict[str, Any]],
    item_id: UUID,
    worker_id: str,
) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT item.*, item.lease_expires_at > clock_timestamp() AS lease_valid
        FROM document_batch_items AS item
        WHERE item.id = %s
        FOR UPDATE
        """,
        (item_id,),
    ).fetchone()
    if (
        row is None
        or row["state"] != "running"
        or row["lease_owner"] != worker_id
        or not row["lease_valid"]
    ):
        raise DocumentStateConflict
    return row


def _refresh_batch_state(
    connection: Connection[dict[str, Any]],
    batch_id: UUID,
) -> None:
    batch = connection.execute(
        "SELECT state FROM document_batches WHERE id = %s FOR UPDATE",
        (batch_id,),
    ).fetchone()
    if batch is None:
        raise DocumentStateConflict
    if batch["state"] == "cancelled":
        return
    counts = connection.execute(
        """
        SELECT
            count(*) AS total,
            count(*) FILTER (WHERE state = 'succeeded') AS succeeded,
            count(*) FILTER (WHERE state = 'permanently_failed') AS failed,
            count(*) FILTER (
                WHERE state IN ('queued', 'running', 'retryable_failed')
            ) AS active
        FROM document_batch_items
        WHERE batch_id = %s
        """,
        (batch_id,),
    ).fetchone()
    if counts is None or counts["total"] < 1:
        raise DocumentStateConflict
    terminal = False
    if counts["succeeded"] == counts["total"]:
        state = "succeeded"
        terminal = True
    elif counts["active"] > 0:
        state = "running"
    elif counts["failed"] == counts["total"]:
        state = "failed"
        terminal = True
    else:
        state = "partial"
    connection.execute(
        """
        UPDATE document_batches
        SET state = %s,
            completed_at = CASE WHEN %s THEN clock_timestamp() ELSE NULL END,
            updated_at = clock_timestamp()
        WHERE id = %s
        """,
        (state, terminal, batch_id),
    )


class BatchRepository:
    """Lease-safe Worker queue and atomic extraction/archive persistence."""

    def __init__(self, database_url: str) -> None:
        self.database_url = psycopg_database_url(database_url)

    def claim_next_item(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 180,
    ) -> BatchItemRecord | None:
        if _BATCH_WORKER_PATTERN.fullmatch(worker_id) is None:
            raise ValueError("invalid worker identity")
        if isinstance(lease_seconds, bool) or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid lease duration")
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            expired = connection.execute(
                """
                UPDATE document_batch_items
                SET state = CASE
                        WHEN attempts >= max_attempts THEN 'permanently_failed'
                        ELSE 'retryable_failed'
                    END,
                    error_code = 'EXTRACTION_TIMEOUT',
                    available_at = clock_timestamp(), lease_owner = NULL,
                    lease_expires_at = NULL, heartbeat_at = NULL,
                    completed_at = CASE WHEN attempts >= max_attempts
                        THEN clock_timestamp() ELSE NULL END,
                    updated_at = clock_timestamp()
                WHERE state = 'running' AND lease_expires_at <= clock_timestamp()
                RETURNING batch_id
                """
            ).fetchall()
            for batch_id in {cast(UUID, row["batch_id"]) for row in expired}:
                _refresh_batch_state(connection, batch_id)
            row = connection.execute(
                """
                WITH candidate AS (
                    SELECT item.id
                    FROM document_batch_items AS item
                    JOIN document_batches AS batch ON batch.id = item.batch_id
                    WHERE item.state IN ('queued', 'retryable_failed')
                      AND item.available_at <= clock_timestamp()
                      AND item.attempts < item.max_attempts
                      AND batch.state IN ('created', 'running', 'partial')
                    ORDER BY item.available_at, item.created_at, item.id
                    FOR UPDATE OF item SKIP LOCKED
                    LIMIT 1
                )
                UPDATE document_batch_items AS item
                SET state = 'running', attempts = item.attempts + 1,
                    ocr_state = 'pending', ocr_pages_processed = 0,
                    ocr_warning_codes = '[]'::jsonb,
                    lease_owner = %s,
                    lease_expires_at = clock_timestamp() + (%s * interval '1 second'),
                    heartbeat_at = clock_timestamp(), error_code = NULL,
                    updated_at = clock_timestamp()
                FROM candidate
                WHERE item.id = candidate.id
                RETURNING item.*
                """,
                (worker_id, lease_seconds),
            ).fetchone()
            if row is None:
                return None
            document_id = row["document_id"]
            if document_id is None:
                document = connection.execute(
                    """
                    INSERT INTO documents (source_key, document_kind, status)
                    VALUES (%s, 'supporting', 'pending')
                    RETURNING id
                    """,
                    (f"private-import/{row['batch_id'].hex}/{row['source_id']}",),
                ).fetchone()
                if document is None:
                    raise DocumentStateConflict
                document_id = document["id"]
                connection.execute(
                    "UPDATE document_batch_items SET document_id = %s WHERE id = %s",
                    (document_id, row["id"]),
                )
            _refresh_batch_state(connection, cast(UUID, row["batch_id"]))
            return BatchItemRecord(
                id=cast(UUID, row["id"]),
                batch_id=cast(UUID, row["batch_id"]),
                source_id=cast(str, row["source_id"]),
                source_key=cast(str, row["source_key"]),
                document_id=cast(UUID, document_id),
                document_version_id=UUID(int=cast(UUID, row["id"]).int),
                state="running",
                attempts=cast(int, row["attempts"]),
                max_attempts=cast(int, row["max_attempts"]),
                lease_owner=worker_id,
                lease_expires_at=cast(datetime, row["lease_expires_at"]),
                error_code=None,
            )

    def heartbeat(self, item_id: UUID, worker_id: str, *, lease_seconds: int = 180) -> bool:
        if _BATCH_WORKER_PATTERN.fullmatch(worker_id) is None or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid batch heartbeat")
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                UPDATE document_batch_items
                SET heartbeat_at = clock_timestamp(),
                    lease_expires_at = clock_timestamp() + (%s * interval '1 second'),
                    updated_at = clock_timestamp()
                WHERE id = %s AND state = 'running' AND lease_owner = %s
                  AND lease_expires_at > clock_timestamp()
                RETURNING id
                """,
                (lease_seconds, item_id, worker_id),
            ).fetchone()
        return row is not None

    def mark_password_required(self, item_id: UUID, worker_id: str, **_: object) -> None:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            item = _lock_owned_batch_item(connection, item_id, worker_id)
            connection.execute(
                """
                UPDATE document_batch_items
                SET state = 'password_required', error_code = 'PASSWORD_REQUIRED',
                    lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                    updated_at = clock_timestamp()
                WHERE id = %s
                """,
                (item_id,),
            )
            _refresh_batch_state(connection, cast(UUID, item["batch_id"]))

    def mark_ocr_progress(
        self,
        item_id: UUID,
        worker_id: str,
        *,
        state: str,
        pages_processed: int,
        warning_codes: tuple[str, ...] = (),
        lease_seconds: int = 180,
    ) -> bool:
        if (
            state not in {"running", "native_only"}
            or isinstance(pages_processed, bool)
            or not 0 <= pages_processed <= 500
            or (state == "native_only" and pages_processed != 0)
            or len(warning_codes) > 8
            or len(set(warning_codes)) != len(warning_codes)
            or any(code not in _OCR_WARNING_CODES for code in warning_codes)
            or _BATCH_WORKER_PATTERN.fullmatch(worker_id) is None
            or not 1 <= lease_seconds <= 3600
        ):
            raise ValueError("invalid OCR progress")
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                UPDATE document_batch_items
                SET ocr_state = %s, ocr_pages_processed = %s,
                    ocr_warning_codes = %s,
                    heartbeat_at = clock_timestamp(),
                    lease_expires_at = clock_timestamp() + (%s * interval '1 second'),
                    updated_at = clock_timestamp()
                WHERE id = %s AND state = 'running' AND lease_owner = %s
                  AND lease_expires_at > clock_timestamp()
                RETURNING id
                """,
                (
                    state,
                    pages_processed,
                    Jsonb(list(warning_codes)),
                    lease_seconds,
                    item_id,
                    worker_id,
                ),
            ).fetchone()
        return row is not None

    def mark_failed(
        self,
        item_id: UUID,
        worker_id: str,
        error_code: str,
        **_: object,
    ) -> None:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", error_code):
            error_code = "RESOURCE_LIMIT_EXCEEDED"
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            item = _lock_owned_batch_item(connection, item_id, worker_id)
            retryable = (
                error_code in _BATCH_RETRYABLE_CODES and item["attempts"] < item["max_attempts"]
            )
            connection.execute(
                """
                UPDATE document_batch_items
                SET state = %s, error_code = %s,
                    ocr_state = CASE WHEN ocr_state = 'running'
                        THEN 'failed' ELSE ocr_state END,
                    available_at = CASE WHEN %s
                        THEN clock_timestamp() + interval '5 seconds' ELSE available_at END,
                    lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                    completed_at = CASE WHEN %s THEN NULL ELSE clock_timestamp() END,
                    updated_at = clock_timestamp()
                WHERE id = %s
                """,
                (
                    "retryable_failed" if retryable else "permanently_failed",
                    error_code,
                    retryable,
                    retryable,
                    item_id,
                ),
            )
            _refresh_batch_state(connection, cast(UUID, item["batch_id"]))

    def mark_succeeded(
        self,
        item_id: UUID,
        worker_id: str,
        *args: object,
        archive: ArchiveMetadata | None = None,
        extraction: object = None,
        ocr: SelectiveOcrResult | None = None,
        validated: ValidatedPdf | None = None,
        **_: object,
    ) -> None:
        del args
        if archive is None or validated is None:
            raise DocumentStateConflict
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            item = _lock_owned_batch_item(connection, item_id, worker_id)
            document_id = item["document_id"]
            if not isinstance(document_id, UUID):
                raise DocumentStateConflict
            result = _validate_result(
                extraction,
                document_version_id=archive.document_version_id,
                expected_content_sha256=validated.content_sha256,
                expected_config_hash=_BATCH_EXTRACTOR_CONFIG_HASH,
            )
            ocr_result = _validate_ocr_result(
                ocr,
                native=result,
                document_version_id=archive.document_version_id,
                content_sha256=validated.content_sha256,
            )
            connection.execute(
                """
                INSERT INTO document_versions (
                    id, document_id, version_number, content_sha256, byte_size, page_count
                )
                VALUES (%s, %s, 1, %s, %s, %s)
                """,
                (
                    archive.document_version_id,
                    document_id,
                    validated.content_sha256,
                    validated.byte_size,
                    validated.page_count,
                ),
            )
            extraction_row = connection.execute(
                """
                INSERT INTO extractions (
                    document_version_id, extractor_name, extractor_version,
                    extractor_config_hash, quality_rule_version, status, succeeded_at
                )
                VALUES (%s, %s, %s, %s, %s, 'succeeded', clock_timestamp())
                RETURNING id
                """,
                (
                    archive.document_version_id,
                    result["extractor_name"],
                    result["extractor_version"],
                    result["extractor_config_hash"],
                    result["quality_rule_version"],
                ),
            ).fetchone()
            if extraction_row is None:
                raise DocumentStateConflict
            extraction_id = cast(UUID, extraction_row["id"])
            for page in result["pages"]:
                quality = page["quality"]
                page_row = connection.execute(
                    """
                    INSERT INTO extraction_pages (
                        extraction_id, page_number, width_points, height_points,
                        non_whitespace_chars, alphanumeric_ratio,
                        replacement_character_ratio, maximum_repeated_character_run,
                        classification, warning_codes
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        extraction_id,
                        page["page_number"],
                        page["width_points"],
                        page["height_points"],
                        quality["non_whitespace_chars"],
                        quality["alphanumeric_ratio"],
                        quality["replacement_character_ratio"],
                        quality["maximum_repeated_character_run"],
                        quality["classification"],
                        Jsonb(page["warning_codes"]),
                    ),
                ).fetchone()
                if page_row is None:
                    raise DocumentStateConflict
                page_id = cast(UUID, page_row["id"])
                for block in page["blocks"]:
                    connection.execute(
                        """
                        INSERT INTO extraction_blocks (page_id, text, bbox, reading_order)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (page_id, block["text"], Jsonb(block["bbox"]), block["reading_order"]),
                    )
                for table in page["tables"]:
                    table_row = connection.execute(
                        """
                        INSERT INTO extraction_tables (page_id, bbox, metadata_json, review_state)
                        VALUES (%s, %s, %s, %s)
                        RETURNING id
                        """,
                        (page_id, Jsonb(table["bbox"]), Jsonb({}), table["review_state"]),
                    ).fetchone()
                    if table_row is None:
                        raise DocumentStateConflict
                    for cell in table["cells"]:
                        connection.execute(
                            """
                            INSERT INTO extraction_cells (
                                table_id, row_index, column_index, text, bbox, review_state
                            )
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            (
                                table_row["id"],
                                cell["row_index"],
                                cell["column_index"],
                                cell["text"],
                                Jsonb(cell["bbox"]),
                                cell["review_state"],
                            ),
                        )
            if ocr_result is not None:
                _persist_ocr(
                    connection,
                    extraction_id=extraction_id,
                    result=ocr_result,
                )
            connection.execute(
                """
                INSERT INTO managed_archives (
                    id, document_version_id, object_key, scheme, key_version,
                    nonce, wrapped_data_key, ciphertext_size, auth_tag
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    archive.archive_id,
                    archive.document_version_id,
                    archive.object_key,
                    archive.scheme,
                    archive.key_version,
                    archive.nonce,
                    archive.wrapped_data_key,
                    archive.ciphertext_size,
                    archive.auth_tag,
                ),
            )
            connection.execute(
                """
                UPDATE documents
                SET media_type = %s, byte_size = %s, page_count = %s,
                    status = 'ready', updated_at = clock_timestamp()
                WHERE id = %s
                """,
                (
                    validated.media_type,
                    validated.byte_size,
                    validated.page_count,
                    document_id,
                ),
            )
            connection.execute(
                """
                UPDATE document_batch_items
                SET state = 'succeeded', error_code = NULL,
                    ocr_state = %s, ocr_pages_processed = %s,
                    ocr_warning_codes = %s,
                    lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                    completed_at = clock_timestamp(), updated_at = clock_timestamp()
                WHERE id = %s
                """,
                (
                    "warning"
                    if ocr_result is not None and ocr_result.warning_codes
                    else "completed"
                    if ocr_result is not None
                    else "native_only",
                    len(ocr_result.pages) if ocr_result is not None else 0,
                    Jsonb(list(ocr_result.warning_codes) if ocr_result is not None else []),
                    item_id,
                ),
            )
            _refresh_batch_state(connection, cast(UUID, item["batch_id"]))

    def active_password_batches(self) -> set[UUID]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT batch_id
                FROM document_batch_items
                WHERE state = 'password_required'
                """
            ).fetchall()
        return {cast(UUID, row["batch_id"]) for row in rows}


__all__ = [
    "BatchItemRecord",
    "BatchRepository",
    "DocumentStateConflict",
    "ExtractionRepository",
    "InvalidExtractionResult",
    "RepositoryError",
]
