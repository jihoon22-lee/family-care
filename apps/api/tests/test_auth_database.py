"""PostgreSQL evidence for hash-only identity rows and authenticated scope."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

import psycopg
import pytest
from familycare_api.identity.cli import AdminProvisioner, AdminProvisioningError
from familycare_api.identity.sessions import PostgresSessionStore, SessionService
from familycare_api.main import create_app
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000000001")


def _database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required for PostgreSQL integration tests")
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture
def identity_database() -> Iterator[str]:
    database_url = _database_url()
    with psycopg.connect(database_url) as connection:
        connection.execute("TRUNCATE app_sessions, app_users CASCADE")
        connection.execute("DELETE FROM household_spaces")
        connection.execute(
            "INSERT INTO household_spaces (id, space_key, display_name) VALUES (%s, %s, %s)",
            (HOUSEHOLD_ID, "synthetic-household", "Synthetic Household"),
        )
    yield database_url
    with psycopg.connect(database_url) as connection:
        connection.execute("TRUNCATE app_sessions, app_users CASCADE")
        connection.execute("DELETE FROM household_spaces")


@pytest.mark.integration
def test_first_run_initialization_creates_login_ready_household() -> None:
    database_url = _database_url()
    with psycopg.connect(database_url) as connection:
        connection.execute("TRUNCATE app_sessions, app_users CASCADE")
        connection.execute("DELETE FROM household_spaces")

    try:
        admin = AdminProvisioner(database_url).initialize(
            space_key="primary-household",
            household_name="FamilyCare Home",
            username="admin-a",
            raw_password="synthetic-auth-secret-a",
            display_name="Admin A",
        )

        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            households = connection.execute(
                "SELECT id, space_key, display_name FROM household_spaces"
            ).fetchall()
            users = connection.execute(
                "SELECT household_space_id, username, password_hash FROM app_users"
            ).fetchall()

        assert households == [
            {
                "id": admin.household_space_id,
                "space_key": "primary-household",
                "display_name": "FamilyCare Home",
            }
        ]
        assert users[0]["household_space_id"] == admin.household_space_id
        assert users[0]["username"] == "admin-a"
        assert users[0]["password_hash"].startswith("$argon2id$")
        assert "synthetic-auth-secret-a" not in repr(users)

        with pytest.raises(AdminProvisioningError, match="HOUSEHOLD_ALREADY_INITIALIZED"):
            AdminProvisioner(database_url).initialize(
                space_key="replacement-household",
                household_name="Replacement Home",
                username="admin-b",
                raw_password="synthetic-auth-secret-b",
                display_name="Admin B",
            )

        application = create_app(enable_synthetic_ingestion=False)
        with TestClient(application, base_url="https://testserver") as client:
            login = client.post(
                "/api/v1/auth/login",
                headers={"Origin": "https://testserver"},
                json={
                    "username": "admin-a",
                    "password": "synthetic-auth-secret-a",
                    "device_label": "Synthetic first-run client",
                },
            )
        assert login.status_code == 200
    finally:
        with psycopg.connect(database_url) as connection:
            connection.execute("TRUNCATE app_sessions, app_users CASCADE")
            connection.execute("DELETE FROM household_spaces")


@pytest.mark.integration
def test_raw_password_and_session_tokens_are_absent_from_rows(identity_database: str) -> None:
    provisioner = AdminProvisioner(identity_database)
    user = provisioner.create("admin-a", "synthetic-auth-secret-a", "Admin A")
    sessions = SessionService(PostgresSessionStore(identity_database))
    issued = sessions.issue(
        user.id,
        "Synthetic integration client",
        datetime(2026, 1, 1, tzinfo=UTC),
    )

    with psycopg.connect(identity_database, row_factory=dict_row) as connection:
        users = connection.execute("SELECT * FROM app_users").fetchall()
        stored_sessions = connection.execute("SELECT * FROM app_sessions").fetchall()

    persisted = repr((users, stored_sessions))
    assert "synthetic-auth-secret-a" not in persisted
    assert issued.raw_token not in persisted
    assert issued.csrf_token not in persisted
    assert users[0]["password_hash"].startswith("$argon2id$")
    assert stored_sessions[0]["token_hash"] != issued.raw_token
    assert stored_sessions[0]["csrf_token_hash"] != issued.csrf_token


@pytest.mark.integration
def test_two_admin_limit_and_password_change_revoke_sessions(identity_database: str) -> None:
    provisioner = AdminProvisioner(identity_database)
    admin_a = provisioner.create("admin-a", "synthetic-auth-secret-a", "Admin A")
    provisioner.create("admin-b", "synthetic-auth-secret-b", "Admin B")
    with pytest.raises(AdminProvisioningError, match="ADMIN_LIMIT_REACHED"):
        provisioner.create("admin-c", "synthetic-auth-secret-c", "Admin C")
    sessions = SessionService(PostgresSessionStore(identity_database))
    issued = sessions.issue(admin_a.id, "Synthetic client", datetime(2026, 1, 1, tzinfo=UTC))

    provisioner.set_password("admin-a", "synthetic-auth-secret-new")

    assert sessions.resolve(issued.raw_token, datetime(2026, 1, 1, tzinfo=UTC)) is None


@pytest.mark.integration
def test_login_cookie_protects_business_scope_and_csrf(identity_database: str) -> None:
    AdminProvisioner(identity_database).create(
        "admin-a",
        "synthetic-auth-secret-a",
        "Admin A",
    )
    application = create_app(enable_synthetic_ingestion=False)
    with TestClient(application, base_url="https://testserver") as client:
        login = client.post(
            "/api/v1/auth/login",
            headers={"Origin": "https://testserver"},
            json={
                "username": "admin-a",
                "password": "synthetic-auth-secret-a",
                "device_label": "Synthetic integration client",
            },
        )
        assert login.status_code == 200
        csrf_token = login.json()["csrf_token"]

        scoped_read = client.get(
            "/api/v1/family-members",
            params={"household_space_id": "00000000-0000-4000-8000-000000000099"},
        )
        missing_csrf = client.post(
            "/api/v1/family-members",
            headers={"Origin": "https://testserver"},
            json={"display_name": "Member A", "internal_alias": "member-a"},
        )
        accepted = client.post(
            "/api/v1/family-members",
            headers={
                "Origin": "https://testserver",
                "X-CSRF-Token": csrf_token,
            },
            json={"display_name": "Member A", "internal_alias": "member-a"},
        )

    assert scoped_read.status_code == 200
    assert scoped_read.json() == []
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error_code"] == "CSRF_REQUIRED"
    assert accepted.status_code == 201
    assert accepted.json()["display_name"] == "Member A"


@pytest.mark.integration
def test_reauthentication_and_password_change_revoke_current_session(
    identity_database: str,
) -> None:
    AdminProvisioner(identity_database).create(
        "admin-a",
        "synthetic-auth-secret-a",
        "Admin A",
    )
    application = create_app(enable_synthetic_ingestion=False)
    with TestClient(application, base_url="https://testserver") as client:
        login = client.post(
            "/api/v1/auth/login",
            headers={"Origin": "https://testserver"},
            json={
                "username": "admin-a",
                "password": "synthetic-auth-secret-a",
                "device_label": "Synthetic integration client",
            },
        )
        csrf_token = login.json()["csrf_token"]
        protected_headers = {
            "Origin": "https://testserver",
            "X-CSRF-Token": csrf_token,
        }

        reauthenticated = client.post(
            "/api/v1/auth/reauthenticate",
            headers=protected_headers,
            json={"password": "synthetic-auth-secret-a"},
        )
        changed = client.post(
            "/api/v1/auth/password",
            headers=protected_headers,
            json={"new_password": "synthetic-auth-secret-new"},
        )
        expired = client.get("/api/v1/auth/me")
        old_login = client.post(
            "/api/v1/auth/login",
            headers={"Origin": "https://testserver"},
            json={
                "username": "admin-a",
                "password": "synthetic-auth-secret-a",
                "device_label": "Synthetic integration client",
            },
        )
        new_login = client.post(
            "/api/v1/auth/login",
            headers={"Origin": "https://testserver"},
            json={
                "username": "admin-a",
                "password": "synthetic-auth-secret-new",
                "device_label": "Synthetic integration client",
            },
        )

    assert reauthenticated.status_code == 204
    assert changed.status_code == 204
    assert expired.status_code == 401
    assert old_login.status_code == 401
    assert old_login.json()["error_code"] == "AUTH_FAILED"
    assert new_login.status_code == 200
