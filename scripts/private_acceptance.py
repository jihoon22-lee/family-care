#!/usr/bin/env python3
"""Read-only, redacted checks for the private local runtime."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.private_runtime_policy import validate_tailscale_inspection_command  # noqa: E402

COMMAND_TIMEOUT_SECONDS = 5.0
MAX_OUTPUT_BYTES = 16 * 1024


class AcceptanceCategory(StrEnum):
    """Stable categories safe to show in an acceptance report."""

    TAILSCALE_UNAVAILABLE = "tailscale-unavailable"
    TAILSCALE_TIMEOUT = "tailscale-timeout"
    TAILSCALE_OUTPUT_MALFORMED = "tailscale-output-malformed"
    TAILSCALE_OUTPUT_OVERSIZED = "tailscale-output-oversized"
    TAILSCALE_COMMAND_FAILED = "tailscale-command-failed"
    TAILSCALE_NOT_CONNECTED = "tailscale-not-connected"
    TAILSCALE_CONNECTED = "tailscale-connected"
    GATEWAY_UNREACHABLE = "gateway-unreachable"
    GATEWAY_REACHABLE = "gateway-reachable"
    APP_AUTH_REQUIRED = "app-auth-required"


@dataclass(frozen=True)
class AcceptanceReport:
    """A deliberately small report containing no command output or metadata."""

    category: AcceptanceCategory


Runner = Callable[..., subprocess.CompletedProcess[str]]


class _OutputOversized(RuntimeError):
    """Internal signal that intentionally carries no captured output."""


def _run_bounded_command(
    command: Sequence[str],
    *,
    timeout_seconds: float = COMMAND_TIMEOUT_SECONDS,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
) -> subprocess.CompletedProcess[str]:
    """Collect bounded output from one argv-only subprocess."""

    process = subprocess.Popen(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        text=False,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        process.wait()
        raise subprocess.SubprocessError("bounded-output-pipe-unavailable")

    output = {"stdout": bytearray(), "stderr": bytearray()}
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    deadline = time.monotonic() + timeout_seconds
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(list(command), timeout_seconds)
            events = selector.select(remaining)
            if not events:
                raise subprocess.TimeoutExpired(list(command), timeout_seconds)
            for key, _mask in events:
                chunk = os.read(key.fd, min(4096, max_output_bytes + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output[str(key.data)].extend(chunk)
                if sum(len(value) for value in output.values()) > max_output_bytes:
                    raise _OutputOversized
        remaining = deadline - time.monotonic()
        returncode = process.wait(timeout=max(0.001, remaining))
    except subprocess.TimeoutExpired, _OutputOversized:
        process.kill()
        process.wait()
        raise
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            if not stream.closed:
                stream.close()

    return subprocess.CompletedProcess(
        list(command),
        returncode,
        bytes(output["stdout"]).decode("utf-8", errors="replace"),
        bytes(output["stderr"]).decode("utf-8", errors="replace"),
    )


def _output_size(value: object) -> int | None:
    if value is None:
        return 0
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    return None


def _output_text(value: object) -> str | None:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return None


def _report(category: AcceptanceCategory) -> AcceptanceReport:
    return AcceptanceReport(category=category)


def _classify_status_json(stdout: str) -> AcceptanceReport:
    try:
        payload: Any = json.loads(stdout)
    except json.JSONDecodeError, UnicodeDecodeError:
        return _report(AcceptanceCategory.TAILSCALE_OUTPUT_MALFORMED)

    if not isinstance(payload, dict):
        return _report(AcceptanceCategory.TAILSCALE_OUTPUT_MALFORMED)

    backend_state = payload.get("BackendState")
    if not isinstance(backend_state, str):
        return _report(AcceptanceCategory.TAILSCALE_OUTPUT_MALFORMED)
    if backend_state.strip().casefold() == "running":
        return _report(AcceptanceCategory.TAILSCALE_CONNECTED)
    return _report(AcceptanceCategory.TAILSCALE_NOT_CONNECTED)


def inspect_tailscale(
    argv: Sequence[str],
    *,
    runner: Runner | None = None,
) -> AcceptanceReport:
    """Run one exact read-only inspection and return only a stable category.

    Command validation happens before selecting or invoking the runner. The
    runner is injectable so unit tests never need a Tailscale installation.
    """

    command = tuple(argv)
    validate_tailscale_inspection_command(command)

    try:
        if runner is None:
            result = _run_bounded_command(command)
        else:
            result = runner(
                list(command),
                capture_output=True,
                check=False,
                errors="replace",
                shell=False,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
    except subprocess.TimeoutExpired:
        return _report(AcceptanceCategory.TAILSCALE_TIMEOUT)
    except _OutputOversized:
        return _report(AcceptanceCategory.TAILSCALE_OUTPUT_OVERSIZED)
    except FileNotFoundError:
        return _report(AcceptanceCategory.TAILSCALE_UNAVAILABLE)
    except OSError:
        return _report(AcceptanceCategory.TAILSCALE_UNAVAILABLE)
    except subprocess.SubprocessError:
        return _report(AcceptanceCategory.TAILSCALE_COMMAND_FAILED)

    stdout_size = _output_size(result.stdout)
    stderr_size = _output_size(result.stderr)
    if stdout_size is None or stderr_size is None:
        return _report(AcceptanceCategory.TAILSCALE_OUTPUT_MALFORMED)
    if stdout_size + stderr_size > MAX_OUTPUT_BYTES:
        return _report(AcceptanceCategory.TAILSCALE_OUTPUT_OVERSIZED)

    stdout = _output_text(result.stdout)
    stderr = _output_text(result.stderr)
    if stdout is None or stderr is None:
        return _report(AcceptanceCategory.TAILSCALE_OUTPUT_MALFORMED)

    if result.returncode != 0:
        return _report(AcceptanceCategory.TAILSCALE_COMMAND_FAILED)
    if command == ("tailscale", "status", "--json"):
        return _classify_status_json(stdout)
    return _report(AcceptanceCategory.TAILSCALE_CONNECTED)


def classify_gateway_status(status_code: int | None) -> AcceptanceReport:
    """Classify a caller-supplied gateway result without performing I/O."""

    if status_code is None:
        return _report(AcceptanceCategory.GATEWAY_UNREACHABLE)
    if status_code in {401, 403}:
        return _report(AcceptanceCategory.APP_AUTH_REQUIRED)
    return _report(AcceptanceCategory.GATEWAY_REACHABLE)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="+", metavar="COMMAND")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = inspect_tailscale(args.command)
    except ValueError:
        print("tailscale-command-not-read-only")
        return 2
    print(report.category.value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "COMMAND_TIMEOUT_SECONDS",
    "MAX_OUTPUT_BYTES",
    "AcceptanceCategory",
    "AcceptanceReport",
    "classify_gateway_status",
    "inspect_tailscale",
    "main",
    "parse_args",
]
