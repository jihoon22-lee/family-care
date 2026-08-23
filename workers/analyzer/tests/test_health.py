from familycare_worker.__main__ import main
from familycare_worker.health import health_payload
from pytest import CaptureFixture


def test_health_payload_reports_analyzer_identity() -> None:
    assert health_payload() == {
        "service": "analyzer",
        "status": "ok",
        "version": "0.0.0",
    }


def test_main_prints_health_payload(capsys: CaptureFixture[str]) -> None:
    assert main([]) == 0

    captured = capsys.readouterr()
    assert captured.out == '{"service": "analyzer", "status": "ok", "version": "0.0.0"}\n'
