"""Unit and HTTP contract tests for the local synthetic analysis API."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from familycare_api.main import create_app
from fastapi.testclient import TestClient

SYNTHETIC_DATABASE_URL = (
    "postgresql+psycopg://synthetic-api-test:synthetic-only@127.0.0.1:5432/synthetic"
)
SYNTHETIC_REQUEST = {
    "schema_version": "1",
    "source_key": "synthetic/policy-001.pdf",
    "document_kind": "policy",
    "extractor_config": {
        "profile": "quality-v1",
        "quality_rule_version": "quality-v1",
        "table_strategy": "auto",
    },
}
SYNTHETIC_FORBIDDEN_VALUE = "synthetic-forbidden-field-value"


@dataclass
class _FakeJob:
    """Small synthetic row projection used to isolate HTTP behavior from PostgreSQL."""

    id: UUID
    document_id: UUID
    source_key: str
    settings_json: dict[str, Any]
    extractor_config_hash: str
    state: str = "queued"
    attempts: int = 0
    error_code: str | None = None
    page_count: int = 0
    block_count: int = 0
    table_count: int = 0
    cell_count: int = 0
    available_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    max_attempts: int = 3


class _FakeResult:
    def __init__(self, rows: Sequence[dict[str, Any]] = ()) -> None:
        self._rows = list(rows)

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _FakeConnection:
    def __init__(self, database: _FakeDatabase) -> None:
        self.database = database

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        del exc_type, exc, traceback
        return False

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def execute(
        self,
        query: str,
        params: Sequence[Any] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> _FakeResult:
        del args, kwargs
        normalized = " ".join(query.split()).lower()
        values = tuple(params or ())
        self.database.queries.append((normalized, values))

        if normalized.startswith("select") and "from analysis_jobs" in normalized:
            job_id = self.database._uuid_param(values)
            job = self.database.jobs.get(job_id)
            return _FakeResult([self.database._job_row(job)]) if job is not None else _FakeResult()

        if normalized.startswith("select") and "from documents" in normalized:
            source_key = self.database._source_key_param(values)
            document_id = self.database.documents.get(source_key)
            if document_id is None:
                return _FakeResult()
            return _FakeResult([{"id": document_id}])

        if normalized.startswith("insert into documents"):
            source_key = self.database._source_key_param(values)
            document_id = self.database.documents.setdefault(source_key, uuid4())
            return _FakeResult([{"id": document_id}])

        if normalized.startswith("insert into analysis_jobs"):
            document_id = self.database._uuid_param(values)
            source_key = self.database._source_key_param(values)
            config_hash = next(
                (
                    value
                    for value in values
                    if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                ),
                "0" * 64,
            )
            job_id = uuid4()
            settings = next(
                (value for value in values if isinstance(value, dict) and "document_kind" in value),
                {"document_kind": "policy", "extractor_config": {}},
            )
            job = _FakeJob(
                id=job_id,
                document_id=document_id,
                source_key=source_key,
                settings_json=settings,
                extractor_config_hash=config_hash,
            )
            self.database.jobs[job_id] = job
            return _FakeResult([{"id": job_id}])

        if normalized.startswith(("update", "delete", "insert")):
            self.database.write_queries.append((normalized, values))
        return _FakeResult()


class _FakeDatabase:
    def __init__(self) -> None:
        self.documents: dict[str, UUID] = {}
        self.jobs: dict[UUID, _FakeJob] = {}
        self.connections = 0
        self.queries: list[tuple[str, tuple[Any, ...]]] = []
        self.write_queries: list[tuple[str, tuple[Any, ...]]] = []

    def connect(self, *args: Any, **kwargs: Any) -> _FakeConnection:
        del args, kwargs
        self.connections += 1
        return _FakeConnection(self)

    def seed_job(
        self,
        *,
        state: str,
        attempts: int,
        error_code: str | None = None,
        summary: tuple[int, int, int, int] = (0, 0, 0, 0),
    ) -> UUID:
        document_id = uuid4()
        job_id = uuid4()
        job = _FakeJob(
            id=job_id,
            document_id=document_id,
            source_key="synthetic/seeded-status.pdf",
            settings_json=SYNTHETIC_REQUEST,
            extractor_config_hash="a" * 64,
            state=state,
            attempts=attempts,
            error_code=error_code,
            page_count=summary[0],
            block_count=summary[1],
            table_count=summary[2],
            cell_count=summary[3],
        )
        self.jobs[job_id] = job
        return job_id

    @staticmethod
    def _uuid_param(values: Sequence[Any]) -> UUID:
        for value in values:
            if isinstance(value, UUID):
                return value
            if isinstance(value, str):
                try:
                    return UUID(value)
                except ValueError:
                    continue
        return UUID(int=0)

    @staticmethod
    def _source_key_param(values: Sequence[Any]) -> str:
        for value in values:
            if isinstance(value, str) and value.startswith("synthetic/"):
                return value
        return "synthetic/unknown.pdf"

    @staticmethod
    def _job_row(job: _FakeJob) -> dict[str, Any]:
        return {
            "id": job.id,
            "job_id": job.id,
            "document_id": job.document_id,
            "source_key": job.source_key,
            "settings_json": job.settings_json,
            "extractor_config_hash": job.extractor_config_hash,
            "state": job.state,
            "attempts": job.attempts,
            "max_attempts": job.max_attempts,
            "error_code": job.error_code,
            "available_at": job.available_at,
            "lease_owner": job.lease_owner,
            "lease_expires_at": job.lease_expires_at,
            "heartbeat_at": job.heartbeat_at,
            "page_count": job.page_count,
            "block_count": job.block_count,
            "table_count": job.table_count,
            "cell_count": job.cell_count,
        }


@pytest.fixture()
def fake_database(monkeypatch: pytest.MonkeyPatch) -> _FakeDatabase:
    database = _FakeDatabase()
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", SYNTHETIC_DATABASE_URL)
    monkeypatch.setattr(psycopg, "connect", database.connect)
    return database


@pytest.fixture()
def client(fake_database: _FakeDatabase) -> Iterator[TestClient]:
    del fake_database
    with TestClient(create_app(enable_synthetic_ingestion=True)) as test_client:
        yield test_client


@pytest.fixture()
def disabled_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("FAMILYCARE_ENV", raising=False)
    monkeypatch.delenv("FAMILYCARE_ENABLE_SYNTHETIC_INGESTION", raising=False)
    with TestClient(create_app()) as test_client:
        yield test_client


def _assert_invalid_request(response: Any, *, raw_value: str | None = None) -> None:
    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == "INVALID_REQUEST"
    assert "detail" not in body
    assert isinstance(body.get("fields"), list)
    assert all(isinstance(field, str) for field in body["fields"])
    if raw_value is not None:
        assert raw_value not in response.text


def test_disabled_app_returns_404_for_analysis_routes(disabled_client: TestClient) -> None:
    response = disabled_client.post(
        "/api/v1/documents/analysis",
        json=SYNTHETIC_REQUEST,
    )
    status_response = disabled_client.get(
        "/api/v1/analysis-jobs/00000000-0000-4000-8000-000000000001"
    )

    assert response.status_code == 404
    assert status_response.status_code == 404
    assert disabled_client.get("/health/live").status_code == 200


@pytest.mark.parametrize(
    ("environment", "feature_flag", "expected_status"),
    [
        ("development", None, 404),
        (None, "true", 404),
        ("production", "true", 404),
        ("development", "true", 202),
    ],
)
def test_environment_gate_requires_both_exact_opt_in_values(
    fake_database: _FakeDatabase,
    monkeypatch: pytest.MonkeyPatch,
    environment: str | None,
    feature_flag: str | None,
    expected_status: int,
) -> None:
    del fake_database
    for name, value in (
        ("FAMILYCARE_ENV", environment),
        ("FAMILYCARE_ENABLE_SYNTHETIC_INGESTION", feature_flag),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    with TestClient(create_app()) as test_client:
        response = test_client.post(
            "/api/v1/documents/analysis",
            json=SYNTHETIC_REQUEST,
        )

    assert response.status_code == expected_status


def test_explicit_disabled_override_wins_over_environment(
    fake_database: _FakeDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fake_database
    monkeypatch.setenv("FAMILYCARE_ENV", "development")
    monkeypatch.setenv("FAMILYCARE_ENABLE_SYNTHETIC_INGESTION", "true")

    with TestClient(create_app(enable_synthetic_ingestion=False)) as test_client:
        response = test_client.post(
            "/api/v1/documents/analysis",
            json=SYNTHETIC_REQUEST,
        )

    assert response.status_code == 404


def test_enabled_submit_returns_async_job_and_stable_status_url(client: TestClient) -> None:
    response = client.post("/api/v1/documents/analysis", json=SYNTHETIC_REQUEST)

    assert response.status_code == 202
    body = response.json()
    job_id = UUID(body["job_id"])
    assert body["state"] == "queued"
    assert body["status_url"] == f"/api/v1/analysis-jobs/{job_id}"
    assert "password" not in body
    assert "content_sha256" not in body


@pytest.mark.parametrize(
    ("state", "attempts", "error_code", "summary"),
    [
        ("queued", 0, None, (0, 0, 0, 0)),
        ("running", 1, None, (0, 0, 0, 0)),
        ("succeeded", 1, None, (2, 5, 1, 3)),
    ],
)
def test_status_get_projects_seeded_job_states(
    client: TestClient,
    fake_database: _FakeDatabase,
    state: str,
    attempts: int,
    error_code: str | None,
    summary: tuple[int, int, int, int],
) -> None:
    job_id = fake_database.seed_job(
        state=state,
        attempts=attempts,
        error_code=error_code,
        summary=summary,
    )

    response = client.get(f"/api/v1/analysis-jobs/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == str(job_id)
    assert body["state"] == state
    assert body["attempts"] == attempts
    assert body.get("error_code") is error_code
    assert "source_key" not in body
    assert "password" not in response.text.lower()
    assert "/mnt/" not in response.text
    if state == "succeeded":
        assert body["extraction_summary"] == {
            "page_count": summary[0],
            "block_count": summary[1],
            "table_count": summary[2],
            "cell_count": summary[3],
        }
    else:
        assert "extraction_summary" not in body


def test_unknown_job_returns_analysis_job_not_found(client: TestClient) -> None:
    response = client.get("/api/v1/analysis-jobs/00000000-0000-4000-8000-000000000099")

    assert response.status_code == 404
    assert response.json()["error_code"] == "ANALYSIS_JOB_NOT_FOUND"
    assert response.json()["error_code"] != "DOCUMENT_NOT_FOUND"


def test_enabled_route_returns_sanitized_unavailable_without_database_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FAMILYCARE_DATABASE_URL", raising=False)

    with TestClient(create_app(enable_synthetic_ingestion=True)) as test_client:
        response = test_client.post(
            "/api/v1/documents/analysis",
            json=SYNTHETIC_REQUEST,
        )

    assert response.status_code == 503
    assert response.json() == {
        "error_code": "RESOURCE_LIMIT_EXCEEDED",
        "message": "analysis service unavailable",
    }
    assert SYNTHETIC_REQUEST["source_key"] not in response.text


@pytest.mark.parametrize(
    "source_key",
    [
        "/outside/synthetic-policy.pdf",
        "synthetic/../policy-001.pdf",
        "../synthetic-policy.pdf",
    ],
)
def test_invalid_source_key_returns_sanitized_error_without_db_write(
    client: TestClient,
    fake_database: _FakeDatabase,
    source_key: str,
) -> None:
    payload = {**SYNTHETIC_REQUEST, "source_key": source_key}

    response = client.post("/api/v1/documents/analysis", json=payload)

    _assert_invalid_request(response, raw_value=source_key)
    assert fake_database.write_queries == []
    assert fake_database.jobs == {}
    assert fake_database.documents == {}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("password", SYNTHETIC_FORBIDDEN_VALUE),
        ("absolute_path", "/outside/synthetic-policy.pdf"),
        ("raw_pdf", SYNTHETIC_FORBIDDEN_VALUE),
        ("url", "https://synthetic.invalid/document.pdf"),
    ],
)
def test_forbidden_extra_field_returns_invalid_request_without_db_row(
    client: TestClient,
    fake_database: _FakeDatabase,
    field: str,
    value: str,
) -> None:
    payload = {**SYNTHETIC_REQUEST, field: value}

    response = client.post("/api/v1/documents/analysis", json=payload)

    _assert_invalid_request(response, raw_value=value)
    assert fake_database.write_queries == []
    assert fake_database.jobs == {}
    assert fake_database.documents == {}
