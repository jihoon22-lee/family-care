"""Administrative provisioning tests without real account values."""

from __future__ import annotations

import io
from collections.abc import Iterator
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from familycare_api.identity.cli import (
    AdminProvisioner,
    AdminProvisioningError,
    build_parser,
    read_confirmed_password,
)
from familycare_api.identity.password import PasswordHashError


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None


class _ProvisioningConnection:
    def __init__(self) -> None:
        self.household_id = UUID("00000000-0000-4000-8000-000000000001")
        self.users: list[dict[str, Any]] = []

    def __enter__(self) -> _ProvisioningConnection:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    def execute(self, query: str, parameters: tuple[Any, ...] = ()) -> _Result:
        normalized = " ".join(query.split())
        if normalized.startswith("SELECT id FROM household_spaces"):
            return _Result([{"id": self.household_id}])
        if normalized.startswith("SELECT count(*) AS count FROM app_users"):
            return _Result([{"count": len(self.users)}])
        if normalized.startswith("INSERT INTO app_users"):
            household_id, username, display_name, password_hash = parameters
            row = {
                "id": uuid4(),
                "household_space_id": household_id,
                "username": username,
                "display_name": display_name,
                "password_hash": password_hash,
                "is_active": True,
            }
            self.users.append(row)
            return _Result(
                [
                    {
                        key: row[key]
                        for key in (
                            "id",
                            "household_space_id",
                            "username",
                            "display_name",
                            "is_active",
                        )
                    }
                ]
            )
        raise AssertionError(normalized)


def test_cli_has_no_raw_password_argument() -> None:
    help_text = build_parser().format_help()

    assert "--password" not in help_text
    assert "PASSWORD=" not in help_text


def test_cli_rejects_password_option_without_echoing_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = build_parser()
    raw = "synthetic-auth-secret-a"

    with pytest.raises(SystemExit):
        parser.parse_args(["create", "--username", "admin-a", "--password", raw])

    captured = capsys.readouterr()
    assert raw not in captured.out
    assert raw not in captured.err


def test_password_reader_confirms_from_injected_prompt_and_discards_mismatch() -> None:
    answers: Iterator[str] = iter(["synthetic-auth-secret-a", "synthetic-auth-secret-b"])

    with pytest.raises(PasswordHashError, match="PASSWORD_MISMATCH"):
        read_confirmed_password(prompt=lambda _label: next(answers))


def test_password_reader_accepts_stdin_without_printing_secret() -> None:
    stream = io.StringIO("synthetic-auth-secret-a\nsynthetic-auth-secret-a\n")
    output = io.StringIO()

    value = read_confirmed_password(stdin=stream, output=output)

    assert value == "synthetic-auth-secret-a"
    assert value not in output.getvalue()


def test_third_active_admin_is_rejected_without_persisting_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _ProvisioningConnection()
    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: connection)
    provisioner = AdminProvisioner("postgresql://synthetic.invalid/familycare")

    provisioner.create("admin-a", "synthetic-auth-secret-a", "Admin A")
    provisioner.create("admin-b", "synthetic-auth-secret-b", "Admin B")
    with pytest.raises(AdminProvisioningError, match="ADMIN_LIMIT_REACHED"):
        provisioner.create("admin-c", "synthetic-auth-secret-c", "Admin C")

    assert len(connection.users) == 2
    persisted = repr(connection.users)
    assert "synthetic-auth-secret" not in persisted
    assert all(row["password_hash"].startswith("$argon2id$") for row in connection.users)
