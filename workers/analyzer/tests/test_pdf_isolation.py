"""Synthetic-only tests for parser isolation and temporary workspaces."""

from __future__ import annotations

import json
import multiprocessing
import os
import resource
import socket
import stat
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from familycare_worker.pdf import isolation, limits
from familycare_worker.pdf.isolation import ParseOutcome, run_isolated_parser
from familycare_worker.pdf.workspace import WorkspaceCleanupError, create_workspace

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="Linux descriptor and rlimit contract"
)


def _read_descriptor(source_fd: int, settings_json: str) -> dict[str, Any]:
    return {
        "settings_json": settings_json,
        "settings": json.loads(settings_json),
        "source": os.read(source_fd, 64).decode("ascii"),
    }


def _sleeping_parser(source_fd: int, settings_json: str) -> None:
    del source_fd, settings_json
    time.sleep(10)


def _oversized_parser(source_fd: int, settings_json: str) -> str:
    del source_fd, settings_json
    return "x" * (limits.MAX_OUTPUT_BYTES + 1)


def _raising_parser(source_fd: int, settings_json: str) -> None:
    del source_fd, settings_json
    raise RuntimeError("synthetic-marker /private/path/document-body")


def _printing_raising_parser(source_fd: int, settings_json: str) -> None:
    del source_fd, settings_json
    print("synthetic-private-document-body", flush=True)
    raise RuntimeError("synthetic-private-document-body")


def _non_json_parser(source_fd: int, settings_json: str) -> object:
    del source_fd, settings_json
    return object()


def _observe_child_limits(source_fd: int, settings_json: str) -> dict[str, tuple[int, int]]:
    del source_fd, settings_json
    return {
        "cpu": resource.getrlimit(resource.RLIMIT_CPU),
        "address_space": resource.getrlimit(resource.RLIMIT_AS),
        "file_size": resource.getrlimit(resource.RLIMIT_FSIZE),
        "open_descriptors": resource.getrlimit(resource.RLIMIT_NOFILE),
    }


def _observe_unrelated_descriptor(source_fd: int, settings_json: str) -> bool:
    del source_fd
    settings = json.loads(settings_json)
    unrelated_fd = settings["unrelated_fd"]
    try:
        metadata = os.fstat(unrelated_fd)
    except OSError:
        return False
    return (metadata.st_dev, metadata.st_ino) == (
        settings["unrelated_device"],
        settings["unrelated_inode"],
    )


def _make_source(tmp_path: Path) -> int:
    source = tmp_path / "synthetic-source.bin"
    source.write_bytes(b"synthetic-descriptor-content")
    return os.open(source, os.O_RDONLY | os.O_CLOEXEC)


def test_limits_are_exact() -> None:
    assert limits.MAX_INPUT_BYTES == 25 * 1024 * 1024
    assert limits.MAX_PDF_PAGES == 500
    assert limits.PARENT_WALL_TIMEOUT_SECONDS == 120
    assert limits.CHILD_CPU_LIMIT_SECONDS == 90
    assert limits.CHILD_ADDRESS_SPACE_BYTES == 1536 * 1024 * 1024
    assert limits.CHILD_FILE_SIZE_BYTES == 64 * 1024 * 1024
    assert limits.MAX_OUTPUT_BYTES == 64 * 1024 * 1024
    assert limits.MAX_SETTINGS_BYTES == 64 * 1024
    assert limits.CHILD_OPEN_DESCRIPTORS == 64
    assert limits.WORKSPACE_DIRECTORY_MODE == 0o700
    assert limits.WORKSPACE_FILE_MODE == 0o600


def test_apply_resource_limits_uses_exact_order_and_values(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, tuple[int, int]]] = []

    def record(which: int, values: tuple[int, int]) -> None:
        calls.append((which, values))

    monkeypatch.setattr(limits.resource, "setrlimit", record)

    limits.apply_resource_limits()

    assert calls == [
        (limits.resource.RLIMIT_CPU, (90, 90)),
        (limits.resource.RLIMIT_AS, (1536 * 1024 * 1024, 1536 * 1024 * 1024)),
        (limits.resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024)),
        (limits.resource.RLIMIT_NOFILE, (64, 64)),
    ]


def test_workspace_has_neutral_0700_directory_and_exclusive_0600_files(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)

    assert workspace.path.parent == tmp_path
    assert workspace.path.name.startswith("job-")
    assert stat.S_IMODE(workspace.path.stat().st_mode) == 0o700

    with workspace.create_file("result.bin") as handle:
        handle.write(b"synthetic-output")

    output = workspace.path / "result.bin"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        workspace.create_file("result.bin")
    with pytest.raises(ValueError):
        workspace.create_file("../escape.bin")

    assert workspace.close_and_cleanup() is True
    assert not workspace.path.exists()


def test_workspace_context_cleans_after_exception(tmp_path: Path) -> None:
    workspace_path: Path
    with (
        pytest.raises(RuntimeError, match="synthetic-marker"),
        create_workspace(tmp_path) as workspace,
    ):
        workspace_path = workspace.path
        raise RuntimeError("synthetic-marker")

    assert not workspace_path.exists()


def test_workspace_cleanup_failure_surfaces_sanitized_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = create_workspace(tmp_path)

    def fail_cleanup(path: str | bytes | os.PathLike[str] | os.PathLike[bytes]) -> None:
        del path
        raise OSError("synthetic-marker /private/path")

    monkeypatch.setattr("familycare_worker.pdf.workspace.shutil.rmtree", fail_cleanup)

    with pytest.raises(WorkspaceCleanupError) as raised:
        workspace.close_and_cleanup()

    assert raised.value.code == "TEMP_CLEANUP_FAILED"
    assert "private" not in str(raised.value)
    assert "synthetic-marker" not in str(raised.value)


def test_child_receives_only_descriptor_and_canonical_settings(tmp_path: Path) -> None:
    fd = _make_source(tmp_path)
    os.lseek(fd, 5, os.SEEK_SET)
    original_offset = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        outcome = run_isolated_parser(fd, '{"z": 2, "a": 1}', parser=_read_descriptor)
    finally:
        assert os.lseek(fd, 0, os.SEEK_CUR) == original_offset
        os.close(fd)

    assert isinstance(outcome, ParseOutcome)
    assert outcome.success is True
    assert outcome.result == {
        "settings_json": '{"a":1,"z":2}',
        "settings": {"a": 1, "z": 2},
        "source": "synthetic-descriptor-content",
    }


def test_child_observes_all_four_resource_limits(tmp_path: Path) -> None:
    fd = _make_source(tmp_path)
    try:
        outcome = run_isolated_parser(fd, "{}", parser=_observe_child_limits)
    finally:
        os.close(fd)

    assert outcome.success is True
    assert outcome.result == {
        "cpu": [limits.CHILD_CPU_LIMIT_SECONDS, limits.CHILD_CPU_LIMIT_SECONDS],
        "address_space": [
            limits.CHILD_ADDRESS_SPACE_BYTES,
            limits.CHILD_ADDRESS_SPACE_BYTES,
        ],
        "file_size": [limits.CHILD_FILE_SIZE_BYTES, limits.CHILD_FILE_SIZE_BYTES],
        "open_descriptors": [
            limits.CHILD_OPEN_DESCRIPTORS,
            limits.CHILD_OPEN_DESCRIPTORS,
        ],
    }


def test_malformed_settings_are_rejected_before_child_start(tmp_path: Path) -> None:
    fd = _make_source(tmp_path)
    child_called = multiprocessing.Value("i", 0)

    def parser(source_fd: int, settings_json: str) -> None:
        del source_fd, settings_json
        child_called.value = 1

    try:
        outcome = run_isolated_parser(fd, "{not-json", parser=parser)
    finally:
        os.close(fd)

    assert outcome.success is False
    assert outcome.error_code == "INVALID_REQUEST"
    assert child_called.value == 0


@pytest.mark.parametrize(
    "settings_json",
    [
        '{"password":"synthetic-secret"}',
        '{"nested":{"source_path":"synthetic/private.pdf"}}',
        '{"value":NaN}',
        "[]",
        ' {"profile":"quality-v1"}' + (" " * (limits.MAX_SETTINGS_BYTES + 1)),
    ],
)
def test_forbidden_or_noncanonical_settings_are_rejected(
    tmp_path: Path,
    settings_json: str,
) -> None:
    fd = _make_source(tmp_path)
    try:
        outcome = run_isolated_parser(fd, settings_json, parser=_read_descriptor)
    finally:
        os.close(fd)

    assert outcome.success is False
    assert outcome.error_code == "INVALID_REQUEST"


def test_write_only_descriptor_is_rejected_before_child_start(tmp_path: Path) -> None:
    source = tmp_path / "synthetic-output.bin"
    fd = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        outcome = run_isolated_parser(fd, "{}", parser=_read_descriptor)
    finally:
        os.close(fd)

    assert outcome.success is False
    assert outcome.error_code == "INVALID_REQUEST"


def test_child_does_not_inherit_unrelated_parent_descriptor(tmp_path: Path) -> None:
    source_fd = _make_source(tmp_path)
    unrelated = tmp_path / "synthetic-unrelated.bin"
    unrelated.write_bytes(b"synthetic-unrelated")
    unrelated_fd = os.open(unrelated, os.O_RDONLY | os.O_CLOEXEC)
    metadata = os.fstat(unrelated_fd)
    settings = json.dumps(
        {
            "unrelated_device": metadata.st_dev,
            "unrelated_fd": unrelated_fd,
            "unrelated_inode": metadata.st_ino,
        }
    )
    try:
        outcome = run_isolated_parser(
            source_fd,
            settings,
            parser=_observe_unrelated_descriptor,
        )
    finally:
        os.close(unrelated_fd)
        os.close(source_fd)

    assert outcome.success is True
    assert outcome.result is False


def test_child_does_not_inherit_parent_socket(tmp_path: Path) -> None:
    source_fd = _make_source(tmp_path)
    left, right = socket.socketpair()
    metadata = os.fstat(left.fileno())
    settings = json.dumps(
        {
            "unrelated_device": metadata.st_dev,
            "unrelated_fd": left.fileno(),
            "unrelated_inode": metadata.st_ino,
        }
    )
    try:
        outcome = run_isolated_parser(
            source_fd,
            settings,
            parser=_observe_unrelated_descriptor,
        )
    finally:
        left.close()
        right.close()
        os.close(source_fd)

    assert outcome.success is True
    assert outcome.result is False


def test_timeout_terminates_and_joins_parser_child(tmp_path: Path) -> None:
    fd = _make_source(tmp_path)
    started = time.monotonic()
    try:
        outcome = run_isolated_parser(
            fd,
            "{}",
            parser=_sleeping_parser,
            wall_timeout_seconds=0.1,
        )
    finally:
        os.close(fd)

    assert outcome.success is False
    assert outcome.error_code == "EXTRACTION_TIMEOUT"
    assert time.monotonic() - started < 2


def test_parent_progress_callback_can_cancel_and_reap_parser_child(tmp_path: Path) -> None:
    fd = _make_source(tmp_path)
    callbacks = 0
    started = time.monotonic()

    def cancel() -> bool:
        nonlocal callbacks
        callbacks += 1
        return False

    try:
        outcome = run_isolated_parser(
            fd,
            "{}",
            parser=_sleeping_parser,
            on_progress=cancel,
            progress_interval_seconds=0.01,
        )
    finally:
        os.close(fd)

    assert outcome.success is False
    assert outcome.error_code is None
    assert outcome.metadata == {"cancelled": True}
    assert callbacks == 1
    assert time.monotonic() - started < 2


def test_oversized_serialized_result_is_rejected(tmp_path: Path) -> None:
    fd = _make_source(tmp_path)
    try:
        outcome = run_isolated_parser(fd, "{}", parser=_oversized_parser)
    finally:
        os.close(fd)

    assert outcome.success is False
    assert outcome.error_code == "RESOURCE_LIMIT_EXCEEDED"


def test_child_exception_is_sanitized(tmp_path: Path) -> None:
    fd = _make_source(tmp_path)
    try:
        outcome = run_isolated_parser(fd, "{}", parser=_raising_parser)
    finally:
        os.close(fd)

    assert outcome.success is False
    assert outcome.error_code == "PDF_CORRUPT"
    assert outcome.error_message == "parser failed"
    assert "private" not in str(outcome)
    assert "document-body" not in str(outcome)


def test_child_parser_output_does_not_reach_parent_logs(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    fd = _make_source(tmp_path)
    try:
        outcome = run_isolated_parser(fd, "{}", parser=_printing_raising_parser)
    finally:
        os.close(fd)

    captured = capfd.readouterr()
    assert outcome.error_code == "PDF_CORRUPT"
    assert "synthetic-private-document-body" not in captured.out
    assert "synthetic-private-document-body" not in captured.err


def test_non_json_result_is_rejected_without_leaking_object_details(tmp_path: Path) -> None:
    fd = _make_source(tmp_path)
    try:
        outcome = run_isolated_parser(fd, "{}", parser=_non_json_parser)
    finally:
        os.close(fd)

    assert outcome.success is False
    assert outcome.error_code == "PDF_CORRUPT"
    assert outcome.error_message == "parser failed"


def test_malformed_ipc_is_rejected_without_unpickling() -> None:
    assert isolation._decode_message(b"not-json").error_code == "PDF_CORRUPT"
    assert isolation._decode_message(b"\x80\x04not-a-pickle").error_code == "PDF_CORRUPT"
