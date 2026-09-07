"""History cost and per-policy failure isolation on the dedicated synthetic DB."""

from typing import Any

import psycopg
import pytest
from familycare_api.clauses.terms_applicability_repository import TermsApplicabilityProjector
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests import test_insurance_document_inventory_integration as seed
from apps.api.tests.test_rider_clause_rules_integration import database_url as database_url
from apps.api.tests.test_terms_applicability_integration import _sources

pytestmark = pytest.mark.integration


def test_current_context_is_computed_once_despite_assessment_history(database_url: str) -> None:
    _sources(database_url)
    projector = TermsApplicabilityProjector(database_url)
    for number in range(8):
        with psycopg.connect(seed._psycopg_url(database_url)) as connection:
            connection.execute(
                "UPDATE policy_contracts SET product_display=%s WHERE id=%s",
                (f"Synthetic revised display {number}", seed.POLICY_ID),
            )
        assert projector.refresh_pending() == 1
    with psycopg.connect(seed._psycopg_url(database_url), row_factory=dict_row) as connection:
        context = connection.execute(
            "SELECT policy_terms_input_context(%s,%s,%s) AS context",
            (seed.POLICY_ID, seed.MEMBER_A_ID, seed.HOUSEHOLD_ID),
        ).fetchone()["context"]
        # Replace only within a rolled-back transaction; count real DB invocations
        # without timing assertions or changing retained assessment history.
        connection.execute("CREATE TEMP SEQUENCE synthetic_context_calls")
        connection.execute(
            sql.SQL(
                "CREATE OR REPLACE FUNCTION policy_terms_input_context("
                "policy_id UUID,member_id UUID,household_id UUID) "
                "RETURNS JSONB LANGUAGE plpgsql STABLE AS {}"
            ).format(
                sql.Literal(
                    "BEGIN PERFORM nextval('pg_temp.synthetic_context_calls'); RETURN "
                    + sql.Literal(Jsonb(context)).as_string(connection)
                    + "; END"
                )
            )
        )
        rows = connection.execute(
            "SELECT id FROM current_policy_terms_applicability "
            "WHERE household_space_id=%s AND family_member_id=%s",
            (seed.HOUSEHOLD_ID, seed.MEMBER_A_ID),
        ).fetchall()
        calls = connection.execute("SELECT last_value FROM synthetic_context_calls").fetchone()
        connection.rollback()
    assert len(rows) == 1
    assert calls["last_value"] == 1


def test_timeout_does_not_starve_later_members_or_immediately_retry(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sources(database_url)
    with psycopg.connect(seed._psycopg_url(database_url)) as connection:
        connection.execute(
            "INSERT INTO policy_parties(household_space_id,policy_contract_id,family_member_id,"
            "role,evidence_id) VALUES(%s,%s,%s,'additional_insured',%s)",
            (seed.HOUSEHOLD_ID, seed.POLICY_ID, seed.MEMBER_B_ID, seed.EVIDENCE_ID),
        )
    visited: list[Any] = []
    original = TermsApplicabilityProjector._refresh

    def refresh(cls: Any, connection: Any, policy: dict[str, Any]) -> bool:
        visited.append(policy["family_member_id"])
        if policy["family_member_id"] == seed.MEMBER_A_ID:
            connection.execute("SET LOCAL statement_timeout='1ms'")
            connection.execute("SELECT pg_sleep(0.1)")
        return original(connection, policy)

    monkeypatch.setattr(TermsApplicabilityProjector, "_refresh", classmethod(refresh))
    projector = TermsApplicabilityProjector(database_url)
    projector.refresh_pending()
    assert visited == [seed.MEMBER_A_ID, seed.MEMBER_B_ID]
    visited.clear()
    projector.refresh_pending()
    assert visited == [seed.MEMBER_B_ID]
