"""PostgreSQL-backed end-to-end tests for the local synthetic analysis API."""

from __future__ import annotations

import json
import logging
import os
from importlib import util as importlib_util
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from familycare_api.main import create_app
from familycare_worker.jobs import JobQueue
from familycare_worker.repository import ExtractionRepository
from familycare_worker.runner import AnalysisJobRunner
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

pytestmark = pytest.mark.integration

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_FACTORY_PATH = REPOSITORY_ROOT / "workers/analyzer/tests/synthetic_pdf_factory.py"
_FACTORY_SPEC = importlib_util.spec_from_file_location(
    "familycare_synthetic_pdf_factory",
    _FACTORY_PATH,
)
if _FACTORY_SPEC is None or _FACTORY_SPEC.loader is None:
    raise RuntimeError("synthetic PDF factory is unavailable")
_PDF_FACTORY: Any = importlib_util.module_from_spec(_FACTORY_SPEC)
_FACTORY_SPEC.loader.exec_module(_PDF_FACTORY)

SOURCE_KEY = "synthetic/document-analysis-success.pdf"
ENCRYPTED_SOURCE_KEY = "synthetic/document-analysis-encrypted.pdf"
SYNTHETIC_PASSWORD = "synthetic-e2e-only-password"
SYNTHETIC_BODY_MARKER = "Synthetic"
ENCRYPTED_BODY_MARKER = "Encrypted Evidence"
EXTRACTOR_CONFIG = {
    "profile": "quality-v1",
    "quality_rule_version": "quality-v1",
    "table_strategy": "auto",
}

_INGESTION_TABLES = (
    "analysis_candidate_evidence",
    "analysis_candidate_fields",
    "analysis_candidate_versions",
    "policy_status_snapshots",
    "riders",
    "policy_parties",
    "policy_contracts",
    "evidence",
    "family_members",
    "household_spaces",
    "extraction_cells",
    "extraction_tables",
    "extraction_blocks",
    "extraction_pages",
    "extractions",
    "analysis_jobs",
    "document_versions",
    "documents",
)


def _psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _reset_ingestion_tables(database_url: str) -> None:
    table_list = ", ".join(_INGESTION_TABLES)
    with psycopg.connect(_psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE")


@pytest.fixture()
def database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required for PostgreSQL integration tests")
    _reset_ingestion_tables(value)
    return value


def _request(source_key: str) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "source_key": source_key,
        "document_kind": "policy",
        "extractor_config": dict(EXTRACTOR_CONFIG),
    }


def _runner(database_url: str, document_root: Path, work_root: Path) -> AnalysisJobRunner:
    return AnalysisJobRunner(
        JobQueue(database_url, default_lease_seconds=30),
        ExtractionRepository(database_url),
        document_root=document_root,
        work_root=work_root,
        lease_seconds=30,
        heartbeat_interval_seconds=1,
    )


def _stored_job(database_url: str, job_id: UUID) -> dict[str, Any]:
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        row = connection.execute(
            """
            SELECT id, source_key, settings_json, state, error_code
            FROM analysis_jobs
            WHERE id = %s
            """,
            (job_id,),
        ).fetchone()
    assert row is not None
    return row


def _assert_sensitive_values_are_not_in_job_payload_or_logs(
    database_url: str,
    job_id: UUID,
    *,
    source_key: str,
    document_path: Path,
    body_marker: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    job = _stored_job(database_url, job_id)
    # The relative source_key is the queue envelope's routing key by contract;
    # settings_json must not duplicate it or carry a path/document payload.
    payload = json.dumps(job["settings_json"], sort_keys=True)
    forbidden_values = (
        SYNTHETIC_PASSWORD,
        source_key,
        str(document_path),
        body_marker,
    )

    assert job["source_key"] == source_key
    assert all(value.lower() not in payload.lower() for value in forbidden_values)

    captured_logs = caplog.text.lower()
    assert all(value.lower() not in captured_logs for value in forbidden_values)


def test_synthetic_pdf_post_worker_run_and_status_get_succeeds(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An explicitly enabled API enqueues one job that the Worker can complete."""

    document_root = tmp_path / "synthetic-document-root"
    work_root = tmp_path / "synthetic-work-root"
    document_root.mkdir()
    work_root.mkdir()
    assert not document_root.is_relative_to(REPOSITORY_ROOT)
    document_path = _PDF_FACTORY.make_text_pdf(document_root / SOURCE_KEY)
    assert document_path.is_file()

    monkeypatch.setenv("FAMILYCARE_ENV", "development")
    monkeypatch.setenv("FAMILYCARE_ENABLE_SYNTHETIC_INGESTION", "true")
    monkeypatch.setenv("FAMILYCARE_DOCUMENT_ROOT", str(document_root))
    caplog.set_level(logging.DEBUG)

    with TestClient(create_app()) as client:
        submitted = client.post("/api/v1/documents/analysis", json=_request(SOURCE_KEY))

        assert submitted.status_code == 202
        submitted_body = submitted.json()
        job_id = UUID(submitted_body["job_id"])
        assert submitted_body["state"] == "queued"
        assert submitted_body["status_url"].startswith("/api/v1/analysis-jobs/")
        assert SYNTHETIC_PASSWORD not in submitted.text
        assert str(document_path) not in submitted.text
        assert SYNTHETIC_BODY_MARKER not in submitted.text

    runner = _runner(database_url, Path(os.environ["FAMILYCARE_DOCUMENT_ROOT"]), work_root)
    assert runner.run_once("synthetic-e2e-worker") is True

    with TestClient(create_app()) as client:
        status = client.get(submitted_body["status_url"])

    assert status.status_code == 200
    status_body = status.json()
    assert status_body["job_id"] == str(job_id)
    assert status_body["state"] == "succeeded"
    assert status_body["attempts"] == 1
    assert status_body.get("error_code") is None
    assert status_body["extraction_summary"]["page_count"] == 1
    assert status_body["extraction_summary"]["block_count"] == 3
    assert list(work_root.iterdir()) == []

    _assert_sensitive_values_are_not_in_job_payload_or_logs(
        database_url,
        job_id,
        source_key=SOURCE_KEY,
        document_path=document_path,
        body_marker=SYNTHETIC_BODY_MARKER,
        caplog=caplog,
    )


def test_encrypted_synthetic_pdf_reports_password_required_without_password_transport(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Encrypted input reaches the Worker and fails without queued password data."""

    document_root = tmp_path / "synthetic-document-root"
    work_root = tmp_path / "synthetic-work-root"
    document_root.mkdir()
    work_root.mkdir()
    assert not document_root.is_relative_to(REPOSITORY_ROOT)
    document_path = _PDF_FACTORY.make_encrypted_pdf(
        document_root / ENCRYPTED_SOURCE_KEY,
        SYNTHETIC_PASSWORD,
    )
    assert document_path.is_file()

    monkeypatch.setenv("FAMILYCARE_ENV", "development")
    monkeypatch.setenv("FAMILYCARE_ENABLE_SYNTHETIC_INGESTION", "true")
    monkeypatch.setenv("FAMILYCARE_DOCUMENT_ROOT", str(document_root))
    caplog.set_level(logging.DEBUG)

    with TestClient(create_app()) as client:
        submitted = client.post(
            "/api/v1/documents/analysis",
            json=_request(ENCRYPTED_SOURCE_KEY),
        )

        assert submitted.status_code == 202
        submitted_body = submitted.json()
        job_id = UUID(submitted_body["job_id"])
        status_url = submitted_body["status_url"]

    runner = _runner(database_url, Path(os.environ["FAMILYCARE_DOCUMENT_ROOT"]), work_root)
    assert runner.run_once("synthetic-e2e-worker") is True

    with TestClient(create_app()) as client:
        status = client.get(status_url)

    assert status.status_code == 200
    status_body = status.json()
    assert status_body["job_id"] == str(job_id)
    assert status_body["state"] == "permanently_failed"
    assert status_body["attempts"] == 1
    assert status_body["error_code"] == "PASSWORD_REQUIRED"
    assert SYNTHETIC_PASSWORD not in status.text
    assert str(document_path) not in status.text
    assert ENCRYPTED_BODY_MARKER not in status.text
    assert list(work_root.iterdir()) == []

    _assert_sensitive_values_are_not_in_job_payload_or_logs(
        database_url,
        job_id,
        source_key=ENCRYPTED_SOURCE_KEY,
        document_path=document_path,
        body_marker=ENCRYPTED_BODY_MARKER,
        caplog=caplog,
    )


def test_repeated_valid_submission_reuses_document_and_enqueues_distinct_jobs(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST remains asynchronous while the active logical document is reused."""

    monkeypatch.setenv("FAMILYCARE_ENV", "development")
    monkeypatch.setenv("FAMILYCARE_ENABLE_SYNTHETIC_INGESTION", "true")

    with TestClient(create_app()) as client:
        first = client.post("/api/v1/documents/analysis", json=_request(SOURCE_KEY))
        second = client.post("/api/v1/documents/analysis", json=_request(SOURCE_KEY))

    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] != second.json()["job_id"]
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        document_count = connection.execute(
            "SELECT count(*) FROM documents WHERE source_key = %s AND deleted_at IS NULL",
            (SOURCE_KEY,),
        ).fetchone()
        job_count = connection.execute(
            "SELECT count(*) FROM analysis_jobs WHERE source_key = %s",
            (SOURCE_KEY,),
        ).fetchone()
    assert document_count == (1,)
    assert job_count == (2,)
