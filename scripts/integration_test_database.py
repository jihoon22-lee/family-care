#!/usr/bin/env python3
"""Fail closed before destructive PostgreSQL integration tests can run."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, MutableMapping

import psycopg

TEST_DATABASE_URL_ENV = "FAMILYCARE_TEST_DATABASE_URL"
DESTRUCTIVE_TEST_OPT_IN_ENV = "FAMILYCARE_ALLOW_DESTRUCTIVE_TEST_DB"
RUNTIME_DATABASE_URL_ENV = "FAMILYCARE_DATABASE_URL"

_SAFE_DATABASE_MARKER = re.compile(r"(?:^|[_-])(?:test|ci)(?:[_-]|$)", re.IGNORECASE)


class IntegrationDatabaseGuardError(RuntimeError):
    """Raised when a destructive integration-test database is not proven safe."""


def _psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _read_current_database(database_url: str) -> str:
    try:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            row = connection.execute("SELECT current_database()").fetchone()
    except psycopg.Error:
        raise IntegrationDatabaseGuardError(
            "integration test database identity could not be verified"
        ) from None

    if row is None or not isinstance(row[0], str) or not row[0]:
        raise IntegrationDatabaseGuardError(
            "integration test database identity could not be verified"
        )
    return row[0]


def is_safe_integration_database_name(database_name: str) -> bool:
    """Return whether a database name has a standalone test or CI marker."""

    return _SAFE_DATABASE_MARKER.search(database_name) is not None


def configure_integration_test_database(
    environment: MutableMapping[str, str] | None = None,
    *,
    database_name_reader: Callable[[str], str] = _read_current_database,
) -> str:
    """Verify the dedicated test DB and expose it to legacy integration fixtures."""

    target_environment = os.environ if environment is None else environment
    database_url = target_environment.get(TEST_DATABASE_URL_ENV)
    if not database_url:
        raise IntegrationDatabaseGuardError(
            f"integration tests require {TEST_DATABASE_URL_ENV}; "
            f"{RUNTIME_DATABASE_URL_ENV} is never used as a fallback"
        )
    if target_environment.get(DESTRUCTIVE_TEST_OPT_IN_ENV) != "true":
        raise IntegrationDatabaseGuardError(
            f"integration tests require {DESTRUCTIVE_TEST_OPT_IN_ENV}=true"
        )

    database_name = database_name_reader(_psycopg_url(database_url))
    if not is_safe_integration_database_name(database_name):
        raise IntegrationDatabaseGuardError(
            "integration test database name must include a standalone test or ci marker"
        )

    target_environment[RUNTIME_DATABASE_URL_ENV] = database_url
    return database_url
