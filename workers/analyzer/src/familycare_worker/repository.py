"""Transactional persistence for validated Phase 1 extraction results."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from familycare_worker.generated_contracts import ExtractionResult
from familycare_worker.jobs import (
    AnalysisJobRecord,
    JobNotFound,
    JobStateConflict,
    psycopg_database_url,
)
from familycare_worker.pdf.intake import ValidatedPdf

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REVIEW_STATES = frozenset({"candidate", "confirmed", "rejected"})
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
                self._mark_job_succeeded(connection, job.id, worker_id)
                return cast(UUID, existing["id"])

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


__all__ = [
    "DocumentStateConflict",
    "ExtractionRepository",
    "InvalidExtractionResult",
    "RepositoryError",
]
