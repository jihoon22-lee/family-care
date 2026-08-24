"""Dedicated parser-child supervision with descriptor-only input."""

from __future__ import annotations

import fcntl
import json
import math
import multiprocessing
import os
import stat
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, cast

from .errors import IntakeErrorCode
from .limits import (
    MAX_OUTPUT_BYTES,
    MAX_SETTINGS_BYTES,
    PARENT_WALL_TIMEOUT_SECONDS,
    ResourceLimitApplicationError,
    apply_resource_limits,
)

Parser = Callable[[int, str], object]
ProgressCallback = Callable[[], bool]

_FORBIDDEN_SETTINGS_KEYS = frozenset(
    {
        "absolute_path",
        "document_body",
        "external_url",
        "password",
        "raw_pdf",
        "raw_pdf_bytes",
        "source_key",
        "source_path",
        "url",
    }
)


@dataclass(frozen=True)
class ParseOutcome:
    """Sanitized success or failure returned by the parser supervisor."""

    success: bool
    result: object | None = None
    error_code: IntakeErrorCode | None = None
    error_message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Compatibility alias for an ok result flag."""

        return self.success

    @property
    def error(self) -> IntakeErrorCode | None:
        """Compatibility alias for the stable error code."""

        return self.error_code


def _failure(code: str | IntakeErrorCode, message: str | None = None) -> ParseOutcome:
    try:
        safe_code = IntakeErrorCode(code)
    except TypeError, ValueError:
        safe_code = IntakeErrorCode.PDF_CORRUPT
    return ParseOutcome(
        success=False,
        error_code=safe_code,
        error_message=message,
    )


def _reject_non_finite(value: str) -> None:
    raise ValueError(value)


def _contains_forbidden_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            key in _FORBIDDEN_SETTINGS_KEYS or _contains_forbidden_key(nested)
            for key, nested in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def _canonical_settings(settings_json: str) -> str | None:
    if not isinstance(settings_json, str) or len(settings_json) > MAX_SETTINGS_BYTES:
        return None
    try:
        if len(settings_json.encode("utf-8")) > MAX_SETTINGS_BYTES:
            return None
        parsed = json.loads(settings_json, parse_constant=_reject_non_finite)
        if not isinstance(parsed, dict) or _contains_forbidden_key(parsed):
            return None
        return json.dumps(
            parsed,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except MemoryError, RecursionError, TypeError, ValueError, json.JSONDecodeError:
        return None


def _read_only_descriptor(source_fd: int) -> bool:
    if not isinstance(source_fd, int) or source_fd < 0:
        return False
    try:
        flags = fcntl.fcntl(source_fd, fcntl.F_GETFL)
        os.fstat(source_fd)
    except OSError:
        return False
    return (flags & os.O_ACCMODE) == os.O_RDONLY


def _default_parser(source_fd: int, settings_json: str) -> object:
    """Lazy bridge for the extractor that lands in a later branch."""

    extractor = import_module("familycare_worker.pdf.extractor")
    parse = cast(Parser, extractor.parse_local_pdf)
    return parse(source_fd, settings_json)


def parse_local_pdf(source_fd: int, settings_json: str) -> object:
    """Call the later extractor lazily without importing parser dependencies here."""

    return _default_parser(source_fd, settings_json)


def _safe_error_code(error: BaseException) -> IntakeErrorCode:
    if isinstance(error, ResourceLimitApplicationError):
        return IntakeErrorCode.RESOURCE_LIMIT_EXCEEDED
    candidate = getattr(error, "code", None)
    if isinstance(candidate, IntakeErrorCode):
        return candidate
    if isinstance(candidate, str):
        try:
            return IntakeErrorCode(candidate)
        except ValueError:
            pass
    return IntakeErrorCode.PDF_CORRUPT


def _canonical_json_bytes(value: object) -> bytes:
    """Serialize only JSON values using one stable UTF-8 representation."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _send_message(connection: Any, envelope: dict[str, object]) -> None:
    try:
        payload = _canonical_json_bytes(envelope)
    except MemoryError, RecursionError, TypeError, ValueError, OverflowError:
        payload = _canonical_json_bytes(
            {
                "success": False,
                "error_code": IntakeErrorCode.PDF_CORRUPT,
                "error_message": "parser failed",
            }
        )
    if len(payload) > MAX_OUTPUT_BYTES:
        payload = _canonical_json_bytes(
            {
                "success": False,
                "error_code": IntakeErrorCode.RESOURCE_LIMIT_EXCEEDED,
                "error_message": "result exceeds output limit",
            }
        )
    with suppress(BrokenPipeError, EOFError, OSError, ValueError):
        connection.send_bytes(payload)


def _child_entry(
    connection: Any,
    source_fd: int,
    settings_json: str,
    parser: Parser,
) -> None:
    """Apply limits, invoke the parser, and return only a bounded envelope."""

    try:
        _close_unrelated_descriptors({source_fd, connection.fileno()})
        _silence_child_output()
        apply_resource_limits()
        os.lseek(source_fd, 0, os.SEEK_SET)
        result = parser(source_fd, settings_json)
        _send_message(connection, {"success": True, "result": result})
    except BaseException as error:
        _send_message(
            connection,
            {
                "success": False,
                "error_code": _safe_error_code(error),
                "error_message": "parser failed",
            },
        )
    finally:
        with suppress(OSError):
            os.close(source_fd)
        with suppress(OSError):
            connection.close()


def _decode_message(payload: bytes) -> ParseOutcome:
    if len(payload) > MAX_OUTPUT_BYTES:
        return _failure("RESOURCE_LIMIT_EXCEEDED")
    try:
        decoded = payload.decode("utf-8")
        envelope = json.loads(decoded, parse_constant=_reject_non_finite)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        MemoryError,
        RecursionError,
        TypeError,
        ValueError,
    ):
        return _failure(IntakeErrorCode.PDF_CORRUPT)
    if not isinstance(envelope, dict):
        return _failure("PDF_CORRUPT")
    if envelope.get("success") is True:
        return ParseOutcome(success=True, result=envelope.get("result"))
    code = envelope.get("error_code")
    if not isinstance(code, str):
        code = IntakeErrorCode.PDF_CORRUPT
    return _failure(code, "parser failed")


def _reap_child(child: Any) -> None:
    """Reap a started child without allowing termination to block forever."""

    try:
        child.join(timeout=0.25)
        if child.is_alive():
            child.terminate()
            child.join(timeout=0.5)
            if child.is_alive():
                child.kill()
                child.join(timeout=0.5)
    except AssertionError, AttributeError, OSError, ValueError:
        return


def _close_unrelated_descriptors(allowed: set[int]) -> None:
    """Drop inherited application files and sockets before parser code runs.

    Fork control pipes remain open so Python can supervise and reap the child.
    The Linux-only parser boundary requires procfs; failing closed is safer than
    letting a parser inherit database, network, or unrelated document handles.
    """

    try:
        descriptor_names = os.listdir("/proc/self/fd")
    except OSError:
        raise ResourceLimitApplicationError from None
    for name in descriptor_names:
        if not name.isdecimal():
            continue
        descriptor = int(name)
        if descriptor < 3 or descriptor in allowed:
            continue
        try:
            mode = os.fstat(descriptor).st_mode
        except OSError:
            continue
        if not stat.S_ISFIFO(mode):
            with suppress(OSError):
                os.close(descriptor)


def _silence_child_output() -> None:
    """Prevent parser and library output from reaching Worker logs."""

    null_fd = -1
    try:
        null_fd = os.open(os.devnull, os.O_WRONLY | os.O_CLOEXEC)
        os.dup2(null_fd, 1)
        os.dup2(null_fd, 2)
    except OSError:
        raise ResourceLimitApplicationError from None
    finally:
        if null_fd not in {-1, 1, 2}:
            with suppress(OSError):
                os.close(null_fd)


def run_isolated_parser(
    source_fd: int,
    settings_json: str,
    *,
    parser: Parser | None = None,
    wall_timeout_seconds: float = PARENT_WALL_TIMEOUT_SECONDS,
    on_progress: ProgressCallback | None = None,
    progress_interval_seconds: float = 30.0,
) -> ParseOutcome:
    """Run an injectable parser in a dedicated child with descriptor-only input."""

    canonical_settings = _canonical_settings(settings_json)
    if canonical_settings is None:
        return _failure("INVALID_REQUEST")
    if not _read_only_descriptor(source_fd):
        return _failure("INVALID_REQUEST")
    if (
        not math.isfinite(wall_timeout_seconds)
        or wall_timeout_seconds <= 0
        or not math.isfinite(progress_interval_seconds)
        or progress_interval_seconds <= 0
        or (on_progress is not None and not callable(on_progress))
    ):
        return _failure("INVALID_REQUEST")
    try:
        source_offset = os.lseek(source_fd, 0, os.SEEK_CUR)
    except OSError:
        source_offset = None

    try:
        context = multiprocessing.get_context("fork")
    except ValueError:
        return _failure("RESOURCE_LIMIT_EXCEEDED")

    try:
        receive, send = context.Pipe(duplex=False)
    except OSError, RuntimeError, ValueError:
        return _failure("RESOURCE_LIMIT_EXCEEDED")
    try:
        child_fd = os.dup(source_fd)
    except OSError:
        with suppress(OSError):
            receive.close()
        with suppress(OSError):
            send.close()
        return _failure("RESOURCE_LIMIT_EXCEEDED")
    try:
        child = context.Process(
            target=_child_entry,
            args=(
                send,
                child_fd,
                canonical_settings,
                parser if parser is not None else parse_local_pdf,
            ),
        )
    except OSError, RuntimeError, ValueError:
        with suppress(OSError):
            os.close(child_fd)
        with suppress(OSError):
            receive.close()
        with suppress(OSError):
            send.close()
        return _failure("RESOURCE_LIMIT_EXCEEDED")
    try:
        child.start()
    except OSError, RuntimeError, ValueError:
        _reap_child(child)
        with suppress(OSError):
            os.close(child_fd)
        with suppress(OSError):
            receive.close()
        with suppress(OSError):
            send.close()
        return _failure("RESOURCE_LIMIT_EXCEEDED")
    finally:
        with suppress(OSError):
            os.close(child_fd)
        with suppress(OSError):
            send.close()

    payload: bytes | None = None
    deadline = time.monotonic() + wall_timeout_seconds
    next_progress = time.monotonic() + progress_interval_seconds
    try:
        while True:
            now = time.monotonic()
            try:
                ready = receive.poll(
                    min(
                        0.05,
                        max(0.0, deadline - now),
                        max(0.0, next_progress - now),
                    )
                )
            except OSError, ValueError:
                return _failure("RESOURCE_LIMIT_EXCEEDED")
            if ready:
                try:
                    payload = receive.recv_bytes(MAX_OUTPUT_BYTES)
                except EOFError, OSError, ValueError:
                    return _failure("RESOURCE_LIMIT_EXCEEDED")
                break
            if not child.is_alive():
                break
            now = time.monotonic()
            if now >= deadline:
                return _failure("EXTRACTION_TIMEOUT")
            if on_progress is not None and now >= next_progress:
                try:
                    should_continue = on_progress()
                except Exception:
                    should_continue = False
                if not should_continue:
                    return ParseOutcome(success=False, metadata={"cancelled": True})
                next_progress = now + progress_interval_seconds

        if payload is None:
            return _failure("PDF_CORRUPT")
        return _decode_message(payload)
    finally:
        _reap_child(child)
        if source_offset is not None:
            with suppress(OSError):
                os.lseek(source_fd, source_offset, os.SEEK_SET)
        with suppress(OSError):
            receive.close()


__all__ = [
    "ParseOutcome",
    "Parser",
    "ProgressCallback",
    "parse_local_pdf",
    "run_isolated_parser",
]
