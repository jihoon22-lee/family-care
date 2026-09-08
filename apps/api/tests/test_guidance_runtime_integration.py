"""Small synthetic API/PG timings while an unrelated worker transaction stays open.

These measurements include ASGI routing, source reads, calculation, persistence
and response serialization. They do not measure a network gateway, a full catalog,
CPU saturation, OCR throughput or a protected-data runtime.
"""

import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from time import perf_counter
from typing import NoReturn

import httpx2
import psycopg
import pytest
from familycare_api.common.scope import resolve_household_scope
from familycare_api.decisions.router import get_decision_service, router
from familycare_api.errors import install_error_handlers
from familycare_worker.ai.provider import OpenAiResponsesAdapter
from familycare_worker.jobs import JobQueue
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.tests.test_decision_integration import _psycopg_url, _service
from apps.api.tests.test_semantic_local_guidance_integration import (
    _published_semantic_sources,
    enrollment_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_semantic_local_guidance_integration import (
    changes_database as changes_database,
)
from workers.analyzer.tests.test_analysis_job_queue import _seed_job

pytestmark = pytest.mark.integration

_AI_TABLES = (
    "medical_event_structuring_jobs",
    "policy_structuring_jobs",
    "analysis_assistance_jobs",
    "terms_semantic_jobs",
)


def _ai_job_counts(url: str) -> tuple[tuple[int, int], ...]:
    # Local search shares this table but inserts SUCCEEDED/attempts=0 directly.
    # Count runnable external work and executed attempts, not local bookkeeping.
    with psycopg.connect(_psycopg_url(url)) as connection:
        counts = []
        for table in _AI_TABLES:
            row = connection.execute(
                "SELECT count(*) FILTER (WHERE lower(state::text) IN "
                "('queued','running','retryable_failed','paused')),coalesce(sum(attempts),0) "
                f"FROM {table}"
            ).fetchone()
            assert row is not None
            counts.append((int(row[0]), int(row[1])))
        return tuple(counts)


def _summary(samples: list[float]) -> dict[str, int | float]:
    ordered = sorted(samples)
    assert ordered
    return {
        "samples": len(ordered),
        "p50_ms": round(ordered[math.ceil(len(ordered) * 0.50) - 1], 3),
        "p95_ms": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
    }


def _local_search_job_count(url: str) -> int:
    with psycopg.connect(_psycopg_url(url)) as connection:
        row = connection.execute(
            "SELECT count(*) FROM analysis_assistance_jobs WHERE state='SUCCEEDED' AND attempts=0"
        ).fetchone()
        assert row is not None
        return int(row[0])


def test_text_only_api_guidance_remains_local_during_unrelated_worker_row_locks(
    changes_database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    attempts = {"http": 0, "provider": 0}

    def reject_http(*args, **kwargs) -> NoReturn:
        attempts["http"] += 1
        raise AssertionError("local inquiry attempted external HTTP")

    def reject_provider(*args, **kwargs) -> NoReturn:
        attempts["provider"] += 1
        raise AssertionError("local inquiry attempted an external AI provider")

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", reject_http)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", reject_http)
    monkeypatch.setattr(OpenAiResponsesAdapter, "complete", reject_provider)
    url, job = changes_database
    sources, _, scope = _published_semantic_sources(url, job)
    before_jobs = _ai_job_counts(url)
    before_local_jobs = _local_search_job_count(url)
    service = _service(url, scope)
    event = service.create_medical_event(
        family_member_id=job.family_member_id,
        mode="post_treatment",
        situation="5일 입원했습니다.",
        event_date=date(2025, 6, 15),
        facts={},
    )
    assert not event.facts
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[resolve_household_scope] = lambda: scope
    app.dependency_overrides[get_decision_service] = lambda: service

    def measure_pairs(client: TestClient, count: int) -> dict[str, list[float]]:
        samples: dict[str, list[float]] = {"post_analyze": [], "get_stored": []}
        for _ in range(count):
            start = perf_counter()
            analyzed = client.post(f"/api/v1/medical-events/{event.id}/analyze")
            samples["post_analyze"].append((perf_counter() - start) * 1000)
            assert analyzed.status_code == 200
            assert analyzed.headers["cache-control"] == "no-store"
            payload = analyzed.json()
            guidance = payload["local_guidance"]
            selected = next(
                candidate
                for candidate in guidance["candidates"]
                if candidate["ref"]["coverage_id"] == str(sources["Sample Rider"])
            )
            assert selected["ref"]["kind"] == "OPERATIONAL_RIDER"
            assert selected["estimate"]["amount"] == "300"
            assert selected["estimate"]["evidence"]
            assert all(e["kind"] == "SEMANTIC_CITATION" for e in selected["estimate"]["evidence"])
            start = perf_counter()
            stored = client.get(f"/api/v1/medical-events/{event.id}/results/1")
            samples["get_stored"].append((perf_counter() - start) * 1000)
            assert stored.status_code == 200 and stored.headers["cache-control"] == "no-store"
            saved = stored.json()
            assert saved["run_id"] == payload["run_id"]
            assert saved["local_guidance"] == guidance
            assert saved["local_guidance_stale"] is False
        return samples

    with TestClient(app) as client:
        first = measure_pairs(client, 1)
        warm = measure_pairs(client, 10)
        independent_id = _seed_job(
            url,
            source_key="synthetic/unrelated-runtime-measurement.pdf",
            available_at=datetime(2000, 1, 1, tzinfo=UTC),
        )
        queue = JobQueue(url)
        worker = "synthetic-runtime-measurement"
        leased = queue.claim_next_job(worker, lease_seconds=180)
        assert leased is not None and leased.id == independent_id
        with psycopg.connect(_psycopg_url(url)) as connection:
            assert connection.execute(
                "SELECT document_id<>%s FROM document_versions WHERE id=%s",
                (leased.document_id, job.document_version_id),
            ).fetchone() == (True,)
        assert leased.state == "running" and leased.lease_owner == worker
        # The actual queue lease is committed. Keep only this unrelated worker's
        # progress transaction open while the API completes every request.
        with psycopg.connect(_psycopg_url(url)) as progress:
            assert progress.execute(
                "UPDATE analysis_jobs SET heartbeat_at=clock_timestamp() "
                "WHERE id=%s AND state='running' AND lease_owner=%s RETURNING state",
                (independent_id, worker),
            ).fetchone() == ("running",)
            progress.execute(
                "SELECT id FROM documents WHERE id=%s FOR UPDATE", (leased.document_id,)
            )
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(measure_pairs, client, 10)
                try:
                    # This is a bounded deadlock guard, not a response-time target.
                    concurrent = pending.result(timeout=30)
                    assert progress.execute(
                        "SELECT state,lease_owner FROM analysis_jobs WHERE id=%s", (independent_id,)
                    ).fetchone() == ("running", worker)
                    assert not progress.closed
                finally:
                    # Release before executor shutdown even when a request fails.
                    progress.rollback()
        retained_job = queue.get_job(independent_id)
        assert retained_job is not None and retained_job.state == "running"
        assert retained_job.lease_owner == worker

    assert attempts == {"http": 0, "provider": 0}
    assert _ai_job_counts(url) == before_jobs
    new_local_jobs = _local_search_job_count(url) - before_local_jobs
    assert new_local_jobs == 1
    # Neither derived days nor the worker's existence may rewrite user event facts.
    assert service.get_medical_event(event.id).facts == event.facts
    with psycopg.connect(_psycopg_url(url)) as connection:
        source_counts = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("policy_contracts", "riders", "clauses", "terms_editions")
        }
        assert connection.execute(
            "SELECT count(*) FROM private_knowledge_import_runs"
        ).fetchone() == (0,)
    print(
        json.dumps(
            {
                "scope": "small_synthetic_asgi_postgresql_worker_row_lock_coexistence",
                "first_request_scope": "first API pair after publication; DB cache not flushed",
                "source_counts": source_counts,
                "external_http_requests": attempts["http"],
                "external_provider_attempts": attempts["provider"],
                "new_external_ai_jobs": 0,
                "new_local_search_bookkeeping_jobs": new_local_jobs,
                "api_requests": 42,
                "unrelated_running_jobs": 1,
                "first_pair": {operation: _summary(values) for operation, values in first.items()},
                "warm_idle": {operation: _summary(values) for operation, values in warm.items()},
                "warm_worker_transaction": {
                    operation: _summary(values) for operation, values in concurrent.items()
                },
            },
            sort_keys=True,
        )
    )
