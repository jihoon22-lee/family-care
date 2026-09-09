"""Installed API readiness requires one supported revision and its critical columns."""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from familycare_api import health
from sqlalchemy.exc import SQLAlchemyError

REVISION = "0065_retained_policy_jobs"


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, rows, contracts=True):
        self.rows, self.contracts = rows, contracts
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, statement):
        query = str(statement)
        self.queries.append(query)
        if "alembic_version" in query:
            if isinstance(self.rows, Exception):
                raise self.rows
            return Result(self.rows)
        if "WHERE false" in query and not self.contracts:
            raise SQLAlchemyError("synthetic missing critical column")
        return Result([(1,)])


class Engine:
    def __init__(self, connection):
        self.connection, self.disposed = connection, False

    def connect(self):
        return self.connection

    def dispose(self):
        self.disposed = True


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [("0064_metadata_proven_prefix",)],
        [("0066_synthetic_future",)],
        [(REVISION,), ("synthetic_other_head",)],
        [(REVISION,), (REVISION,)],
        [(None,)],
        SQLAlchemyError("synthetic absent alembic table"),
    ],
)
def test_api_rejects_missing_older_newer_or_multiple_schema_heads(monkeypatch, rows):
    engine = Engine(Connection(rows))
    monkeypatch.setattr(health, "create_engine", lambda *args, **kwargs: engine)
    assert health.database_is_ready("postgresql+psycopg://synthetic") is False
    assert engine.disposed


def test_api_checks_current_contract_without_ai_or_checkout_access(monkeypatch, tmp_path):
    connection = Connection([(REVISION,)])
    engine = Engine(connection)
    monkeypatch.setattr(health, "create_engine", lambda *args, **kwargs: engine)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    assert health.database_is_ready("postgresql+psycopg://synthetic") is True
    assert any("alembic_version" in q and "LIMIT 2" in q for q in connection.queries)
    assert any("review_job_id" in q and "WHERE false" in q for q in connection.queries)
    assert any("policy_structuring_source_current" in q for q in connection.queries)
    assert engine.disposed


def test_api_rejects_stamped_revision_with_missing_contract(monkeypatch, caplog):
    engine = Engine(Connection([(REVISION,)], contracts=False))
    monkeypatch.setattr(health, "create_engine", lambda *args, **kwargs: engine)
    assert health.database_is_ready("postgresql+psycopg://synthetic") is False
    assert "synthetic missing critical column" not in caplog.text


def test_both_distributed_schema_constants_match_the_single_repository_head():
    from familycare_api.runtime_schema import SUPPORTED_SCHEMA_REVISION as api_revision
    from familycare_worker.runtime_schema import SUPPORTED_SCHEMA_REVISION as worker_revision

    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
    assert ScriptDirectory.from_config(config).get_heads() == [api_revision]
    assert api_revision == worker_revision == REVISION
