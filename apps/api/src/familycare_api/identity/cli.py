"""TTY/stdin-only local administrator provisioning."""

from __future__ import annotations

import argparse
import getpass
import hmac
import os
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Never, TextIO, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from familycare_api.identity.password import PasswordHasher, PasswordHashError

_USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")


class AdminProvisioningError(RuntimeError):
    """Stable administrative error that never embeds account or secret values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _SafeArgumentParser(argparse.ArgumentParser):
    """Reject invalid argv without reflecting unknown values."""

    def error(self, message: str) -> Never:
        del message
        self.print_usage(sys.stderr)
        self.exit(2, "familycare-admin: invalid arguments\n")


@dataclass(frozen=True)
class ProvisionedAdmin:
    id: UUID
    household_space_id: UUID
    username: str
    display_name: str
    is_active: bool


def _database_url(value: str) -> str:
    if not value:
        raise AdminProvisioningError("DATABASE_URL_REQUIRED")
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _username(value: str) -> str:
    normalized = value.strip().casefold()
    if _USERNAME_PATTERN.fullmatch(normalized) is None:
        raise AdminProvisioningError("INVALID_USERNAME")
    return normalized


def _display_name(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 160 or any(ord(char) < 32 for char in normalized):
        raise AdminProvisioningError("INVALID_DISPLAY_NAME")
    return normalized


class AdminProvisioner:
    """Provision at most two equal administrators in the sole active household."""

    def __init__(self, database_url: str, *, password_hasher: PasswordHasher | None = None) -> None:
        self.database_url = _database_url(database_url)
        self.password_hasher = password_hasher or PasswordHasher()

    @staticmethod
    def _household_id(connection: Any) -> UUID:
        rows = connection.execute(
            """
            SELECT id FROM household_spaces
            WHERE deleted_at IS NULL
            ORDER BY id
            FOR UPDATE
            """
        ).fetchall()
        if len(rows) != 1:
            raise AdminProvisioningError("HOUSEHOLD_NOT_READY")
        return cast(UUID, rows[0]["id"])

    def create(
        self,
        username: str,
        raw_password: str,
        display_name: str,
    ) -> ProvisionedAdmin:
        normalized_username = _username(username)
        normalized_display = _display_name(display_name)
        encoded_hash = self.password_hasher.hash(raw_password)
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                household_id = self._household_id(connection)
                active_count = connection.execute(
                    "SELECT count(*) AS count FROM app_users "
                    "WHERE household_space_id = %s AND is_active",
                    (household_id,),
                ).fetchone()
                if active_count is None or int(active_count["count"]) >= 2:
                    raise AdminProvisioningError("ADMIN_LIMIT_REACHED")
                row = connection.execute(
                    """
                    INSERT INTO app_users (
                        household_space_id, username, display_name, password_hash
                    ) VALUES (%s, %s, %s, %s)
                    RETURNING id, household_space_id, username, display_name, is_active
                    """,
                    (household_id, normalized_username, normalized_display, encoded_hash),
                ).fetchone()
        except AdminProvisioningError:
            raise
        except psycopg.errors.UniqueViolation:
            raise AdminProvisioningError("ADMIN_USERNAME_EXISTS") from None
        except psycopg.Error:
            raise AdminProvisioningError("ADMIN_STORE_UNAVAILABLE") from None
        if row is None:
            raise AdminProvisioningError("ADMIN_STORE_UNAVAILABLE")
        return ProvisionedAdmin(**row)

    def set_password(self, username: str, raw_password: str) -> None:
        normalized_username = _username(username)
        encoded_hash = self.password_hasher.hash(raw_password)
        now = datetime.now(UTC)
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    UPDATE app_users
                    SET password_hash = %s, updated_at = %s
                    WHERE username = %s AND is_active
                    RETURNING id
                    """,
                    (encoded_hash, now, normalized_username),
                ).fetchone()
                if row is None:
                    raise AdminProvisioningError("ADMIN_NOT_FOUND")
                connection.execute(
                    "UPDATE app_sessions SET revoked_at = %s "
                    "WHERE app_user_id = %s AND revoked_at IS NULL",
                    (now, row["id"]),
                )
        except AdminProvisioningError:
            raise
        except psycopg.Error:
            raise AdminProvisioningError("ADMIN_STORE_UNAVAILABLE") from None

    def disable(self, username: str) -> None:
        normalized_username = _username(username)
        now = datetime.now(UTC)
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    UPDATE app_users
                    SET is_active = false, deactivated_at = %s, updated_at = %s
                    WHERE username = %s AND is_active
                    RETURNING id
                    """,
                    (now, now, normalized_username),
                ).fetchone()
                if row is None:
                    raise AdminProvisioningError("ADMIN_NOT_FOUND")
                connection.execute(
                    "UPDATE app_sessions SET revoked_at = %s "
                    "WHERE app_user_id = %s AND revoked_at IS NULL",
                    (now, row["id"]),
                )
        except AdminProvisioningError:
            raise
        except psycopg.Error:
            raise AdminProvisioningError("ADMIN_STORE_UNAVAILABLE") from None


def read_confirmed_password(
    *,
    prompt: Callable[[str], str] | None = None,
    stdin: TextIO | None = None,
    output: TextIO | None = None,
) -> str:
    """Read a password twice without accepting it from argv or the environment."""

    stream = stdin or sys.stdin
    target = output or sys.stderr
    if prompt is not None:
        first = prompt("Password: ")
        second = prompt("Confirm password: ")
    elif stream.isatty():
        first = getpass.getpass("Password: ", stream=target)
        second = getpass.getpass("Confirm password: ", stream=target)
    else:
        first = stream.readline().rstrip("\r\n")
        second = stream.readline().rstrip("\r\n")
    if not hmac.compare_digest(first, second):
        raise PasswordHashError("PASSWORD_MISMATCH")
    PasswordHasher.validate(first)
    return first


def build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(prog="familycare-admin")
    subparsers = parser.add_subparsers(
        dest="command", required=True, parser_class=_SafeArgumentParser
    )

    create = subparsers.add_parser("create", help="create a local administrator")
    create.add_argument("--username", required=True)
    create.add_argument("--display-name")

    set_password = subparsers.add_parser("set-password", help="replace an administrator hash")
    set_password.add_argument("--username", required=True)

    disable = subparsers.add_parser("disable", help="disable an administrator")
    disable.add_argument("--username", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        provisioner = AdminProvisioner(os.getenv("FAMILYCARE_DATABASE_URL", ""))
        if args.command == "create":
            raw_password = read_confirmed_password()
            try:
                provisioner.create(
                    args.username,
                    raw_password,
                    display_name=args.display_name or args.username,
                )
            finally:
                del raw_password
        elif args.command == "set-password":
            raw_password = read_confirmed_password()
            try:
                provisioner.set_password(args.username, raw_password)
            finally:
                del raw_password
        else:
            provisioner.disable(args.username)
        return 0
    except (AdminProvisioningError, PasswordHashError) as error:
        print(f"familycare-admin: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AdminProvisioner",
    "AdminProvisioningError",
    "ProvisionedAdmin",
    "build_parser",
    "main",
    "read_confirmed_password",
]
