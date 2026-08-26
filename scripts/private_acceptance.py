#!/usr/bin/env python3
"""Read-only, redacted checks for the private local runtime."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import selectors
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
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
    TAILSCALE_SERVE_EMPTY = "tailscale-serve-empty"
    TAILSCALE_SERVE_CONFIGURED = "tailscale-serve-configured"
    TAILSCALE_SERVE_GATEWAY_MATCH = "tailscale-serve-gateway-match"
    GATEWAY_UNREACHABLE = "gateway-unreachable"
    GATEWAY_REACHABLE = "gateway-reachable"
    APP_AUTH_REQUIRED = "app-auth-required"


@dataclass(frozen=True)
class AcceptanceReport:
    """A deliberately small report containing no command output or metadata."""

    category: AcceptanceCategory
    foreign_configuration_fingerprint: str | None = field(default=None, repr=False)


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


def _endpoint_port(endpoint: object) -> str | None:
    if not isinstance(endpoint, str) or ":" not in endpoint:
        return None
    candidate = endpoint.rsplit(":", 1)[-1]
    if not candidate.isdecimal():
        return None
    port = int(candidate)
    return candidate if 1 <= port <= 65535 else None


def _strip_expected_gateway(
    configuration: dict[str, Any],
    expected_target: str | None,
) -> tuple[dict[str, Any], int]:
    """Remove only the exact expected proxy while retaining foreign configuration."""

    scrubbed = copy.deepcopy(configuration)
    matches = 0

    def strip_config(config: dict[str, Any]) -> None:
        nonlocal matches
        matched_ports: set[str] = set()
        remaining_ports: set[str] = set()
        web = config.get("Web")
        if isinstance(web, dict):
            for endpoint in list(web):
                endpoint_config = web.get(endpoint)
                port = _endpoint_port(endpoint)
                if not isinstance(endpoint_config, dict):
                    if port is not None:
                        remaining_ports.add(port)
                    continue
                handlers = endpoint_config.get("Handlers")
                if not isinstance(handlers, dict):
                    if port is not None:
                        remaining_ports.add(port)
                    continue
                for route in list(handlers):
                    handler = handlers.get(route)
                    if (
                        expected_target is not None
                        and isinstance(handler, dict)
                        and handler.get("Proxy") == expected_target
                    ):
                        del handlers[route]
                        matches += 1
                        if port is not None:
                            matched_ports.add(port)
                if not handlers:
                    endpoint_config.pop("Handlers", None)
                    if not endpoint_config:
                        del web[endpoint]
                        continue
                if port is not None:
                    remaining_ports.add(port)
            if not web:
                config.pop("Web", None)

        tcp = config.get("TCP")
        if isinstance(tcp, dict):
            for port in matched_ports - remaining_ports:
                if tcp.get(port) == {"HTTPS": True}:
                    del tcp[port]
            if not tcp:
                config.pop("TCP", None)

        services = config.get("Services")
        if isinstance(services, dict):
            for service_name in list(services):
                service = services.get(service_name)
                if isinstance(service, dict):
                    strip_config(service)
                    if not service:
                        del services[service_name]
            if not services:
                config.pop("Services", None)

    strip_config(scrubbed)
    return scrubbed, matches


def _configuration_fingerprint(configuration: dict[str, Any]) -> str:
    canonical = json.dumps(
        configuration,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _classify_serve_json(
    stdout: str,
    *,
    expected_gateway_port: int | None,
) -> AcceptanceReport:
    try:
        payload: Any = json.loads(stdout)
    except json.JSONDecodeError, UnicodeDecodeError:
        return _report(AcceptanceCategory.TAILSCALE_OUTPUT_MALFORMED)
    if not isinstance(payload, dict):
        return _report(AcceptanceCategory.TAILSCALE_OUTPUT_MALFORMED)

    expected_target = (
        f"http://127.0.0.1:{expected_gateway_port}" if expected_gateway_port is not None else None
    )
    foreign_configuration, matches = _strip_expected_gateway(payload, expected_target)
    fingerprint = _configuration_fingerprint(foreign_configuration)
    if not payload:
        category = AcceptanceCategory.TAILSCALE_SERVE_EMPTY
    elif matches:
        category = AcceptanceCategory.TAILSCALE_SERVE_GATEWAY_MATCH
    else:
        category = AcceptanceCategory.TAILSCALE_SERVE_CONFIGURED
    return AcceptanceReport(
        category=category,
        foreign_configuration_fingerprint=fingerprint,
    )


def same_foreign_serve_configuration(
    before: AcceptanceReport,
    after: AcceptanceReport,
) -> bool:
    """Compare redacted in-memory fingerprints without exposing configuration."""

    fingerprint = before.foreign_configuration_fingerprint
    return fingerprint is not None and fingerprint == after.foreign_configuration_fingerprint


def inspect_tailscale(
    argv: Sequence[str],
    *,
    runner: Runner | None = None,
    expected_gateway_port: int | None = None,
) -> AcceptanceReport:
    """Run one exact read-only inspection and return only a stable category.

    Command validation happens before selecting or invoking the runner. The
    runner is injectable so unit tests never need a Tailscale installation.
    """

    command = tuple(argv)
    validate_tailscale_inspection_command(command)
    serve_command = ("tailscale", "serve", "status", "--json")
    if expected_gateway_port is not None and (
        isinstance(expected_gateway_port, bool)
        or not 1 <= expected_gateway_port <= 65535
        or command != serve_command
    ):
        raise ValueError("invalid-expected-gateway-port")

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
    if command == serve_command:
        return _classify_serve_json(
            stdout,
            expected_gateway_port=expected_gateway_port,
        )
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
    parser.add_argument("--expected-gateway-port", type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER, metavar="COMMAND")
    args = parser.parse_args(argv)
    if not args.command:
        parser.error("a read-only command is required")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = inspect_tailscale(
            args.command,
            expected_gateway_port=args.expected_gateway_port,
        )
    except ValueError:
        print("tailscale-command-not-read-only")
        return 2
    print(report.category.value)
    successful = {
        AcceptanceCategory.TAILSCALE_CONNECTED,
        AcceptanceCategory.TAILSCALE_SERVE_CONFIGURED,
        AcceptanceCategory.TAILSCALE_SERVE_EMPTY,
        AcceptanceCategory.TAILSCALE_SERVE_GATEWAY_MATCH,
    }
    return 0 if report.category in successful else 1


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
    "same_foreign_serve_configuration",
]
