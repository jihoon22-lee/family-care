"""PostgreSQL-backed synthetic AnalysisJob runner and persistence tests."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from familycare_worker.jobs import JobQueue
from familycare_worker.pdf.errors import IntakeErrorCode
from familycare_worker.pdf.isolation import ParseOutcome
from familycare_worker.repository import ExtractionRepository
from familycare_worker.runner import AnalysisJobRunner
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from workers.analyzer.tests.synthetic_pdf_factory import (
    make_encrypted_pdf,
    make_table_pdf,
    make_text_pdf,
)

pytestmark = pytest.mark.integration

SYNTHETIC_PASSWORD = "synthetic-runner-only-password"
SETTINGS = {
    "document_kind": "policy",
    "extractor_config": {
        "profile": "quality-v1",
        "quality_rule_version": "quality-v1",
        "table_strategy": "auto",
    },
}
CONFIG_HASH = hashlib.sha256(
    json.dumps(
        SETTINGS["extractor_config"],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()


def _database_url() -> str:
    value = os.environ["FAMILYCARE_DATABASE_URL"]
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(autouse=True)
def clean_ingestion_tables() -> None:
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            TRUNCATE TABLE
                analysis_jobs,
                extraction_cells,
                extraction_blocks,
                extraction_tables,
                extraction_pages,
                extractions,
                document_versions,
                documents
            CASCADE
            """
        )


def _seed_job(source_key: str, *, document_id: UUID | None = None) -> tuple[UUID, UUID]:
    with psycopg.connect(_database_url(), row_factory=dict_row) as connection:
        if document_id is None:
            document = connection.execute(
                """
                INSERT INTO documents (source_key, document_kind)
                VALUES (%s, 'policy')
                RETURNING id
                """,
                (source_key,),
            ).fetchone()
            assert document is not None
            document_id = document["id"]
        job = connection.execute(
            """
            INSERT INTO analysis_jobs (
                document_id,
                source_key,
                settings_json,
                extractor_config_hash
            )
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (document_id, source_key, Jsonb(SETTINGS), CONFIG_HASH),
        ).fetchone()
        assert job is not None
        return document_id, job["id"]


def _runner(document_root: Path, work_root: Path, **kwargs: Any) -> AnalysisJobRunner:
    database_url = os.environ["FAMILYCARE_DATABASE_URL"]
    queue = JobQueue(database_url, default_lease_seconds=30)
    repository = ExtractionRepository(database_url)
    return AnalysisJobRunner(
        queue,
        repository,
        document_root=document_root,
        work_root=work_root,
        lease_seconds=30,
        heartbeat_interval_seconds=1,
        **kwargs,
    )


def _counts() -> dict[str, int]:
    tables = (
        "document_versions",
        "extractions",
        "extraction_pages",
        "extraction_blocks",
        "extraction_tables",
        "extraction_cells",
    )
    with psycopg.connect(_database_url(), row_factory=dict_row) as connection:
        return {
            table: connection.execute(f"SELECT count(*) AS count FROM {table}").fetchone()["count"]
            for table in tables
        }


def test_runner_persists_synthetic_extraction_and_completes_job(tmp_path: Path) -> None:
    document_root = tmp_path / "documents"
    work_root = tmp_path / "work"
    document_root.mkdir()
    work_root.mkdir()
    source = make_text_pdf(document_root / "synthetic" / "policy-001.pdf")
    _, job_id = _seed_job("synthetic/policy-001.pdf")

    runner = _runner(document_root, work_root)

    assert runner.run_once("worker-a") is True
    job = runner.queue.get_job(job_id)
    assert job is not None
    assert job.state == "succeeded"
    assert job.error_code is None
    assert source.is_file()
    assert list(work_root.iterdir()) == []
    assert _counts() == {
        "document_versions": 1,
        "extractions": 1,
        "extraction_pages": 1,
        "extraction_blocks": 3,
        "extraction_tables": 0,
        "extraction_cells": 0,
    }


def test_duplicate_content_and_config_reuses_succeeded_extraction(tmp_path: Path) -> None:
    document_root = tmp_path / "documents"
    work_root = tmp_path / "work"
    document_root.mkdir()
    work_root.mkdir()
    make_text_pdf(document_root / "synthetic" / "same.pdf")
    document_id, first_job_id = _seed_job("synthetic/same.pdf")
    runner = _runner(document_root, work_root)
    assert runner.run_once("worker-a") is True

    _, second_job_id = _seed_job("synthetic/same.pdf", document_id=document_id)

    def parser_must_not_run(source_fd: int, settings_json: str, **kwargs: object) -> ParseOutcome:
        del source_fd, settings_json, kwargs
        raise AssertionError("duplicate succeeded extraction must skip the parser")

    duplicate_runner = _runner(
        document_root,
        work_root,
        parser_runner=parser_must_not_run,
    )
    assert duplicate_runner.run_once("worker-b") is True

    first = runner.queue.get_job(first_job_id)
    second = duplicate_runner.queue.get_job(second_job_id)
    assert first is not None and first.state == "succeeded"
    assert second is not None and second.state == "succeeded"
    assert _counts() == {
        "document_versions": 1,
        "extractions": 1,
        "extraction_pages": 1,
        "extraction_blocks": 3,
        "extraction_tables": 0,
        "extraction_cells": 0,
    }


def test_runner_persists_table_cells_and_candidate_coordinates(tmp_path: Path) -> None:
    document_root = tmp_path / "documents"
    work_root = tmp_path / "work"
    document_root.mkdir()
    work_root.mkdir()
    make_table_pdf(document_root / "synthetic-table.pdf")
    _, job_id = _seed_job("synthetic-table.pdf")
    runner = _runner(document_root, work_root)

    assert runner.run_once("worker-a") is True

    job = runner.queue.get_job(job_id)
    assert job is not None and job.state == "succeeded"
    counts = _counts()
    assert counts["extraction_tables"] == 1
    assert counts["extraction_cells"] == 4
    with psycopg.connect(_database_url(), row_factory=dict_row) as connection:
        table = connection.execute("SELECT bbox, review_state FROM extraction_tables").fetchone()
        cells = connection.execute(
            """
            SELECT row_index, column_index, bbox, review_state
            FROM extraction_cells
            ORDER BY row_index, column_index
            """
        ).fetchall()
    assert table is not None
    assert len(table["bbox"]) == 4
    assert table["review_state"] == "candidate"
    assert [(cell["row_index"], cell["column_index"]) for cell in cells] == [
        (0, 0),
        (0, 1),
        (1, 0),
        (1, 1),
    ]
    assert all(len(cell["bbox"]) == 4 for cell in cells)
    assert all(cell["review_state"] == "candidate" for cell in cells)


def test_invalid_child_result_creates_no_partial_extraction(tmp_path: Path) -> None:
    document_root = tmp_path / "documents"
    work_root = tmp_path / "work"
    document_root.mkdir()
    work_root.mkdir()
    make_text_pdf(document_root / "synthetic-invalid-result.pdf")
    _, job_id = _seed_job("synthetic-invalid-result.pdf")

    def invalid_parser(source_fd: int, settings_json: str, **kwargs: object) -> ParseOutcome:
        del source_fd, settings_json, kwargs
        return ParseOutcome(success=True, result={"schema_version": "1"})

    runner = _runner(document_root, work_root, parser_runner=invalid_parser)

    assert runner.run_once("worker-a") is True

    job = runner.queue.get_job(job_id)
    assert job is not None
    assert job.state == "permanently_failed"
    assert job.error_code == IntakeErrorCode.PDF_CORRUPT
    assert _counts()["extractions"] == 0


@pytest.mark.parametrize(
    ("builder", "expected_code"),
    [
        (
            lambda path: make_encrypted_pdf(path, SYNTHETIC_PASSWORD),
            IntakeErrorCode.PASSWORD_REQUIRED,
        ),
        (lambda path: path.write_bytes(b"%PDF-corrupt") or path, IntakeErrorCode.PDF_CORRUPT),
    ],
)
def test_runner_permanently_fails_invalid_synthetic_input_and_cleans_workspace(
    tmp_path: Path,
    builder: Callable[[Path], object],
    expected_code: IntakeErrorCode,
) -> None:
    document_root = tmp_path / "documents"
    work_root = tmp_path / "work"
    document_root.mkdir()
    work_root.mkdir()
    path = document_root / "synthetic-input.pdf"
    builder(path)
    _, job_id = _seed_job(path.name)
    runner = _runner(document_root, work_root)

    assert runner.run_once("worker-a") is True

    job = runner.queue.get_job(job_id)
    assert job is not None
    assert job.state == "permanently_failed"
    assert job.error_code == expected_code
    assert list(work_root.iterdir()) == []
    assert _counts()["extractions"] == 0


def test_timeout_is_retryable_and_internal_child_settings_are_password_free(
    tmp_path: Path,
) -> None:
    document_root = tmp_path / "documents"
    work_root = tmp_path / "work"
    document_root.mkdir()
    work_root.mkdir()
    make_text_pdf(document_root / "synthetic.pdf")
    _, job_id = _seed_job("synthetic.pdf")
    captured: list[str] = []

    def timeout_parser(source_fd: int, settings_json: str, **kwargs: object) -> ParseOutcome:
        del source_fd, kwargs
        captured.append(settings_json)
        return ParseOutcome(
            success=False,
            error_code=IntakeErrorCode.EXTRACTION_TIMEOUT,
            error_message="parser failed",
        )

    runner = _runner(document_root, work_root, parser_runner=timeout_parser)

    assert runner.run_once("worker-a") is True

    job = runner.queue.get_job(job_id)
    assert job is not None
    assert job.state == "retryable_failed"
    assert job.error_code == IntakeErrorCode.EXTRACTION_TIMEOUT
    assert len(captured) == 1
    child_settings = json.loads(captured[0])
    assert set(child_settings) == {
        "content_sha256",
        "document_version_id",
        "extractor_config_hash",
        "quality_rule_version",
        "table_strategy",
    }
    assert "password" not in captured[0].lower()
    assert "source" not in captured[0].lower()
    assert list(work_root.iterdir()) == []


def test_cancellation_during_parser_wait_prevents_persistence(tmp_path: Path) -> None:
    document_root = tmp_path / "documents"
    work_root = tmp_path / "work"
    document_root.mkdir()
    work_root.mkdir()
    make_text_pdf(document_root / "synthetic.pdf")
    _, job_id = _seed_job("synthetic.pdf")
    queue = JobQueue(os.environ["FAMILYCARE_DATABASE_URL"], default_lease_seconds=30)

    def cancelling_parser(source_fd: int, settings_json: str, **kwargs: object) -> ParseOutcome:
        del source_fd, settings_json
        on_progress = kwargs["on_progress"]
        queue.cancel_job(job_id)
        assert callable(on_progress)
        assert on_progress() is False
        return ParseOutcome(success=False, metadata={"cancelled": True})

    runner = _runner(document_root, work_root, parser_runner=cancelling_parser)

    assert runner.run_once("worker-a") is True

    job = runner.queue.get_job(job_id)
    assert job is not None and job.state == "cancelled"
    assert _counts()["extractions"] == 0
    assert list(work_root.iterdir()) == []


def test_cleanup_failure_is_permanent_and_logs_only_sanitized_event(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailedWorkspace:
        def close_and_cleanup(self, *, raise_on_failure: bool = True) -> bool:
            del raise_on_failure
            return False

    document_root = tmp_path / "documents"
    work_root = tmp_path / "work"
    document_root.mkdir()
    work_root.mkdir()
    source_key = "synthetic-sensitive-name.pdf"
    make_text_pdf(document_root / source_key)
    _, job_id = _seed_job(source_key)
    logger = logging.getLogger("familycare.synthetic.runner-test")
    caplog.set_level(logging.ERROR, logger=logger.name)

    runner = _runner(
        document_root,
        work_root,
        workspace_factory=lambda root: FailedWorkspace(),
        logger=logger,
    )

    assert runner.run_once("worker-a") is True

    job = runner.queue.get_job(job_id)
    assert job is not None
    assert job.state == "permanently_failed"
    assert job.error_code == IntakeErrorCode.TEMP_CLEANUP_FAILED
    log_text = " ".join(record.getMessage() for record in caplog.records)
    assert "workspace_cleanup_failed" in log_text
    assert source_key not in log_text
    assert str(document_root) not in log_text
    assert SYNTHETIC_PASSWORD not in log_text
    assert _counts()["extractions"] == 0
