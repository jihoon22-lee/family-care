"""Compare wholly synthetic rows across a restore/migration boundary."""

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest

from scripts.private_runtime_state import (
    RuntimeStateError,
    capture_database_state,
    require_preserved_state,
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def connection() -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    database_url = os.environ["FAMILYCARE_TEST_DATABASE_URL"].replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    with psycopg.connect(database_url) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        conn.execute(
            "CREATE TABLE synthetic_transition_records (id integer PRIMARY KEY, payload jsonb)"
        )
        conn.execute(
            "INSERT INTO synthetic_transition_records VALUES "
            '(1, \'{"correction":"synthetic-a"}\'), '
            '(2, \'{"claim_snapshot":"synthetic-b"}\')'
        )
        try:
            yield conn
        finally:
            conn.rollback()


def test_state_survives_new_migration_columns_and_row_order(
    connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    baseline = capture_database_state(connection, tables=("synthetic_transition_records",))
    connection.execute(
        "ALTER TABLE synthetic_transition_records ADD COLUMN future_schema boolean DEFAULT false"
    )
    connection.execute("UPDATE synthetic_transition_records SET future_schema=true WHERE id=1")
    require_preserved_state(connection, baseline)
    assert baseline.tables[0].row_count == 2
    assert "synthetic-a" not in repr(baseline)


def test_same_row_count_edit_is_detected_and_error_has_no_payload(
    connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    baseline = capture_database_state(connection, tables=("synthetic_transition_records",))
    connection.execute(
        "UPDATE synthetic_transition_records SET payload='\"synthetic replacement\"' WHERE id=1"
    )
    with pytest.raises(RuntimeStateError, match="^TRANSITION_PRESERVED_DATA_CHANGED$"):
        require_preserved_state(connection, baseline)


def test_deleted_source_column_is_not_silently_ignored(
    connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    baseline = capture_database_state(connection, tables=("synthetic_transition_records",))
    connection.execute("ALTER TABLE synthetic_transition_records DROP COLUMN payload")
    with pytest.raises(RuntimeStateError, match="^TRANSITION_SCHEMA_CONTRACT_MISSING$"):
        require_preserved_state(connection, baseline)


def test_empty_requested_table_set_is_not_a_success(
    connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    with pytest.raises(RuntimeStateError, match="^TRANSITION_EMPTY_INVENTORY$"):
        capture_database_state(connection, tables=())


def test_state_detects_inserted_rows(
    connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    baseline = capture_database_state(connection, tables=("synthetic_transition_records",))
    connection.execute("INSERT INTO synthetic_transition_records VALUES (3, '{}')")
    with pytest.raises(RuntimeStateError, match="^TRANSITION_PRESERVED_DATA_CHANGED$"):
        require_preserved_state(connection, baseline)


def test_full_inventory_allows_migration_stamp_but_strict_source_check_rejects_it(
    connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    baseline = capture_database_state(connection)
    connection.execute("UPDATE alembic_version SET version_num='0064_synthetic_transition'")
    connection.execute(
        "ALTER TABLE synthetic_transition_records ADD COLUMN future_schema boolean DEFAULT false"
    )
    require_preserved_state(connection, baseline)
    with pytest.raises(RuntimeStateError, match="^TRANSITION_SOURCE_CHANGED$"):
        require_preserved_state(connection, baseline, require_same_schema=True)


def test_backfill_may_append_new_rows_without_replacing_an_original(
    connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    baseline = capture_database_state(connection, tables=("synthetic_transition_records",))
    connection.execute("INSERT INTO synthetic_transition_records VALUES (3, '{}')")
    require_preserved_state(connection, baseline, allow_new_rows=True)
    connection.execute(
        "UPDATE synthetic_transition_records "
        "SET payload='\"synthetic changed original\"' WHERE id=1"
    )
    with pytest.raises(RuntimeStateError, match="^TRANSITION_PRESERVED_DATA_CHANGED$"):
        require_preserved_state(connection, baseline, allow_new_rows=True)


def test_new_identity_cannot_disguise_a_removed_original(
    connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    baseline = capture_database_state(connection, tables=("synthetic_transition_records",))
    connection.execute("UPDATE synthetic_transition_records SET id=4 WHERE id=1")
    with pytest.raises(RuntimeStateError, match="^TRANSITION_PRESERVED_DATA_CHANGED$"):
        require_preserved_state(connection, baseline, allow_new_rows=True)


def test_identity_inventory_budget_is_enforced_while_streaming(
    connection: psycopg.Connection[tuple[object, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import private_runtime_state

    monkeypatch.setattr(private_runtime_state, "_MAX_IDENTITY_ROWS", 1)
    with pytest.raises(RuntimeStateError, match="^TRANSITION_INVENTORY_LIMIT_EXCEEDED$"):
        capture_database_state(connection, tables=("synthetic_transition_records",))


def test_large_strict_tables_need_no_retained_identity_list(
    connection: psycopg.Connection[tuple[object, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import private_runtime_state

    monkeypatch.setattr(private_runtime_state, "_MAX_IDENTITY_ROWS", 1)
    baseline = capture_database_state(
        connection, tables=("synthetic_transition_records",), identity_tables=()
    )
    assert baseline.tables[0].identity_digests is None
    require_preserved_state(connection, baseline, require_same_schema=True)
    connection.execute("INSERT INTO synthetic_transition_records VALUES (3, '{}')")
    with pytest.raises(RuntimeStateError, match="^TRANSITION_PRESERVED_DATA_CHANGED$"):
        require_preserved_state(connection, baseline, allow_new_rows=True)
