"""Process and database health contracts for the FamilyCare API."""

import os
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from familycare_api import __version__

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
    """Return whether PostgreSQL accepts a minimal query."""

    url = database_url or os.getenv("FAMILYCARE_DATABASE_URL")
    if not url:
        return False

    engine: Engine | None = None
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False
    finally:
        if engine is not None:
            engine.dispose()
    return True


def readiness(probe: ReadinessProbe) -> HealthResponse:
    """Report whether the API can reach its required database."""

    return HealthResponse(status="ready" if probe() else "unavailable")
