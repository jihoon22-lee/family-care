"""Process and database health contracts for the FamilyCare API."""

import os
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from familycare_api import __version__
from familycare_api.runtime_schema import (
    REQUIRED_SCHEMA_QUERY,
    SCHEMA_REVISION_QUERY,
    SUPPORTED_SCHEMA_REVISION,
)

ReadinessProbe = Callable[[], bool]


class HealthResponse(BaseModel):
    """Stable public health response."""

    model_config = ConfigDict(frozen=True)

    service: Literal["api"] = "api"
    status: Literal["ok", "ready", "unavailable"]
    version: str = __version__


def liveness() -> HealthResponse:
    """Report that the API process can serve requests."""

    return HealthResponse(status="ok")


def database_is_ready(database_url: str | None = None) -> bool:
    """Require the installed schema revision and critical read/write contracts."""

    url = database_url or os.getenv("FAMILYCARE_DATABASE_URL")
    if not url:
        return False

    engine: Engine | None = None
    try:
        engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args={
                "connect_timeout": 2,
                "options": "-c default_transaction_read_only=on -c statement_timeout=1000 "
                "-c lock_timeout=1000",
            },
        )
        with engine.connect() as connection:
            revisions = connection.execute(text(SCHEMA_REVISION_QUERY)).fetchall()
            if [row[0] for row in revisions] != [SUPPORTED_SCHEMA_REVISION]:
                return False
            # Parse required columns without reading application rows. A database
            # stamped to the right revision but missing these contracts is not ready.
            connection.execute(text(REQUIRED_SCHEMA_QUERY))
    except SQLAlchemyError:
        return False
    finally:
        if engine is not None:
            engine.dispose()
    return True


def readiness(probe: ReadinessProbe) -> HealthResponse:
    """Report whether the API can reach its required database."""

    return HealthResponse(status="ready" if probe() else "unavailable")
