"""Repository-wide pytest safety hooks."""

from __future__ import annotations

import pytest

from scripts.integration_test_database import (
    IntegrationDatabaseGuardError,
    configure_integration_test_database,
)


def pytest_collection_finish(session: pytest.Session) -> None:
    """Validate the database before any selected integration test can set up fixtures."""

    if not any(item.get_closest_marker("integration") is not None for item in session.items):
        return
    try:
        configure_integration_test_database()
    except IntegrationDatabaseGuardError as error:
        raise pytest.UsageError(str(error)) from None
