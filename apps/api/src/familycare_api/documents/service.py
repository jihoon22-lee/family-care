"""PostgreSQL use cases for local synthetic document analysis jobs."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from familycare_api.documents.generated_contracts import (
    DocumentIngestionRequest,
    ErrorCode,
    ExtractionSummary,
    JobState,
)
from familycare_api.errors import AnalysisJobNotFound, AnalysisServiceUnavailable


@dataclass(frozen=True)
class SubmittedAnalysisJob:
    job_id: UUID
    document_id: UUID
    state: Literal["queued"] = "queued"


@dataclass(frozen=True)
class AnalysisJobStatus:
    job_id: UUID
    document_id: UUID
    state: JobState
    attempts: int
    error_code: ErrorCode | None
    extraction_summary: ExtractionSummary | None


def _psycopg_database_url(database_url: str) -> str:
    if not isinstance(database_url, str) or not database_url:
        raise AnalysisServiceUnavailable
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _canonical_config_hash(request: DocumentIngestionRequest) -> str:
    payload = json.dumps(
        request["extractor_config"],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class DocumentAnalysisService:
    """Enqueue and project synthetic-only analysis jobs without opening files."""

    def __init__(self, database_url: str):
        self.database_url = _psycopg_database_url(database_url)

    @classmethod
    def from_environment(cls) -> DocumentAnalysisService:
        database_url = os.getenv("FAMILYCARE_DATABASE_URL")
        if not database_url:
            raise AnalysisServiceUnavailable
        return cls(database_url)

    def submit(self, request: DocumentIngestionRequest) -> SubmittedAnalysisJob:
        """Create/reuse a logical document and always enqueue one due job."""

        source_key = request["source_key"]
        settings = {
            "document_kind": request["document_kind"],
            "extractor_config": dict(request["extractor_config"]),
        }
        config_hash = _canonical_config_hash(request)
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                document = connection.execute(
                    """
                    INSERT INTO documents (source_key, document_kind, status)
                    VALUES (%s, %s, 'pending')
                    ON CONFLICT (source_key) WHERE deleted_at IS NULL
                    DO NOTHING
                    RETURNING id
                    """,
                    (source_key, request["document_kind"]),
                ).fetchone()
                if document is None:
                    document = connection.execute(
                        """
                        SELECT id
                        FROM documents
                        WHERE source_key = %s AND deleted_at IS NULL
                        FOR SHARE
                        """,
                        (source_key,),
                    ).fetchone()
                if document is None:
                    raise AnalysisServiceUnavailable
                document_id = cast(UUID, document["id"])
                job = connection.execute(
                    """
                    INSERT INTO analysis_jobs (
                        document_id,
                        source_key,
                        settings_json,
                        extractor_config_hash,
                        state,
                        available_at
                    )
                    VALUES (%s, %s, %s, %s, 'queued', clock_timestamp())
                    RETURNING id
                    """,
                    (document_id, source_key, Jsonb(settings), config_hash),
                ).fetchone()
                if job is None:
                    raise AnalysisServiceUnavailable
                return SubmittedAnalysisJob(
                    job_id=cast(UUID, job["id"]),
                    document_id=document_id,
                )
        except psycopg.Error:
            raise AnalysisServiceUnavailable from None

    def get_status(self, job_id: UUID) -> AnalysisJobStatus:
        """Return a path-free job projection and the latest matching success counts."""

        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    SELECT
                        job.id AS job_id,
                        job.document_id,
                        job.state,
                        job.attempts,
                        job.error_code,
                        summary.page_count,
                        summary.block_count,
                        summary.table_count,
                        summary.cell_count
                    FROM analysis_jobs AS job
                    LEFT JOIN LATERAL (
                        SELECT
                            (
                                SELECT count(*)::integer
                                FROM extraction_pages AS page
                                WHERE page.extraction_id = selected.id
                            ) AS page_count,
                            (
                                SELECT count(*)::integer
                                FROM extraction_blocks AS block
                                JOIN extraction_pages AS page ON page.id = block.page_id
                                WHERE page.extraction_id = selected.id
                            ) AS block_count,
                            (
                                SELECT count(*)::integer
                                FROM extraction_tables AS table_candidate
                                JOIN extraction_pages AS page ON page.id = table_candidate.page_id
                                WHERE page.extraction_id = selected.id
                            ) AS table_count,
                            (
                                SELECT count(*)::integer
                                FROM extraction_cells AS cell
                                JOIN extraction_tables AS table_candidate
                                  ON table_candidate.id = cell.table_id
                                JOIN extraction_pages AS page
                                  ON page.id = table_candidate.page_id
                                WHERE page.extraction_id = selected.id
                            ) AS cell_count
                        FROM (
                            SELECT extraction.id
                            FROM document_versions AS version
                            JOIN extractions AS extraction
                              ON extraction.document_version_id = version.id
                            WHERE version.document_id = job.document_id
                              AND extraction.extractor_config_hash = job.extractor_config_hash
                              AND extraction.status = 'succeeded'
                              AND extraction.succeeded_at <= job.updated_at
                            ORDER BY
                                version.version_number DESC,
                                extraction.succeeded_at DESC,
                                extraction.id
                            LIMIT 1
                        ) AS selected
                    ) AS summary ON job.state = 'succeeded'
                    WHERE job.id = %s
                    """,
                    (job_id,),
                ).fetchone()
        except psycopg.Error:
            raise AnalysisServiceUnavailable from None
        if row is None:
            raise AnalysisJobNotFound

        raw_summary: dict[str, Any] | None = None
        if row["state"] == "succeeded" and row.get("page_count") is not None:
            raw_summary = {
                "page_count": int(row["page_count"]),
                "block_count": int(row["block_count"]),
                "table_count": int(row["table_count"]),
                "cell_count": int(row["cell_count"]),
            }
        return AnalysisJobStatus(
            job_id=cast(UUID, row["job_id"]),
            document_id=cast(UUID, row["document_id"]),
            state=cast(JobState, row["state"]),
            attempts=int(row["attempts"]),
            error_code=cast(ErrorCode | None, row.get("error_code")),
            extraction_summary=cast(ExtractionSummary | None, raw_summary),
        )


__all__ = [
    "AnalysisJobStatus",
    "DocumentAnalysisService",
    "SubmittedAnalysisJob",
]
