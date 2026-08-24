"""Process and database health contract for the analyzer worker."""

import os
from collections.abc import Callable
from typing import Literal, TypedDict

import psycopg

from familycare_worker import __version__

DatabaseProbe = Callable[[], bool]

_ANALYSIS_JOBS_TABLE_QUERY = """
SELECT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_class AS relation
    INNER JOIN pg_catalog.pg_namespace AS namespace
        ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'public'
      AND relation.relname = 'analysis_jobs'
      AND relation.relkind IN ('r', 'p')
)
"""


class HealthPayload(TypedDict):
    """Stable process health payload."""

    service: Literal["analyzer"]
    status: Literal["ok", "ready", "unavailable"]
    version: str


def database_is_ready(database_url: str | None = None) -> bool:
    """Return whether PostgreSQL and the worker queue table are available."""

    url = database_url or os.getenv("FAMILYCARE_DATABASE_URL")
    if not url:
        return False

    psycopg_url = url.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        with psycopg.connect(psycopg_url) as connection:
            connection.execute("SELECT 1")
            result = connection.execute(_ANALYSIS_JOBS_TABLE_QUERY)
            row = result.fetchone()
    except psycopg.Error:
        return False
    return bool(row and row[0])


def health_payload(database_probe: DatabaseProbe | None = None) -> HealthPayload:
    """Report process health or database-backed readiness."""

    status: Literal["ok", "ready", "unavailable"] = "ok"
    if database_probe is not None:
        status = "ready" if database_probe() else "unavailable"

    return {
        "service": "analyzer",
        "status": status,
        "version": __version__,
    }
