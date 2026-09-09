"""Private, bounded fingerprints for restore and source-change verification.

Only row digests leave PostgreSQL. These fingerprints still belong in the private
transition journal, never in public logs or repository artifacts. Callers own the
repeatable-read transaction and the writer barrier used for actual activation.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import tuple_row

_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
_MAX_TABLES = 512
_MAX_COLUMNS = 256
_MAX_IDENTITY_ROWS = 1_000_000


class RuntimeStateError(RuntimeError):
    """A fixed, non-sensitive transition failure."""


@dataclass(frozen=True, repr=False)
class TableState:
    name: str
    columns: tuple[str, ...]
    identity_columns: tuple[str, ...]
    identity_digests: tuple[str, ...] | None
    row_count: int
    sha256: str


@dataclass(frozen=True, repr=False)
class DatabaseState:
    schema_revision: str
    tables: tuple[TableState, ...]
    complete_inventory: bool


def _require_snapshot(connection: psycopg.Connection[Any]) -> str:
    with connection.cursor(row_factory=tuple_row) as cursor:
        cursor.execute("SHOW transaction_isolation")
        isolation = cursor.fetchone()
        if isolation is None or isolation[0] not in {"repeatable read", "serializable"}:
            raise RuntimeStateError("TRANSITION_CONSISTENT_SNAPSHOT_REQUIRED")
        cursor.execute("SELECT version_num FROM public.alembic_version LIMIT 2")
        revisions = cursor.fetchall()
    if len(revisions) != 1 or not isinstance(revisions[0][0], str):
        raise RuntimeStateError("TRANSITION_SCHEMA_CONTRACT_MISSING")
    return revisions[0][0]


def _tables(connection: psycopg.Connection[Any]) -> tuple[str, ...]:
    with connection.cursor(row_factory=tuple_row) as cursor:
        cursor.execute(
            "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public' "
            "ORDER BY tablename LIMIT %s",
            (_MAX_TABLES + 1,),
        )
        names = tuple(row[0] for row in cursor.fetchall())
    if not names or len(names) > _MAX_TABLES:
        raise RuntimeStateError("TRANSITION_INVENTORY_LIMIT_EXCEEDED")
    return names


def _columns(connection: psycopg.Connection[Any], table: str) -> tuple[str, ...]:
    with connection.cursor(row_factory=tuple_row) as cursor:
        cursor.execute(
            "SELECT a.attname FROM pg_catalog.pg_attribute a "
            "JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname=%s AND c.relkind IN ('r','p') "
            "AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attname LIMIT %s",
            (table, _MAX_COLUMNS + 1),
        )
        columns = tuple(row[0] for row in cursor.fetchall())
    if not columns or len(columns) > _MAX_COLUMNS:
        raise RuntimeStateError("TRANSITION_SCHEMA_CONTRACT_MISSING")
    return columns


def _primary_keys(connection: psycopg.Connection[Any], table: str) -> tuple[str, ...]:
    with connection.cursor(row_factory=tuple_row) as cursor:
        cursor.execute(
            "SELECT a.attname FROM pg_catalog.pg_index i "
            "JOIN pg_catalog.pg_class c ON c.oid=i.indrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum=ANY(i.indkey) "
            "WHERE n.nspname='public' AND c.relname=%s AND i.indisprimary ORDER BY a.attname",
            (table,),
        )
        return tuple(row[0] for row in cursor.fetchall())


def _fingerprint(
    connection: psycopg.Connection[Any],
    table: str,
    columns: tuple[str, ...],
    *,
    identity_columns: tuple[str, ...] | None = None,
    retained_identities: tuple[str, ...] | None = None,
    identity_limit: int = _MAX_IDENTITY_ROWS,
    retain_identity_digests: bool = True,
) -> TableState:
    if not _NAME.fullmatch(table) or not columns or any(not _NAME.fullmatch(c) for c in columns):
        raise RuntimeStateError("TRANSITION_SCHEMA_CONTRACT_MISSING")
    available = _columns(connection, table)
    if not set(columns).issubset(available):
        raise RuntimeStateError("TRANSITION_SCHEMA_CONTRACT_MISSING")
    identities = _primary_keys(connection, table) if identity_columns is None else identity_columns
    if not set(identities).issubset(columns):
        raise RuntimeStateError("TRANSITION_SCHEMA_CONTRACT_MISSING")
    identity = (
        sql.SQL("encode(sha256(convert_to(jsonb_build_array({})::text,'UTF8')),'hex')").format(
            sql.SQL(",").join(map(sql.Identifier, identities))
        )
        if identities
        else sql.SQL("NULL::text")
    )
    condition = (
        sql.SQL(" WHERE {} = ANY(%s)").format(identity)
        if retained_identities is not None
        else sql.SQL("")
    )
    query = sql.SQL(
        "SELECT encode(sha256(convert_to(to_jsonb(projected)::text,'UTF8')),'hex') AS digest, {} "
        "FROM (SELECT {} FROM {}) AS projected {} ORDER BY digest"
    ).format(
        identity,
        sql.SQL(",").join(map(sql.Identifier, columns)),
        sql.Identifier("public", table),
        condition,
    )
    digest = hashlib.sha256()
    count = 0
    identity_digests: list[str] = []
    # A named cursor bounds client memory even for large extraction/source tables.
    with connection.cursor(name=f"transition_{uuid4().hex}", row_factory=tuple_row) as cursor:
        cursor.itersize = 512
        cursor.execute(
            query, (list(retained_identities),) if retained_identities is not None else None
        )
        for row in cursor:
            digest.update(bytes.fromhex(row[0]))
            count += 1
            if retain_identity_digests and row[1] is not None:
                if len(identity_digests) >= identity_limit:
                    raise RuntimeStateError("TRANSITION_INVENTORY_LIMIT_EXCEEDED")
                identity_digests.append(row[1])
    return TableState(
        table,
        columns,
        identities,
        tuple(identity_digests) if retain_identity_digests else None,
        count,
        digest.hexdigest(),
    )


def capture_database_state(
    connection: psycopg.Connection[Any],
    *,
    tables: Sequence[str] | None = None,
    identity_tables: Sequence[str] | None = None,
) -> DatabaseState:
    """Capture every row; retain bounded IDs only for tables allowed future appends.

    Tables excluded from identity_tables remain subject to exact row comparison,
    including when allow_new_rows is requested. None retains IDs for every table.
    """
    try:
        revision = _require_snapshot(connection)
        selected = _tables(connection) if tables is None else tuple(sorted(tables))
        if not selected:
            raise RuntimeStateError("TRANSITION_EMPTY_INVENTORY")
        if len(selected) > _MAX_TABLES or len(set(selected)) != len(selected):
            raise RuntimeStateError("TRANSITION_INVENTORY_LIMIT_EXCEEDED")
        # Stable JSON timestamp rendering across source and restored connections.
        connection.execute("SET LOCAL TIME ZONE 'UTC'")
        state: list[TableState] = []
        remaining = _MAX_IDENTITY_ROWS
        for table in selected:
            value = _fingerprint(
                connection,
                table,
                _columns(connection, table),
                identity_limit=remaining,
                retain_identity_digests=identity_tables is None or table in identity_tables,
            )
            state.append(value)
            remaining -= len(value.identity_digests or ())
        return DatabaseState(revision, tuple(state), tables is None)
    except psycopg.Error, ValueError, TypeError:
        raise RuntimeStateError("TRANSITION_STATE_UNAVAILABLE") from None


def require_preserved_state(
    connection: psycopg.Connection[Any],
    baseline: DatabaseState,
    *,
    require_same_schema: bool = False,
    allow_new_rows: bool = False,
) -> None:
    """Verify source columns, allowing new migration columns unless checking a live baseline."""
    try:
        revision = _require_snapshot(connection)
        if not baseline.tables:
            raise RuntimeStateError("TRANSITION_EMPTY_INVENTORY")
        if require_same_schema and allow_new_rows:
            raise RuntimeStateError("TRANSITION_INVALID_COMPARE_MODE")
        if require_same_schema and (
            revision != baseline.schema_revision
            or (
                baseline.complete_inventory
                and _tables(connection) != tuple(table.name for table in baseline.tables)
            )
        ):
            raise RuntimeStateError("TRANSITION_SOURCE_CHANGED")
        connection.execute("SET LOCAL TIME ZONE 'UTC'")
        for table in baseline.tables:
            if table.name == "alembic_version" and not require_same_schema:
                # Migration version is validated separately from preserved user/source rows.
                continue
            if require_same_schema and (
                _columns(connection, table.name) != table.columns
                or _primary_keys(connection, table.name) != table.identity_columns
            ):
                raise RuntimeStateError("TRANSITION_SOURCE_CHANGED")
            if allow_new_rows and table.row_count and not table.identity_columns:
                raise RuntimeStateError("TRANSITION_ROW_IDENTITY_UNAVAILABLE")
            current = _fingerprint(
                connection,
                table.name,
                table.columns,
                identity_columns=table.identity_columns,
                retained_identities=table.identity_digests if allow_new_rows else None,
                retain_identity_digests=table.identity_digests is not None,
            )
            if current != table:
                raise RuntimeStateError("TRANSITION_PRESERVED_DATA_CHANGED")
    except psycopg.Error, ValueError, TypeError:
        raise RuntimeStateError("TRANSITION_STATE_UNAVAILABLE") from None
