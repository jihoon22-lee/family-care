"""Process and database health contract for the analyzer worker."""

import os
import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal, TypedDict

import psycopg

from familycare_worker import __version__
from familycare_worker.archive.keys import MasterKey, MasterKeyError

DatabaseProbe = Callable[[], bool]
RuntimeProbe = Callable[[], bool]

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


def _available_directory(path: Path, *, writable: bool) -> bool:
    if not path.is_absolute() or not path.is_dir():
        return False
    access = os.R_OK | os.X_OK
    if writable:
        access |= os.W_OK
    return os.access(path, access)


def private_runtime_is_ready(environ: Mapping[str, str] | None = None) -> bool:
    """Fail closed when configured private Worker boundaries are unavailable."""

    values = os.environ if environ is None else environ
    activation_names = (
        "FAMILYCARE_IMPORT_ROOT",
        "FAMILYCARE_ARCHIVE_ROOT",
        "FAMILYCARE_ARCHIVE_MASTER_KEY_FILE",
        "FAMILYCARE_SECRET_SOCKET",
    )
    if not any(values.get(name) for name in activation_names):
        return True

    required_names = (*activation_names, "FAMILYCARE_WORK_ROOT")
    if not all(values.get(name) for name in required_names):
        return False

    import_root = Path(values["FAMILYCARE_IMPORT_ROOT"])
    archive_root = Path(values["FAMILYCARE_ARCHIVE_ROOT"])
    work_root = Path(values["FAMILYCARE_WORK_ROOT"])
    key_file = Path(values["FAMILYCARE_ARCHIVE_MASTER_KEY_FILE"])
    socket_path = Path(values["FAMILYCARE_SECRET_SOCKET"])
    if not _available_directory(import_root, writable=False):
        return False
    if not _available_directory(archive_root, writable=True):
        return False
    if not _available_directory(work_root, writable=True):
        return False
    if not socket_path.is_absolute() or not _available_directory(
        socket_path.parent,
        writable=True,
    ):
        return False
    try:
        MasterKey.from_file(key_file)
        socket_details = socket_path.lstat()
    except MasterKeyError, OSError:
        return False
    return stat.S_ISSOCK(socket_details.st_mode)


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
