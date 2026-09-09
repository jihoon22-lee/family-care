"""Bounded PostgreSQL tools for an explicitly selected local Docker database.

The caller owns writer quiescence, backup authentication and target identity.
Credentials stay in inherited environment variables; tool diagnostics are discarded.
Restore accepts only an empty target and uses one transaction. No database is created,
dropped, cleaned or migrated by this module.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

import psycopg
from psycopg.conninfo import conninfo_to_dict

from scripts.private_runtime_backup import (
    BackupContractError,
    _exclusive_private_writer,
    _private_reader,
    _require_new_private_destination,
    _require_outside_repository,
)


class PostgresToolError(RuntimeError):
    """A fixed operational code without database or source details."""


def _require_empty_database(database_url: str) -> bool:
    try:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            row = connection.execute(
                "SELECT NOT EXISTS (SELECT 1 FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname NOT IN ('pg_catalog','information_schema') "
                "AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p','v','m','S','f'))"
            ).fetchone()
            return row is not None and row[0] is True
    except psycopg.Error:
        raise PostgresToolError("TRANSITION_DATABASE_UNAVAILABLE") from None


@dataclass(frozen=True)
class PostgresTools:
    container: str = field(repr=False)
    database_url: str = field(repr=False)
    timeout_seconds: int = 600

    def _run(
        self,
        arguments: list[str],
        *,
        source: BinaryIO | None = None,
        destination: BinaryIO | None = None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.container):
            raise PostgresToolError("TRANSITION_DATABASE_TOOL_CONFIG_INVALID")
        if not 1 <= self.timeout_seconds <= 3600:
            raise PostgresToolError("TRANSITION_DATABASE_TOOL_CONFIG_INVALID")
        try:
            options = conninfo_to_dict(
                self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
            )
            environment = {
                key: value for key, value in os.environ.items() if not key.startswith("PG")
            }
            environment.update(
                PGHOST="127.0.0.1",
                PGPORT="5432",
                PGUSER=str(options["user"]),
                PGPASSWORD=str(options.get("password", "")),
                PGDATABASE=str(options["dbname"]),
                PGCONNECT_TIMEOUT="5",
            )
            command = ["docker", "exec", "-i"]
            for key in (
                "PGHOST",
                "PGPORT",
                "PGUSER",
                "PGPASSWORD",
                "PGDATABASE",
                "PGCONNECT_TIMEOUT",
            ):
                command.extend(["-e", key])
            command.extend(
                [self.container, "timeout", "-s", "TERM", "-k", "5", str(self.timeout_seconds)]
            )
            command.extend(arguments)
            result = subprocess.run(
                command,
                env=environment,
                stdin=source if source is not None else subprocess.DEVNULL,
                stdout=destination if destination is not None else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=self.timeout_seconds + 15,
                check=False,
            )
            if result.returncode:
                raise PostgresToolError("TRANSITION_DATABASE_TOOL_FAILED")
        except OSError, subprocess.SubprocessError, psycopg.Error, KeyError:
            raise PostgresToolError("TRANSITION_DATABASE_TOOL_FAILED") from None

    def dump(self, destination: Path, *, snapshot: str) -> None:
        """Export the caller's still-open repeatable-read snapshot to a new private file."""
        created = False
        complete = False
        try:
            if not re.fullmatch(r"[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+", snapshot):
                raise PostgresToolError("TRANSITION_SNAPSHOT_INVALID")
            _require_new_private_destination(destination)
            with _exclusive_private_writer(destination) as target:
                created = True
                self._run(
                    [
                        "pg_dump",
                        "--format=custom",
                        "--no-owner",
                        "--no-acl",
                        f"--snapshot={snapshot}",
                    ],
                    destination=target,
                )
            complete = True
        except BackupContractError, OSError, PostgresToolError:
            raise PostgresToolError("TRANSITION_DATABASE_TOOL_FAILED") from None
        finally:
            if created and not complete:
                destination.unlink(missing_ok=True)

    def restore(self, source: Path) -> None:
        """Restore authenticated inputs into a caller-provisioned, isolated empty database."""
        database_url = self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        if not _require_empty_database(database_url):
            raise PostgresToolError("TRANSITION_TARGET_NOT_EMPTY")
        try:
            _require_outside_repository(source, strict=True, code="TRANSITION_DUMP_INVALID")
            with _private_reader(source, code="TRANSITION_DUMP_INVALID") as (handle, _):
                if handle.read(5) != b"PGDMP":
                    raise PostgresToolError("TRANSITION_DUMP_INVALID")
                handle.seek(0)
                # Buffered seek may leave the inherited OS descriptor after read-ahead.
                os.lseek(handle.fileno(), 0, os.SEEK_SET)
                self._run(
                    [
                        "pg_restore",
                        "--exit-on-error",
                        "--single-transaction",
                        "--no-owner",
                        "--no-acl",
                        "--dbname=" + str(conninfo_to_dict(database_url)["dbname"]),
                    ],
                    source=handle,
                )
        except BackupContractError, OSError, psycopg.Error, KeyError:
            raise PostgresToolError("TRANSITION_DUMP_INVALID") from None
