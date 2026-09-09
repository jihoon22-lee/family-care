from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import private_runtime_postgres as postgres


def test_dump_uses_environment_credentials_and_private_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        assert kwargs["env"]["PGPASSWORD"] == "synthetic-only"  # type: ignore[index]
        kwargs["stdout"].write(b"PGDMPsynthetic")  # type: ignore[union-attr]
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(postgres.subprocess, "run", run)
    destination = tmp_path / "database.pgcustom"
    postgres.PostgresTools("synthetic-db", "postgresql://test:synthetic-only@localhost/test").dump(
        destination, snapshot="00000001-00000002-1"
    )
    assert destination.stat().st_mode & 0o777 == 0o600
    assert "synthetic-only" not in repr(calls)
    assert "--snapshot=00000001-00000002-1" in calls[0]
    assert "timeout" in calls[0]


@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_failed_dump_removes_only_new_file_and_sanitizes_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 1, stderr=b"synthetic private detail")
        return subprocess.CompletedProcess(command, 1, stderr=b"synthetic private detail")

    monkeypatch.setattr(postgres.subprocess, "run", run)
    destination = tmp_path / "database.pgcustom"
    with pytest.raises(postgres.PostgresToolError, match="^TRANSITION_DATABASE_TOOL_FAILED$"):
        postgres.PostgresTools("synthetic-db", "postgresql://test@localhost/test").dump(
            destination, snapshot="00000001-00000002-1"
        )
    assert not destination.exists()


def test_dump_refuses_existing_file_without_removing_it(tmp_path: Path) -> None:
    destination = tmp_path / "database.pgcustom"
    destination.write_bytes(b"synthetic existing")
    with pytest.raises(postgres.PostgresToolError):
        postgres.PostgresTools("synthetic-db", "postgresql://test@localhost/test").dump(
            destination, snapshot="00000001-00000002-1"
        )
    assert destination.read_bytes() == b"synthetic existing"


def test_restore_refuses_nonempty_database_before_invoking_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dump = tmp_path / "database.pgcustom"
    dump.write_bytes(b"PGDMPsynthetic")
    dump.chmod(0o600)
    monkeypatch.setattr(postgres, "_require_empty_database", lambda _: False)
    with pytest.raises(postgres.PostgresToolError, match="^TRANSITION_TARGET_NOT_EMPTY$"):
        postgres.PostgresTools("synthetic-db", "postgresql://test@localhost/test").restore(dump)
