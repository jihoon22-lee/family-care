import math
import os
import socket
import sys
from pathlib import Path
from threading import Event
from typing import Any

import psycopg
import pytest
from familycare_worker.__main__ import (
    FairJobRunner,
    ManagedPrivateRunner,
    _local_ocr_processor,
    _runner_from_environment,
    main,
    run_idle,
    run_worker_loop,
)
from familycare_worker.ai.provider import OpenAiResponsesAdapter
from familycare_worker.health import database_is_ready, health_payload, private_runtime_is_ready
from familycare_worker.imports.secret_channel import BatchSecretReceiver
from familycare_worker.ocr.engine import TesseractOcrEngine
from familycare_worker.ocr.renderer import PdfiumPageRenderer
from familycare_worker.repository import BatchRepository
from familycare_worker.runner import (
    EventStructuringJobRunner,
    PolicyStructuringJobRunner,
    RecommendationJobRunner,
)
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
        "version": "0.3.2",
    }


def test_main_prints_health_payload(capsys: CaptureFixture[str]) -> None:
    stop_event = Event()
    stop_event.set()

    assert main([], stop_event=stop_event) == 0

    captured = capsys.readouterr()
    assert captured.out == '{"service": "analyzer", "status": "ok", "version": "0.3.2"}\n'


def test_health_command_reports_database_ready(capsys: CaptureFixture[str]) -> None:
    assert main(["--health"], database_probe=lambda: True) == 0

    captured = capsys.readouterr()
    assert captured.out == '{"service": "analyzer", "status": "ready", "version": "0.3.2"}\n'


def test_health_command_fails_when_database_is_unavailable(
    capsys: CaptureFixture[str],
) -> None:
    assert main(["--health"], database_probe=lambda: False) == 1

    captured = capsys.readouterr()
    assert captured.out == (
        '{"service": "analyzer", "status": "unavailable", "version": "0.3.2"}\n'
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


def _private_environment(tmp_path: Path) -> dict[str, str]:
    import_root = tmp_path / "import"
    archive_root = tmp_path / "archive"
    work_root = tmp_path / "work"
    socket_root = tmp_path / "run"
    import_root.mkdir()
    archive_root.mkdir()
    work_root.mkdir()
    socket_root.mkdir()
    key_path = tmp_path / "master-key"
    key_path.write_bytes(b"k" * 32)
    key_path.chmod(0o600)
    socket_path = socket_root / "secret.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.close()
    return {
        "FAMILYCARE_IMPORT_ROOT": str(import_root),
        "FAMILYCARE_ARCHIVE_ROOT": str(archive_root),
        "FAMILYCARE_WORK_ROOT": str(work_root),
        "FAMILYCARE_ARCHIVE_MASTER_KEY_FILE": str(key_path),
        "FAMILYCARE_SECRET_SOCKET": str(socket_path),
    }


def test_private_runtime_probe_preserves_non_private_mode() -> None:
    assert private_runtime_is_ready({}) is True


def test_private_runtime_probe_accepts_available_worker_boundaries(tmp_path: Path) -> None:
    assert private_runtime_is_ready(_private_environment(tmp_path)) is True


@pytest.mark.parametrize(
    "missing_name",
    [
        "FAMILYCARE_IMPORT_ROOT",
        "FAMILYCARE_ARCHIVE_ROOT",
        "FAMILYCARE_WORK_ROOT",
        "FAMILYCARE_ARCHIVE_MASTER_KEY_FILE",
        "FAMILYCARE_SECRET_SOCKET",
    ],
)
def test_private_runtime_probe_fails_closed_for_incomplete_configuration(
    tmp_path: Path,
    missing_name: str,
) -> None:
    environment = _private_environment(tmp_path)
    del environment[missing_name]

    assert private_runtime_is_ready(environment) is False


def test_private_runtime_probe_rejects_invalid_key_and_socket(tmp_path: Path) -> None:
    environment = _private_environment(tmp_path)
    key_path = Path(environment["FAMILYCARE_ARCHIVE_MASTER_KEY_FILE"])
    key_path.chmod(0o640)

    assert private_runtime_is_ready(environment) is False

    key_path.chmod(0o600)
    key_path.write_bytes(b"short")
    assert private_runtime_is_ready(environment) is False

    key_path.write_bytes(b"k" * 32)
    Path(environment["FAMILYCARE_SECRET_SOCKET"]).unlink()
    assert private_runtime_is_ready(environment) is False


@pytest.mark.parametrize(
    "environment_name",
    ["FAMILYCARE_IMPORT_ROOT", "FAMILYCARE_ARCHIVE_ROOT", "FAMILYCARE_WORK_ROOT"],
)
def test_private_runtime_probe_rejects_unavailable_directories(
    tmp_path: Path,
    environment_name: str,
) -> None:
    environment = _private_environment(tmp_path)
    Path(environment[environment_name]).rmdir()

    assert private_runtime_is_ready(environment) is False


def test_health_command_combines_database_and_private_runtime_readiness(
    capsys: CaptureFixture[str],
) -> None:
    assert (
        main(
            ["--health"],
            database_probe=lambda: True,
            private_runtime_probe=lambda: False,
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.out == (
        '{"service": "analyzer", "status": "unavailable", "version": "0.3.2"}\n'
    )
    assert os.getcwd() not in captured.out


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


def test_fair_runner_rotates_recommendations_with_existing_lanes() -> None:
    calls: list[str] = []

    class SyntheticRunner:
        def __init__(self, name: str) -> None:
            self.name = name

        def run_once(self, worker_id: str) -> bool:
            assert worker_id == "worker-a"
            calls.append(self.name)
            return True

    runner = FairJobRunner(
        events=SyntheticRunner("events"),
        documents=SyntheticRunner("documents"),
        imports=SyntheticRunner("imports"),
        recommendations=SyntheticRunner("recommendations"),
    )

    for _ in range(4):
        assert runner.run_once("worker-a") is True
    assert calls == ["events", "documents", "imports", "recommendations"]


def test_environment_builds_event_runner_without_document_roots(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", "postgresql://synthetic")
    monkeypatch.delenv("FAMILYCARE_DOCUMENT_ROOT", raising=False)
    monkeypatch.delenv("FAMILYCARE_WORK_ROOT", raising=False)

    monkeypatch.setenv("FAMILYCARE_AI_ASSISTANCE_MODEL", "synthetic-assistance-model")

    runner = _runner_from_environment(Event())

    assert isinstance(runner, FairJobRunner)
    assert any(isinstance(item, EventStructuringJobRunner) for item in runner._runners)
    recommendation_runners = [
        item for item in runner._runners if isinstance(item, RecommendationJobRunner)
    ]
    assert len(recommendation_runners) == 1
    assert recommendation_runners[0].model == "synthetic-assistance-model"


def test_private_work_root_alone_does_not_enable_the_document_runner(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", "postgresql://synthetic")
    monkeypatch.delenv("FAMILYCARE_DOCUMENT_ROOT", raising=False)
    monkeypatch.setenv("FAMILYCARE_WORK_ROOT", str(tmp_path))
    for name in (
        "FAMILYCARE_IMPORT_ROOT",
        "FAMILYCARE_ARCHIVE_ROOT",
        "FAMILYCARE_ARCHIVE_MASTER_KEY_FILE",
        "FAMILYCARE_SECRET_SOCKET",
    ):
        monkeypatch.delenv(name, raising=False)

    runner = _runner_from_environment(Event())

    assert isinstance(runner, FairJobRunner)
    assert any(isinstance(item, EventStructuringJobRunner) for item in runner._runners)
    assert any(isinstance(item, RecommendationJobRunner) for item in runner._runners)
    assert "document_runner_configuration_incomplete" not in caplog.messages


def test_private_environment_wires_policy_queue_and_strict_schemas(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    import_root = tmp_path / "import"
    work_root = tmp_path / "work"
    archive_root = tmp_path / "archive"
    socket_root = tmp_path / "run"
    for path in (import_root, work_root, archive_root, socket_root):
        path.mkdir()
    key_file = tmp_path / "master-key"
    key_file.write_bytes(b"k" * 32)
    key_file.chmod(0o600)
    environment = {
        "FAMILYCARE_DATABASE_URL": "postgresql://synthetic",
        "FAMILYCARE_IMPORT_ROOT": str(import_root),
        "FAMILYCARE_WORK_ROOT": str(work_root),
        "FAMILYCARE_ARCHIVE_ROOT": str(archive_root),
        "FAMILYCARE_ARCHIVE_MASTER_KEY_FILE": str(key_file),
        "FAMILYCARE_SECRET_SOCKET": str(socket_root / "secret.sock"),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("FAMILYCARE_DOCUMENT_ROOT", raising=False)
    monkeypatch.setattr(BatchRepository, "active_password_batches", lambda _: set())
    monkeypatch.setattr(BatchSecretReceiver, "start", lambda _: None)

    runner = _runner_from_environment(Event())
    assert isinstance(runner, ManagedPrivateRunner)
    try:
        assert isinstance(runner.runner, FairJobRunner)
        policy_runners = [
            item for item in runner.runner._runners if isinstance(item, PolicyStructuringJobRunner)
        ]
        assert len(policy_runners) == 1
        provider = policy_runners[0].provider
        assert isinstance(provider, OpenAiResponsesAdapter)
        schemas = provider._schemas
        assert "policy_candidate_batch_structurer_v2" in schemas
        assert "policy_candidate_verifier_v1" in schemas
        assert "event_clause_recommendations_v1" in schemas
        assert provider._output_token_limits["medical_event_structurer_v1"] == 2_000  # noqa: SLF001
        assert provider._request_timeouts["medical_event_structurer_v1"] == 50.0  # noqa: SLF001
    finally:
        runner.shutdown()


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
    assert captured.out == '{"service": "analyzer", "status": "ready", "version": "0.3.2"}\n'
