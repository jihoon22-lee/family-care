import sys
from threading import Event

from familycare_worker.__main__ import main, run_idle
from familycare_worker.health import database_is_ready, health_payload
from pytest import CaptureFixture, MonkeyPatch


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


def test_idle_process_stops_without_external_access() -> None:
    stop_event = Event()
    stop_event.set()

    assert run_idle(stop_event, interval_seconds=0) == 0


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
