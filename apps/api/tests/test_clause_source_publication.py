"""Complete source-bound Clause bodies are retained without rewriting Clause content."""

import json
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from familycare_api.clauses.normalization import normalize_clause_text
from familycare_api.clauses.repository import ClauseRepository
from familycare_api.clauses.source_repository import (
    ClauseSourceProjector,
    read_verified_clause_source,
)
from familycare_api.common.scope import HouseholdScope
from psycopg.rows import dict_row

from apps.api.tests.test_terms_change_integration import (
    _psycopg_url,
    _sources,
    enrollment_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_terms_change_integration import (
    changes_database as changes_database,
)

pytestmark = pytest.mark.integration

_BODY_7 = "회사는 합성 지급 조건에 따라 보험금을 지급합니다."
_BODY_8 = "회사는 합성 제외 조건에 해당하면 보험금을 지급하지 않습니다."
_TERMS = f"제7조 (합성 지급 조건)\n{_BODY_7}\n제8조 (합성 제외 조건)\n{_BODY_8}"


def _clause(
    url: str, job: Any, edition: UUID, *, body: str = _BODY_7, label: str = "제7조"
) -> UUID:
    evidence = uuid4()
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        source = connection.execute(
            "SELECT e.document_version_id,e.content_sha256,g.extraction_id FROM terms_editions e "
            "JOIN terms_applicability_component_sources c ON c.id=e.source_component_id "
            "JOIN document_structure_generations g ON g.id=c.generation_id WHERE e.id=%s",
            (edition,),
        ).fetchone()
        connection.execute(
            "INSERT INTO evidence(id,household_space_id,document_version_id,extraction_id,"
            "content_sha256,physical_page,x0,y0,x1,y1,review_state) "
            "VALUES(%s,%s,%s,%s,%s,1,10,10,500,700,'USER_CONFIRMED')",
            (
                evidence,
                job.household_space_id,
                source["document_version_id"],
                source["extraction_id"],
                source["content_sha256"],
            ),
        )
    return (
        ClauseRepository(url)
        .create(
            HouseholdScope(job.household_space_id),
            terms_edition_id=edition,
            parent_clause_id=None,
            clause_type="article",
            label=label,
            normalized_title=normalize_clause_text("합성 지급 조건"),
            normalized_text=normalize_clause_text(body),
            physical_page_start=1,
            physical_page_end=1,
            evidence_ids=(evidence,),
        )
        .id
    )


@pytest.mark.parametrize("wrong_body", [False, True])
def test_clause_source_checks_the_whole_body_beneath_its_actual_heading(
    changes_database: Any,
    wrong_body: bool,
) -> None:
    url, job = changes_database
    sources = _sources(url, job, terms_body=_TERMS)
    clause_id = _clause(url, job, sources["EDITION-A"], body=_BODY_8 if wrong_body else _BODY_7)
    projector = ClauseSourceProjector(url)
    assert projector.refresh_pending() == 1
    assert projector.refresh_pending() == 0
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        row = connection.execute(
            "SELECT * FROM current_clause_source_assessments WHERE clause_id=%s", (clause_id,)
        ).fetchone()
        assert row["status"] == ("UNKNOWN" if wrong_body else "MATCH")
        verified = read_verified_clause_source(connection, job.household_space_id, clause_id)
        assert (verified is not None) is not wrong_body
        assert read_verified_clause_source(connection, uuid4(), clause_id) is None
        if not wrong_body:
            assert row["source_region"]["heading"]["text"] == "제7조 (합성 지급 조건)"
            assert row["source_region"]["body_text"] == _BODY_7
            assert row["source_region"]["complete"] is True
        assert connection.execute(
            "SELECT normalized_text FROM clauses WHERE id=%s", (clause_id,)
        ).fetchone()["normalized_text"] == normalize_clause_text(_BODY_8 if wrong_body else _BODY_7)
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute(
                "UPDATE clause_source_assessments SET status='MATCH' WHERE id=%s", (row["id"],)
            )
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute("DELETE FROM clause_source_assessments WHERE id=%s", (row["id"],))


@pytest.mark.parametrize("mutation", ["none", "bbox", "body_text", "borrowed_body", "digest"])
def test_stored_match_is_replayed_before_becoming_source_authority(
    changes_database: Any,
    mutation: str,
) -> None:
    from psycopg.types.json import Jsonb

    url, job = changes_database
    sources = _sources(url, job, terms_body=_TERMS)
    clause_id = _clause(url, job, sources["EDITION-A"])
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        with connection.transaction(force_rollback=True):
            assert ClauseSourceProjector(url)._refresh(
                connection, clause_id, job.household_space_id
            )
            row = connection.execute(
                "SELECT * FROM clause_source_assessments WHERE clause_id=%s", (clause_id,)
            ).fetchone()
        region = row["source_region"]
        if mutation == "bbox":
            region["heading"]["bbox"] = [0, 0, 1, 1]
        elif mutation == "body_text":
            region["body_text"] = _BODY_8
        elif mutation == "digest":
            region["source_sha256"] = "0" * 64
        elif mutation == "borrowed_body":
            body = region["body"][0]
            body["start"] = _TERMS.index(_BODY_8) + body["start"] - _TERMS.index(_BODY_7)
            body["end"] = body["start"] + len(_BODY_8)
            body["text"] = _BODY_8
            region["body_text"] = _BODY_8
        # A retained assessment is an audit candidate; only canonical replay grants authority.
        # Some structural corruption can also be rejected earlier by the DB guard.
        try:
            with connection.transaction():
                connection.execute(
                    "INSERT INTO clause_source_assessments SELECT * FROM "
                    "jsonb_populate_record(NULL::clause_source_assessments,"
                    "jsonb_set(%s::jsonb,'{input_context}',clause_source_input_context(%s,%s)))",
                    (
                        Jsonb(row, dumps=lambda item: json.dumps(item, default=str)),
                        clause_id,
                        job.household_space_id,
                    ),
                )
        except psycopg.IntegrityError:
            pass
        if mutation != "bbox":
            assert (
                connection.execute(
                    "SELECT count(*) AS n FROM clause_source_assessments WHERE clause_id=%s",
                    (clause_id,),
                ).fetchone()["n"]
                == 1
            )
        verified = read_verified_clause_source(connection, job.household_space_id, clause_id)
        assert (verified is not None) is (mutation == "none")


def test_evidence_changes_retire_current_source_without_removing_history(
    changes_database: Any,
) -> None:
    url, job = changes_database
    sources = _sources(url, job, terms_body=_TERMS)
    clause_id = _clause(url, job, sources["EDITION-A"])
    projector = ClauseSourceProjector(url)
    assert projector.refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            read_verified_clause_source(connection, job.household_space_id, clause_id) is not None
        )
        connection.execute(
            "UPDATE evidence SET review_state='NEEDS_REVIEW' WHERE id IN("
            "SELECT evidence_id FROM clause_evidence WHERE clause_id=%s)",
            (clause_id,),
        )
        assert read_verified_clause_source(connection, job.household_space_id, clause_id) is None
        assert (
            connection.execute("SELECT count(*) AS n FROM clause_source_assessments").fetchone()[
                "n"
            ]
            == 1
        )
    assert projector.refresh_pending() == 1
    assert projector.refresh_pending() == 0
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute("SELECT status FROM current_clause_source_assessments").fetchone()[
                "status"
            ]
            == "UNKNOWN"
        )
        assert (
            connection.execute("SELECT count(*) AS n FROM clause_source_assessments").fetchone()[
                "n"
            ]
            == 2
        )


def test_concurrent_source_refresh_and_cancel_preserve_one_assessment(
    changes_database: Any,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    url, job = changes_database
    sources = _sources(url, job, terms_body=_TERMS)
    _clause(url, job, sources["EDITION-A"])
    assert ClauseSourceProjector(url).refresh_pending(stop_requested=lambda: True) == 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: ClauseSourceProjector(url).refresh_pending(), range(3)))
    assert sum(results) == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM clause_source_assessments").fetchone()[
                "n"
            ]
            == 1
        )


@pytest.mark.parametrize("operation", ["publish", "read"])
def test_source_replay_rejects_evidence_changed_during_observation(
    changes_database: Any,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    from familycare_api.clauses import source_repository

    url, job = changes_database
    sources = _sources(url, job, terms_body=_TERMS)
    clause_id = _clause(url, job, sources["EDITION-A"])
    if operation == "read":
        assert ClauseSourceProjector(url).refresh_pending() == 1
    original = source_repository._observe_clause

    def changed(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        with psycopg.connect(_psycopg_url(url)) as other:
            other.execute(
                "UPDATE evidence SET review_state='NEEDS_REVIEW' WHERE id IN("
                "SELECT evidence_id FROM clause_evidence WHERE clause_id=%s)",
                (clause_id,),
            )
        return result

    monkeypatch.setattr(source_repository, "_observe_clause", changed)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        if operation == "read":
            assert (
                read_verified_clause_source(connection, job.household_space_id, clause_id) is None
            )
        else:
            assert not ClauseSourceProjector(url)._refresh(
                connection, clause_id, job.household_space_id
            )
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM current_clause_source_assessments"
            ).fetchone()["n"]
            == 0
        )
    monkeypatch.setattr(source_repository, "_observe_clause", original)
    assert ClauseSourceProjector(url).refresh_pending() == 1


def test_failed_assessment_insert_rolls_back_and_retries_without_source_log(
    changes_database: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from familycare_api.clauses import source_repository

    url, job = changes_database
    sources = _sources(url, job, terms_body=_TERMS)
    clause_id = _clause(url, job, sources["EDITION-A"])
    monkeypatch.setattr(source_repository.logger, "disabled", False)
    caplog.set_level("WARNING", logger=source_repository.logger.name)
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute("""
            CREATE FUNCTION synthetic_fail_clause_source_insert() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic source detail'; END $$;
            CREATE TRIGGER synthetic_clause_source_insert_failure
              AFTER INSERT ON clause_source_assessments
              FOR EACH ROW EXECUTE FUNCTION synthetic_fail_clause_source_insert();
        """)
    try:
        assert ClauseSourceProjector(url).refresh_pending() == 0
        with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
            assert (
                connection.execute(
                    "SELECT count(*) AS n FROM clause_source_assessments"
                ).fetchone()["n"]
                == 0
            )
        assert "clause source assessment failed" in caplog.messages
        assert "synthetic source detail" not in caplog.text and _BODY_7 not in caplog.text
    finally:
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute(
                "DROP TRIGGER synthetic_clause_source_insert_failure ON clause_source_assessments"
            )
            connection.execute("DROP FUNCTION synthetic_fail_clause_source_insert()")
    assert ClauseSourceProjector(url).refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            read_verified_clause_source(connection, job.household_space_id, clause_id) is not None
        )


@pytest.mark.parametrize("mutation", ["wrong_component", "partial_source", "unknown_region"])
def test_assessment_guard_rejects_inconsistent_source_references(
    changes_database: Any, mutation: str
) -> None:
    from psycopg.types.json import Jsonb

    url, job = changes_database
    sources = _sources(url, job, terms_body=_TERMS)
    clause_id = _clause(url, job, sources["EDITION-A"])
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        with connection.transaction(force_rollback=True):
            assert ClauseSourceProjector(url)._refresh(
                connection, clause_id, job.household_space_id
            )
            row = connection.execute(
                "SELECT * FROM clause_source_assessments WHERE clause_id=%s", (clause_id,)
            ).fetchone()
        row["status"] = "UNKNOWN"
        if mutation != "unknown_region":
            row["source_region"] = None
        if mutation == "wrong_component":
            row["source_component_id"] = connection.execute(
                "SELECT source_component_id FROM terms_editions WHERE id=%s",
                (sources["EDITION-B"],),
            ).fetchone()["source_component_id"]
        elif mutation == "partial_source":
            row["source_publication_id"] = None
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute(
                "INSERT INTO clause_source_assessments SELECT * FROM "
                "jsonb_populate_record(NULL::clause_source_assessments,"
                "jsonb_set(%s::jsonb,'{input_context}',clause_source_input_context(%s,%s)))",
                (
                    Jsonb(row, dumps=lambda item: json.dumps(item, default=str)),
                    clause_id,
                    job.household_space_id,
                ),
            )


def test_source_publication_rejects_temporary_evidence_change_returning_to_old_input(
    changes_database: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from familycare_api.clauses import source_repository

    url, job = changes_database
    sources = _sources(url, job, terms_body=_TERMS)
    clause_id = _clause(url, job, sources["EDITION-A"])

    def width(value: int) -> None:
        with psycopg.connect(_psycopg_url(url)) as other:
            other.execute(
                "UPDATE evidence SET x1=%s WHERE id IN("
                "SELECT evidence_id FROM clause_evidence WHERE clause_id=%s)",
                (value, clause_id),
            )

    width(20)
    original = source_repository._observe_clause

    def temporarily_wide(*args: Any, **kwargs: Any) -> Any:
        width(500)
        try:
            return original(*args, **kwargs)
        finally:
            width(20)

    monkeypatch.setattr(source_repository, "_observe_clause", temporarily_wide)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert not ClauseSourceProjector(url)._refresh(
            connection, clause_id, job.household_space_id
        )
        assert (
            connection.execute("SELECT count(*) AS n FROM clause_source_assessments").fetchone()[
                "n"
            ]
            == 0
        )
    monkeypatch.setattr(source_repository, "_observe_clause", original)
    assert ClauseSourceProjector(url).refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute("SELECT status FROM current_clause_source_assessments").fetchone()[
                "status"
            ]
            == "UNKNOWN"
        )


def test_downgrade_preserves_existing_clause_source_history(changes_database: Any) -> None:
    import os
    import subprocess
    import sys

    url, job = changes_database
    sources = _sources(url, job, terms_body=_TERMS)
    clause_id = _clause(url, job, sources["EDITION-A"])
    assert ClauseSourceProjector(url).refresh_pending() == 1
    environment = {
        **os.environ,
        "FAMILYCARE_DATABASE_URL": url.replace("postgresql://", "postgresql+psycopg://"),
        "TMPDIR": "/tmp",
    }
    command = [sys.executable, "-m", "alembic", "-c", "apps/api/alembic.ini"]
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        starting_revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()["version_num"]
    attempted = subprocess.run(
        command + ["downgrade", "0046_event_terms_snapshots"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert attempted.returncode != 0
    assert "clause source history prevents downgrade" in attempted.stderr
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()["version_num"]
            == starting_revision
        )
        assert (
            read_verified_clause_source(connection, job.household_space_id, clause_id) is not None
        )
