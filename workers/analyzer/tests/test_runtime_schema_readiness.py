"""Worker schema checks reject incompatible stores before private resources or jobs."""

from threading import Event

import psycopg
import pytest
from familycare_worker import __main__ as entry
from familycare_worker import health

REVISION = "0064_metadata_proven_prefix"


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, revisions, contracts=True):
        self.revisions, self.contracts = revisions, contracts
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query):
        self.queries.append(query)
        if "alembic_version" in query:
            if isinstance(self.revisions, Exception):
                raise self.revisions
            return Result(self.revisions)
        if "WHERE false" in query and not self.contracts:
            raise psycopg.ProgrammingError("synthetic missing critical column")
        return Result([(True,)])


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [("0063_review_claim_sources",)],
        [("0065_synthetic_future",)],
        [(REVISION,), ("synthetic_other_head",)],
        [(REVISION,), (REVISION,)],
        [(None,)],
        psycopg.ProgrammingError("synthetic absent alembic table"),
    ],
)
def test_worker_rejects_missing_older_newer_or_multiple_schema_heads(monkeypatch, rows):
    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: Connection(rows))
    assert health.database_is_ready("postgresql://synthetic") is False


def test_worker_current_schema_is_ready_without_ai_or_checkout(monkeypatch, tmp_path):
    connection = Connection([(REVISION,)])
    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: connection)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    assert health.database_is_ready("postgresql://synthetic") is True
    assert any("alembic_version" in q and "LIMIT 2" in q for q in connection.queries)
    assert any("sources_json" in q and "WHERE false" in q for q in connection.queries)


def test_worker_rejects_stamped_revision_with_missing_contract(monkeypatch, caplog):
    connection = Connection([(REVISION,)], contracts=False)
    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: connection)
    assert health.database_is_ready("postgresql://synthetic") is False
    assert "synthetic missing critical column" not in caplog.text


def test_worker_startup_checks_before_private_runner_initialization(monkeypatch, caplog):
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", "postgresql://synthetic")

    def forbidden(*args):
        raise AssertionError("Private runner initialization must not run")

    monkeypatch.setattr(entry, "_runner_from_environment", forbidden)
    assert entry.main([], database_probe=lambda: False, stop_event=Event()) == 1
    assert "worker_database_schema_unavailable" in caplog.messages


def test_worker_rechecks_before_next_consumption_and_closes_resources(caplog):
    stop = Event()
    checks = iter([True, False])

    class Runner:
        calls = 0
        closed = False

        def run_once(self, worker_id):
            self.calls += 1
            if self.calls == 2:
                stop.set()
            return True

        def shutdown(self):
            self.closed = True

    runner = Runner()
    assert (
        entry.run_worker_loop(
            stop,
            runner,
            worker_id="synthetic-worker",
            poll_interval_seconds=0,
            database_probe=lambda: next(checks),
        )
        == 1
    )
    assert runner.calls == 1 and runner.closed
    assert "worker_database_schema_unavailable" in caplog.messages


def test_worker_default_loop_probe_fails_before_first_job(monkeypatch):
    stop = Event()

    class Runner:
        calls = 0
        closed = False

        def run_once(self, worker_id):
            self.calls += 1
            stop.set()
            return False

        def shutdown(self):
            self.closed = True

    monkeypatch.setattr(entry, "database_is_ready", lambda: False)
    runner = Runner()
    assert entry.run_worker_loop(stop, runner, worker_id="synthetic-worker") == 1
    assert runner.calls == 0 and runner.closed


def test_schema_probe_failure_does_not_log_connection_details(caplog):
    class Runner:
        def run_once(self, worker_id):
            raise AssertionError("No job may be claimed")

    def failing_probe():
        raise RuntimeError("synthetic-private-connection-value")

    assert (
        entry.run_worker_loop(
            Event(),
            Runner(),
            worker_id="synthetic-worker",
            database_probe=failing_probe,
        )
        == 1
    )
    assert "synthetic-private-connection-value" not in caplog.text
    assert "worker_database_schema_unavailable" in caplog.messages


def test_already_stopped_worker_closes_supplied_runner_without_probing():
    stop = Event()
    stop.set()

    class Runner:
        closed = False

        def run_once(self, worker_id):
            raise AssertionError("No job may be claimed")

        def shutdown(self):
            self.closed = True

    def forbidden_probe():
        raise AssertionError("No probe needed after cancellation")

    runner = Runner()
    assert (
        entry.main(
            [],
            job_runner=runner,
            stop_event=stop,
            database_probe=forbidden_probe,
        )
        == 0
    )
    assert runner.closed
