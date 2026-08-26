from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from scripts import private_acceptance
from scripts.private_acceptance import (
    COMMAND_TIMEOUT_SECONDS,
    MAX_OUTPUT_BYTES,
    AcceptanceCategory,
    classify_gateway_status,
    inspect_tailscale,
)
from scripts.private_runtime_policy import validate_private_roots

SYNTHETIC_NODE = "synthetic-node-a"
SYNTHETIC_IP = "100.64.0.42"
SYNTHETIC_TAILNET = "synthetic-tailnet.example"
SYNTHETIC_STDOUT = "raw synthetic output must never be copied"
STATUS_COMMAND = ["tailscale", "status", "--json"]


def _completed(
    argv: Sequence[str],
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> CompletedProcess[str]:
    return CompletedProcess(list(argv), returncode, stdout, stderr)


def test_status_report_contains_only_a_stable_connected_category() -> None:
    status_json = json.dumps(
        {
            "BackendState": "Running",
            "Self": {
                "HostName": SYNTHETIC_NODE,
                "TailscaleIPs": [SYNTHETIC_IP],
                "DNSName": f"{SYNTHETIC_NODE}.{SYNTHETIC_TAILNET}",
            },
            "Peer": {"synthetic-peer": {"HostName": "synthetic-peer"}},
        }
    )

    def runner(argv: Sequence[str], **_kwargs: object) -> CompletedProcess[str]:
        return _completed(argv, stdout=status_json)

    report = inspect_tailscale(STATUS_COMMAND, runner=runner)

    assert report.category is AcceptanceCategory.TAILSCALE_CONNECTED
    rendered = repr(report)
    assert SYNTHETIC_NODE not in rendered
    assert SYNTHETIC_IP not in rendered
    assert SYNTHETIC_TAILNET not in rendered
    assert status_json not in rendered


def _assert_mutation_commands_are_rejected_before_runner_invocation(
    argv: list[str],
) -> None:
    calls = 0

    def runner(_argv: Sequence[str], **_kwargs: object) -> CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return _completed(_argv)

    with pytest.raises(ValueError, match="tailscale-command-not-read-only"):
        inspect_tailscale(argv, runner=runner)

    assert calls == 0


@pytest.mark.parametrize(
    "argv",
    [
        ["tailscale", "serve", "--bg", "http://127.0.0.1:8080"],
        ["tailscale", "funnel", "8080"],
        ["tailscale", "route", "approve", "10.0.0.0/8"],
        ["tailscale", "ssh", "root@synthetic-node-a"],
        ["tailscale", "set", "--hostname=synthetic"],
        ["tailscale", "up"],
        ["tailscale", "down"],
        ["tailscale", "logout"],
        ["tailscale", "status", "--json", "--peers=false"],
    ],
)
def test_mutation_or_unknown_forms_are_rejected(argv: list[str]) -> None:
    _assert_mutation_commands_are_rejected_before_runner_invocation(argv)


def test_runner_receives_argv_list_without_shell_and_fixed_timeout() -> None:
    calls: list[tuple[object, dict[str, object]]] = []

    def runner(argv: Sequence[str], **kwargs: object) -> CompletedProcess[str]:
        calls.append((argv, kwargs))
        return _completed(argv, stdout=json.dumps({"BackendState": "Stopped"}))

    report = inspect_tailscale(tuple(STATUS_COMMAND), runner=runner)

    assert report.category is AcceptanceCategory.TAILSCALE_NOT_CONNECTED
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert isinstance(argv, list)
    assert argv == STATUS_COMMAND
    assert kwargs == {
        "capture_output": True,
        "check": False,
        "errors": "replace",
        "shell": False,
        "text": True,
        "timeout": COMMAND_TIMEOUT_SECONDS,
    }


def test_default_runner_stops_reading_after_the_output_limit() -> None:
    command = [
        sys.executable,
        "-c",
        f"import sys; sys.stdout.write('x' * {MAX_OUTPUT_BYTES + 1})",
    ]

    with pytest.raises(private_acceptance._OutputOversized):
        private_acceptance._run_bounded_command(command)


def test_default_runner_terminates_after_the_fixed_timeout() -> None:
    command = [sys.executable, "-c", "import time; time.sleep(1)"]

    with pytest.raises(subprocess.TimeoutExpired):
        private_acceptance._run_bounded_command(command, timeout_seconds=0.05)


@pytest.mark.parametrize(
    "command",
    [["tailscale", "ip", "-1"], ["tailscale", "serve", "status"]],
)
def test_other_read_only_commands_discard_their_output(command: list[str]) -> None:
    def runner(argv: Sequence[str], **_kwargs: object) -> CompletedProcess[str]:
        return _completed(argv, stdout=SYNTHETIC_STDOUT, stderr=SYNTHETIC_NODE)

    report = inspect_tailscale(command, runner=runner)

    assert report.category is AcceptanceCategory.TAILSCALE_CONNECTED
    assert SYNTHETIC_STDOUT not in repr(report)
    assert SYNTHETIC_NODE not in repr(report)


def test_unavailable_runner_error_is_redacted() -> None:
    def runner(_argv: Sequence[str], **_kwargs: object) -> CompletedProcess[str]:
        raise FileNotFoundError("synthetic executable path")

    report = inspect_tailscale(STATUS_COMMAND, runner=runner)

    assert report.category is AcceptanceCategory.TAILSCALE_UNAVAILABLE
    assert "synthetic executable path" not in repr(report)


def test_timeout_runner_error_is_redacted() -> None:
    def runner(argv: Sequence[str], **_kwargs: object) -> CompletedProcess[str]:
        raise subprocess.TimeoutExpired(
            list(argv), COMMAND_TIMEOUT_SECONDS, output=SYNTHETIC_STDOUT
        )

    report = inspect_tailscale(STATUS_COMMAND, runner=runner)

    assert report.category is AcceptanceCategory.TAILSCALE_TIMEOUT
    assert SYNTHETIC_STDOUT not in repr(report)


def test_malformed_status_output_is_redacted() -> None:
    malformed = f'{{"Node": "{SYNTHETIC_NODE}",'

    def runner(argv: Sequence[str], **_kwargs: object) -> CompletedProcess[str]:
        return _completed(argv, stdout=malformed, stderr=SYNTHETIC_STDOUT)

    report = inspect_tailscale(STATUS_COMMAND, runner=runner)

    assert report.category is AcceptanceCategory.TAILSCALE_OUTPUT_MALFORMED
    assert SYNTHETIC_NODE not in repr(report)
    assert SYNTHETIC_STDOUT not in repr(report)


def test_oversized_output_is_rejected_without_retaining_output() -> None:
    oversized = "x" * (MAX_OUTPUT_BYTES + 1)

    def runner(argv: Sequence[str], **_kwargs: object) -> CompletedProcess[str]:
        return _completed(argv, stdout=oversized)

    report = inspect_tailscale(STATUS_COMMAND, runner=runner)

    assert report.category is AcceptanceCategory.TAILSCALE_OUTPUT_OVERSIZED
    assert oversized not in repr(report)


def test_combined_stdout_and_stderr_share_one_output_limit() -> None:
    half = "x" * (MAX_OUTPUT_BYTES // 2 + 1)

    def runner(argv: Sequence[str], **_kwargs: object) -> CompletedProcess[str]:
        return _completed(argv, stdout=half, stderr=half)

    report = inspect_tailscale(STATUS_COMMAND, runner=runner)

    assert report.category is AcceptanceCategory.TAILSCALE_OUTPUT_OVERSIZED
    assert half not in repr(report)


def test_failed_command_redacts_stdout_and_stderr() -> None:
    def runner(argv: Sequence[str], **_kwargs: object) -> CompletedProcess[str]:
        return _completed(
            argv,
            stdout=SYNTHETIC_STDOUT,
            stderr=f"stderr for {SYNTHETIC_NODE}",
            returncode=1,
        )

    report = inspect_tailscale(STATUS_COMMAND, runner=runner)

    assert report.category is AcceptanceCategory.TAILSCALE_COMMAND_FAILED
    assert SYNTHETIC_STDOUT not in repr(report)
    assert SYNTHETIC_NODE not in repr(report)


def test_gateway_categories_are_stable_and_do_not_include_response_body() -> None:
    assert classify_gateway_status(None).category is AcceptanceCategory.GATEWAY_UNREACHABLE
    assert classify_gateway_status(401).category is AcceptanceCategory.APP_AUTH_REQUIRED
    assert classify_gateway_status(200).category is AcceptanceCategory.GATEWAY_REACHABLE


def test_private_acceptance_roots_are_external_and_distinct(tmp_path: Path) -> None:
    repository = tmp_path / "checkout"
    repository.mkdir()
    import_root = tmp_path / "synthetic-import"
    archive_root = tmp_path / "synthetic-archive"
    work_root = tmp_path / "synthetic-work"

    validate_private_roots(repository, import_root, archive_root, work_root)

    with pytest.raises(ValueError, match="root-boundary"):
        validate_private_roots(repository, import_root, import_root, work_root)


def test_cli_help_is_safe() -> None:
    script = Path(__file__).parents[1] / "private_acceptance.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0
    help_text = result.stdout
    assert result.stderr == ""
    assert "OPENAI_API_KEY" not in help_text
    assert SYNTHETIC_NODE not in help_text
    assert SYNTHETIC_IP not in help_text
    assert SYNTHETIC_TAILNET not in help_text
