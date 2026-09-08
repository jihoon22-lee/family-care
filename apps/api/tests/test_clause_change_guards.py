"""Synthetic Clause-pair migrations preserve history and rejection boundaries."""

import os
import subprocess
import sys
from copy import deepcopy
from typing import Any
from uuid import UUID

import psycopg
import pytest
from familycare_api.clauses.errors import CoverageRuleInvalid, RiderClauseLinkInvalid
from familycare_api.clauses.repository import CoverageRuleRepository, RiderClauseLinkRepository
from familycare_api.clauses.terms_change_clauses import change_allows_clause_publication
from familycare_api.clauses.terms_change_repository import TermsChangeProjector
from familycare_api.common.scope import HouseholdScope
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests.test_clause_change_integration import (
    _native_link,
    _native_rule,
    _paired_sources,
)
from apps.api.tests.test_terms_change_integration import (
    _psycopg_url,
    enrollment_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_terms_change_integration import (
    changes_database as changes_database,
)

pytestmark = pytest.mark.integration

_PAIR_COLUMNS = (
    "previous_clause_id",
    "new_clause_id",
    "previous_clause_source_id",
    "new_clause_source_id",
)


def _migrate(url: str, operation: str, revision: str) -> subprocess.CompletedProcess[str]:
    # The integration fixture supplies the URL already checked by the root hook.
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "apps/api/alembic.ini", operation, revision],
        env={**os.environ, "FAMILYCARE_DATABASE_URL": url, "TMPDIR": "/tmp"},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _capture_change(connection: Any, url: str, household: UUID) -> dict[str, Any]:
    component = connection.execute(
        "SELECT id FROM insurance_document_components WHERE household_space_id=%s "
        "AND role='amendment' AND deleted_at IS NULL",
        (household,),
    ).fetchone()["id"]
    with connection.transaction(force_rollback=True):
        assert TermsChangeProjector(url)._refresh(connection, component, household)
        row = connection.execute(
            "SELECT to_jsonb(a) AS value FROM policy_terms_changes a "
            "WHERE a.source_component_id=%s AND a.revision='terms-change-v2'",
            (component,),
        ).fetchone()["value"]
        assert row["status"] == "MATCH"
    assert connection.execute("SELECT count(*) AS n FROM policy_terms_changes").fetchone()["n"] == 0
    return row


def _insert_with_current_input(
    connection: Any, payload: dict[str, Any], *, legacy: bool = False
) -> dict[str, Any]:
    arguments: tuple[object, ...] = (
        payload["source_component_id"],
        payload["household_space_id"],
    )
    placeholders = "%s,%s"
    if not legacy:
        arguments = (*arguments, payload["scope_kind"])
        placeholders += ",%s"
    # Let PostgreSQL supply both values so numeric JSON roundtrips cannot create
    # an unrelated digest failure in a malformed-pair test.
    return connection.execute(
        "WITH input AS (SELECT terms_change_input_context("
        + placeholders
        + ") AS context) INSERT INTO policy_terms_changes "
        "SELECT (jsonb_populate_record(NULL::policy_terms_changes,%s::jsonb || "
        "jsonb_build_object('input_context',context,'input_digest',"
        "encode(sha256(convert_to(context::text,'UTF8')),'hex')))).* FROM input "
        "RETURNING to_jsonb(policy_terms_changes) AS value",
        (*arguments, Jsonb(payload)),
    ).fetchone()["value"]


def test_downgrade_refuses_v2_clause_history_before_changing_any_row(changes_database: Any) -> None:
    url, job = changes_database
    _paired_sources(url, job)
    assert TermsChangeProjector(url).refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        before = connection.execute(
            "SELECT id,to_jsonb(a)::text AS snapshot FROM policy_terms_changes a"
        ).fetchone()
        original_revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()["version_num"]
    try:
        attempted = _migrate(url, "downgrade", "0047_clause_sources")
        assert attempted.returncode != 0
        assert "clause change history prevents downgrade" in attempted.stderr
        with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
            assert (
                connection.execute("SELECT version_num FROM alembic_version").fetchone()[
                    "version_num"
                ]
                == original_revision
            )
    finally:
        restored = _migrate(url, "upgrade", "head")
        assert restored.returncode == 0, restored.stderr
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute(
                "SELECT to_jsonb(a)::text AS snapshot FROM policy_terms_changes a WHERE id=%s",
                (before["id"],),
            ).fetchone()["snapshot"]
            == before["snapshot"]
        )
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()["version_num"]
            == original_revision
        )


def test_upgrade_preserves_every_legacy_change_field_and_adds_only_null_pair_refs(
    changes_database: Any,
) -> None:
    url, job = changes_database
    _paired_sources(url, job, change_scope="특약")
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        legacy = _capture_change(connection, url, job.household_space_id)
    legacy = {key: value for key, value in legacy.items() if key not in _PAIR_COLUMNS}
    legacy["revision"] = "terms-change-v1"
    try:
        downgraded = _migrate(url, "downgrade", "0047_clause_sources")
        assert downgraded.returncode == 0, downgraded.stderr
        with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
            original = _insert_with_current_input(connection, legacy, legacy=True)
            before = connection.execute(
                "SELECT to_jsonb(a)::text AS snapshot FROM policy_terms_changes a WHERE id=%s",
                (original["id"],),
            ).fetchone()["snapshot"]
        upgraded = _migrate(url, "upgrade", "0048_clause_change_pairs")
        assert upgraded.returncode == 0, upgraded.stderr
        with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
            after = connection.execute(
                "SELECT to_jsonb(a) AS value,(to_jsonb(a)-%s::text[])::text AS legacy_snapshot "
                "FROM policy_terms_changes a WHERE id=%s",
                (list(_PAIR_COLUMNS), original["id"]),
            ).fetchone()
            assert after["legacy_snapshot"] == before
            assert all(after["value"][column] is None for column in _PAIR_COLUMNS)
            assert after["value"]["revision"] == "terms-change-v1"
            assert (
                connection.execute("SELECT count(*) AS n FROM policy_terms_changes").fetchone()["n"]
                == 1
            )
    finally:
        restored = _migrate(url, "upgrade", "head")
        assert restored.returncode == 0, restored.stderr


@pytest.mark.parametrize("mutation", ["missing_source_ref", "another_clause_source_ref"])
def test_pair_guard_rejects_missing_or_cross_clause_source_references(
    changes_database: Any, mutation: str
) -> None:
    url, job = changes_database
    sources = _paired_sources(url, job)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        original = _capture_change(connection, url, job.household_space_id)
        with connection.transaction(force_rollback=True):
            good = _insert_with_current_input(connection, original)
            assert good["status"] == "MATCH"
            assert good["new_clause_id"] == str(sources["clauses"]["EDITION-B", 7])
        malformed = deepcopy(original)
        if mutation == "missing_source_ref":
            malformed["new_clause_source_id"] = None
        else:
            foreign = connection.execute(
                "SELECT id FROM current_clause_source_assessments WHERE household_space_id=%s "
                "AND clause_id=%s AND status='MATCH'",
                (job.household_space_id, sources["clauses"]["EDITION-B", 8]),
            ).fetchone()["id"]
            malformed["new_clause_source_id"] = str(foreign)
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            _insert_with_current_input(connection, malformed)
        assert (
            connection.execute("SELECT count(*) AS n FROM policy_terms_changes").fetchone()["n"]
            == 0
        )


def test_failed_confirmation_preserves_rejection_and_cannot_reactivate_pair_publication(
    changes_database: Any,
) -> None:
    url, job = changes_database
    sources = _paired_sources(url, job)
    rider = sources["Sample Rider"]
    clause = sources["clauses"]["EDITION-B", 7]
    edition = sources["EDITION-B"]
    link_id = sources["links"]["EDITION-B", 7]
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        # An approved candidate for another Rider is not an alternative authority
        # for the exact link that the user will reject.
        _native_link(connection, job.household_space_id, sources["Another Rider"], clause, edition)
        draft = _native_rule(connection, job.household_space_id, link_id)
    assert TermsChangeProjector(url).refresh_pending() == 1
    scope = HouseholdScope(job.household_space_id)
    links = RiderClauseLinkRepository(url)
    confirmed = links.confirm(scope, link_id, expected_version=1)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        original = connection.execute(
            "SELECT id,to_jsonb(a)::text AS snapshot FROM current_policy_terms_changes a"
        ).fetchone()
        assert change_allows_clause_publication(
            connection, job.household_space_id, sources["policy_id"], rider, clause, edition
        )
    rejected = links.reject(
        scope, link_id, expected_version=confirmed.version, reason_code="USER_REJECTED"
    )
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        rejected_snapshot = connection.execute(
            "SELECT to_jsonb(l)::text AS snapshot FROM rider_clause_links l WHERE id=%s", (link_id,)
        ).fetchone()["snapshot"]
    with pytest.raises(RiderClauseLinkInvalid) as failed:
        links.confirm(scope, link_id, expected_version=rejected.version)
    assert failed.value.reason_code == "LINK_NOT_ACTIVE"
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute(
                "SELECT to_jsonb(l)::text AS snapshot FROM rider_clause_links l WHERE id=%s",
                (link_id,),
            ).fetchone()["snapshot"]
            == rejected_snapshot
        )
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM current_policy_terms_changes WHERE id=%s",
                (original["id"],),
            ).fetchone()["n"]
            == 0
        )
        assert not change_allows_clause_publication(
            connection, job.household_space_id, sources["policy_id"], rider, clause, edition
        )
    assert TermsChangeProjector(url).refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert not change_allows_clause_publication(
            connection, job.household_space_id, sources["policy_id"], rider, clause, edition
        )
        assert (
            connection.execute(
                "SELECT to_jsonb(a)::text AS snapshot FROM policy_terms_changes a WHERE id=%s",
                (original["id"],),
            ).fetchone()["snapshot"]
            == original["snapshot"]
        )
    with pytest.raises(CoverageRuleInvalid):
        CoverageRuleRepository(url).publish(scope, *draft, expected_version=1)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM coverage_rule_versions "
                "WHERE coverage_rule_id=%s AND executable",
                (draft[0],),
            ).fetchone()["n"]
            == 0
        )
