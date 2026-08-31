from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.integration_test_database import (
    IntegrationDatabaseGuardError,
    configure_integration_test_database,
    is_safe_integration_database_name,
    reset_integration_test_database,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "database_name",
    ["familycare_test", "familycare_ci", "ci_familycare", "test-familycare"],
)
def test_safe_integration_database_name_accepts_standalone_markers(
    database_name: str,
) -> None:
    assert is_safe_integration_database_name(database_name)


@pytest.mark.parametrize(
    "database_name",
    ["familycare", "critical", "contest", "production"],
)
def test_safe_integration_database_name_rejects_embedded_or_missing_markers(
    database_name: str,
) -> None:
    assert not is_safe_integration_database_name(database_name)


def test_legacy_runtime_database_url_is_not_used_as_a_test_fallback() -> None:
    environment = {
        "FAMILYCARE_DATABASE_URL": "postgresql+psycopg://synthetic@localhost/familycare",
        "FAMILYCARE_ALLOW_DESTRUCTIVE_TEST_DB": "true",
    }

    with pytest.raises(
        IntegrationDatabaseGuardError,
        match="FAMILYCARE_TEST_DATABASE_URL",
    ):
        configure_integration_test_database(
            environment,
            database_name_reader=lambda _: pytest.fail("database must not be probed"),
        )


def test_destructive_test_opt_in_is_required_before_the_database_is_probed() -> None:
    test_url = "postgresql+psycopg://synthetic@localhost/familycare_test"
    environment = {"FAMILYCARE_TEST_DATABASE_URL": test_url}

    with pytest.raises(
        IntegrationDatabaseGuardError,
        match="FAMILYCARE_ALLOW_DESTRUCTIVE_TEST_DB=true",
    ):
        configure_integration_test_database(
            environment,
            database_name_reader=lambda _: pytest.fail("database must not be probed"),
        )


@pytest.mark.parametrize("database_name", ["familycare", "familycare_release", "critical"])
def test_database_name_without_a_test_or_ci_marker_is_rejected(database_name: str) -> None:
    environment = {
        "FAMILYCARE_TEST_DATABASE_URL": (
            "postgresql+psycopg://synthetic@localhost/synthetic_requested_name"
        ),
        "FAMILYCARE_ALLOW_DESTRUCTIVE_TEST_DB": "true",
    }

    with pytest.raises(IntegrationDatabaseGuardError, match="test.*ci.*marker"):
        configure_integration_test_database(
            environment,
            database_name_reader=lambda _: database_name,
        )

    assert "FAMILYCARE_DATABASE_URL" not in environment


@pytest.mark.parametrize("database_name", ["familycare_test", "familycare_ci", "ci_familycare"])
def test_verified_test_database_replaces_the_runtime_url(database_name: str) -> None:
    test_url = "postgresql+psycopg://synthetic@localhost/familycare_test"
    environment = {
        "FAMILYCARE_DATABASE_URL": "postgresql+psycopg://synthetic@localhost/familycare",
        "FAMILYCARE_TEST_DATABASE_URL": test_url,
        "FAMILYCARE_ALLOW_DESTRUCTIVE_TEST_DB": "true",
    }
    observed_urls: list[str] = []

    def read_database_name(url: str) -> str:
        observed_urls.append(url)
        return database_name

    configured = configure_integration_test_database(
        environment,
        database_name_reader=read_database_name,
    )

    assert configured == test_url
    assert environment["FAMILYCARE_DATABASE_URL"] == test_url
    assert observed_urls == ["postgresql://synthetic@localhost/familycare_test"]


def test_reset_uses_only_the_verified_dedicated_test_database() -> None:
    test_url = "postgresql+psycopg://synthetic@localhost/familycare_ci"
    environment = {
        "FAMILYCARE_DATABASE_URL": "postgresql+psycopg://synthetic@localhost/familycare",
        "FAMILYCARE_TEST_DATABASE_URL": test_url,
        "FAMILYCARE_ALLOW_DESTRUCTIVE_TEST_DB": "true",
    }
    reset_urls: list[str] = []

    reset_integration_test_database(
        environment,
        database_name_reader=lambda _: "familycare_ci",
        database_resetter=reset_urls.append,
    )

    assert reset_urls == ["postgresql://synthetic@localhost/familycare_ci"]
    assert environment["FAMILYCARE_DATABASE_URL"] == test_url


def test_reset_rejects_a_runtime_database_before_calling_the_resetter() -> None:
    environment = {
        "FAMILYCARE_TEST_DATABASE_URL": ("postgresql+psycopg://synthetic@localhost/familycare"),
        "FAMILYCARE_ALLOW_DESTRUCTIVE_TEST_DB": "true",
    }

    with pytest.raises(IntegrationDatabaseGuardError, match="test.*ci.*marker"):
        reset_integration_test_database(
            environment,
            database_name_reader=lambda _: "familycare",
            database_resetter=lambda _: pytest.fail("resetter must not run"),
        )


def test_pytest_blocks_an_integration_body_when_only_the_runtime_url_is_set() -> None:
    environment = os.environ.copy()
    environment.pop("FAMILYCARE_TEST_DATABASE_URL", None)
    environment.pop("FAMILYCARE_ALLOW_DESTRUCTIVE_TEST_DB", None)
    environment["FAMILYCARE_DATABASE_URL"] = "postgresql+psycopg://synthetic@127.0.0.1:1/familycare"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "integration",
            "-s",
            "-q",
            f"{Path(__file__)}::test_integration_guard_subprocess_sentinel",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "synthetic integration sentinel executed" not in output
    assert "FAMILYCARE_TEST_DATABASE_URL" in output


@pytest.mark.integration
def test_integration_guard_subprocess_sentinel() -> None:
    print("synthetic integration sentinel executed")
