import math
import sys
from threading import Event
from typing import Any

import psycopg
import pytest
from familycare_worker.__main__ import (
    FairJobRunner,
    _local_ocr_processor,
    _runner_from_environment,
    main,
    run_idle,
    run_worker_loop,
)
from familycare_worker.health import database_is_ready, health_payload
from familycare_worker.ocr.engine import TesseractOcrEngine
from familycare_worker.ocr.renderer import PdfiumPageRenderer
from familycare_worker.runner import EventStructuringJobRunner
from pytest import CaptureFixture, MonkeyPatch


class _FakeResult:
    def __init__(self, value: bool) -> None:
        self.value = value

    def fetchone(self) -> tuple[bool]:
        return (self.value,)


class _FakeConnection:
    def __init__(self, *, analysis_jobs_exists: bool) -> None:
        self.analysis_jobs_exists = analysis_jobs_exists
        self.queries: list[str] = []

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def execute(self, query: str) -> _FakeResult:
        self.queries.append(query)
        return _FakeResult(self.analysis_jobs_exists)


def test_health_payload_reports_analyzer_identity() -> None:
    assert health_payload() == {
        "service": "analyzer",
        "status": "ok",
        "version": "0.0.0",
    }


def test_main_prints_health_payload(capsys: CaptureFixture[str]) -> None:
    stop_event = Event()
    stop_event.set()

    assert main([], stop_event=stop_event) == 0

    captured = capsys.readouterr()
    assert captured.out == '{"service": "analyzer", "status": "ok", "version": "0.0.0"}\n'


def test_health_command_reports_database_ready(capsys: CaptureFixture[str]) -> None:
    assert main(["--health"], database_probe=lambda: True) == 0

    captured = capsys.readouterr()
    assert captured.out == '{"service": "analyzer", "status": "ready", "version": "0.0.0"}\n'


def test_health_command_fails_when_database_is_unavailable(
    capsys: CaptureFixture[str],
) -> None:
    assert main(["--health"], database_probe=lambda: False) == 1

    captured = capsys.readouterr()
    assert captured.out == (
        '{"service": "analyzer", "status": "unavailable", "version": "0.0.0"}\n'
    )


def test_database_probe_is_unavailable_without_configuration(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("FAMILYCARE_DATABASE_URL", raising=False)

    assert database_is_ready() is False


def test_database_probe_is_unavailable_for_invalid_url() -> None:
    assert database_is_ready("not-a-database-url") is False


def test_database_probe_checks_public_analysis_jobs_table(
    monkeypatch: MonkeyPatch,
) -> None:
    connection = _FakeConnection(analysis_jobs_exists=True)
    monkeypatch.setattr(psycopg, "connect", lambda _: connection)

    assert database_is_ready("postgresql://synthetic") is True
    assert any("public" in query and "analysis_jobs" in query for query in connection.queries)


def test_database_probe_is_unavailable_when_analysis_jobs_table_is_missing(
    monkeypatch: MonkeyPatch,
) -> None:
    connection = _FakeConnection(analysis_jobs_exists=False)
    monkeypatch.setattr(psycopg, "connect", lambda _: connection)

    assert database_is_ready("postgresql://synthetic") is False


def test_idle_process_stops_without_external_access() -> None:
    stop_event = Event()
    stop_event.set()

    assert run_idle(stop_event, interval_seconds=0) == 0


def test_worker_loop_runs_one_job_at_a_time_and_stops_cleanly() -> None:
    stop_event = Event()

    class SyntheticRunner:
        calls = 0
        active = False

        def run_once(self, worker_id: str) -> bool:
            assert worker_id == "worker-a"
            assert self.active is False
            self.active = True
            self.calls += 1
            self.active = False
            if self.calls == 2:
                stop_event.set()
            return True

    runner = SyntheticRunner()

    assert (
        run_worker_loop(
            stop_event,
            runner,
            worker_id="worker-a",
            poll_interval_seconds=0,
        )
        == 0
    )
    assert runner.calls == 2
    assert runner.active is False


def test_worker_loop_rejects_non_finite_poll_interval() -> None:
    stop_event = Event()
    stop_event.set()

    class SyntheticRunner:
        def run_once(self, worker_id: str) -> bool:
            raise AssertionError("runner must not be called")

    for invalid_interval in (True, math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="poll interval"):
            run_worker_loop(
                stop_event,
                SyntheticRunner(),
                worker_id="worker-a",
                poll_interval_seconds=invalid_interval,
            )


def test_fair_runner_checks_both_queues_without_starvation() -> None:
    calls: list[str] = []

    class SyntheticRunner:
        def __init__(self, name: str, results: list[bool]) -> None:
            self.name = name
            self.results = results

        def run_once(self, worker_id: str) -> bool:
            assert worker_id == "worker-a"
            calls.append(self.name)
            return self.results.pop(0)

    events = SyntheticRunner("events", [False, True])
    documents = SyntheticRunner("documents", [True, False])
    runner = FairJobRunner(events=events, documents=documents)

    assert runner.run_once("worker-a") is True
    assert runner.run_once("worker-a") is True
    assert calls == ["events", "documents", "documents", "events"]


def test_environment_builds_event_runner_without_document_roots(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", "postgresql://synthetic")
    monkeypatch.delenv("FAMILYCARE_DOCUMENT_ROOT", raising=False)
    monkeypatch.delenv("FAMILYCARE_WORK_ROOT", raising=False)

    runner = _runner_from_environment(Event())

    assert isinstance(runner, EventStructuringJobRunner)


def test_local_ocr_processor_factory_is_lazy_and_descriptor_only() -> None:
    processor = _local_ocr_processor()

    assert isinstance(processor.renderer, PdfiumPageRenderer)
    assert processor.engine_factory is TesseractOcrEngine


def test_console_entrypoint_reads_process_arguments(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    stop_event = Event()
    stop_event.set()
    monkeypatch.setattr(sys, "argv", ["familycare-worker", "--health"])

    assert main(database_probe=lambda: True, stop_event=stop_event) == 0

    captured = capsys.readouterr()
    assert captured.out == '{"service": "analyzer", "status": "ready", "version": "0.0.0"}\n'
