"""Synthetic scoped reads and concurrent or interrupted change projection."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from familycare_api.clauses.terms_change_repository import TermsChangeProjector, read_event_terms
from familycare_api.clauses.terms_change_selection import TermsSelectionScope
from psycopg.rows import dict_row

from apps.api.tests.test_terms_change_integration import (
    _psycopg_url,
    _sources,
    changes_database,  # noqa: F401
    enrollment_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "field", ["household_space_id", "family_member_id", "policy_contract_id", "rider_id"]
)
def test_foreign_scope_cannot_read_change_or_base_editions(
    changes_database: Any,  # noqa: F811
    field: str,
) -> None:
    url, job = changes_database
    sources = _sources(url, job)
    assert TermsChangeProjector(url).refresh_pending() == 1
    scope = TermsSelectionScope(
        job.household_space_id, sources["policy_id"], job.family_member_id, sources["Sample Rider"]
    )
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        selected = read_event_terms(
            connection, replace(scope, **{field: uuid4()}), date(2025, 7, 1)
        )
        assert not selected.editions and not selected.applied_relation_ids
        assert not selected.uncertain_relation_ids


def test_concurrent_refreshers_publish_one_source_assessment(changes_database: Any) -> None:  # noqa: F811
    url, job = changes_database
    _sources(url, job)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: TermsChangeProjector(url).refresh_pending(), range(3)))
    assert sum(results) == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM policy_terms_changes").fetchone()["n"]
            == 1
        )


def test_cancelled_refresh_has_no_side_effects(changes_database: Any) -> None:  # noqa: F811
    url, job = changes_database
    _sources(url, job)
    assert TermsChangeProjector(url).refresh_pending(stop_requested=lambda: True) == 0
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM policy_terms_changes").fetchone()["n"]
            == 0
        )
        assert (
            connection.execute("SELECT count(*) AS n FROM terms_change_refresh_checks").fetchone()[
                "n"
            ]
            == 0
        )


def test_failed_history_insert_rolls_back_and_can_retry_without_source_logs(
    changes_database: Any,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from familycare_api.clauses import terms_change_repository

    # In-process migration tests can disable existing loggers with fileConfig.
    monkeypatch.setattr(terms_change_repository.logger, "disabled", False)
    caplog.set_level("WARNING", logger=terms_change_repository.logger.name)
    url, job = changes_database
    _sources(url, job)
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute("""
            CREATE FUNCTION synthetic_fail_terms_change_insert() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'synthetic exception detail'; END $$;
            CREATE TRIGGER synthetic_terms_change_insert_failure
            AFTER INSERT ON policy_terms_changes
            FOR EACH ROW EXECUTE FUNCTION synthetic_fail_terms_change_insert();
        """)
    try:
        assert TermsChangeProjector(url).refresh_pending() == 0
        with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
            assert (
                connection.execute("SELECT count(*) AS n FROM policy_terms_changes").fetchone()["n"]
                == 0
            )
            assert (
                connection.execute(
                    "SELECT count(*) AS n FROM range_enrollment_publications"
                ).fetchone()["n"]
                == 3
            )
            assert (
                connection.execute(
                    "SELECT retry_after FROM terms_change_refresh_checks"
                ).fetchone()["retry_after"]
                is not None
            )
        assert "synthetic exception detail" not in caplog.text
        assert "Terms change refresh deferred" in caplog.text
    finally:
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute(
                "DROP TRIGGER synthetic_terms_change_insert_failure ON policy_terms_changes"
            )
            connection.execute("DROP FUNCTION synthetic_fail_terms_change_insert()")
            connection.execute("UPDATE terms_change_refresh_checks SET retry_after=NULL")
    assert TermsChangeProjector(url).refresh_pending() == 1
