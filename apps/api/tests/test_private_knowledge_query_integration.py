"""PostgreSQL proof for bounded household-scoped knowledge reads."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.private_knowledge.query_repository import (
    PostgresPrivateKnowledgeQueryRepository,
)
from familycare_api.private_knowledge.repository import (
    PostgresPrivateKnowledgeRepository,
)

from apps.api.tests.test_private_knowledge_apply_integration import (
    ACTOR_ID,
    HOUSEHOLD_ID,
    _database_url,
    _package,
    _report,
    _seed,
)

pytestmark = pytest.mark.integration


def test_query_projection_is_complete_bounded_and_household_isolated(
    tmp_path: Path,
) -> None:
    _seed()
    _, package = _package(tmp_path)
    writer = PostgresPrivateKnowledgeRepository(_database_url())
    writer.apply_snapshot(
        package,
        household_space_id=HOUSEHOLD_ID,
        actor_id=ACTOR_ID,
        approved_report=_report(writer, package),
    )
    reader = PostgresPrivateKnowledgeQueryRepository(_database_url())
    scope = HouseholdScope(HOUSEHOLD_ID)

    current = reader.current(scope)
    page = reader.list_contracts(scope, limit=50, after=None)

    assert current is not None
    assert current.counts.contracts == 1
    assert page is not None
    assert len(page.items) == 1
    assert page.next_cursor is None
    contract = page.items[0]
    detail = reader.get_contract(scope, contract.id)
    assert detail is not None
    assert len(detail.coverages) == 1
    assert len(detail.terms_assignments) == 1
    assert len(detail.coverage_mappings) == 1
    assert len(detail.terms_sections) == 1
    assert len(detail.terms_sections[0].facts) == 1
    assert len(detail.terms_sections[0].facts[0].citations) == 1
    assert reader.list_contracts(scope, limit=50, after=contract.id).items == ()

    other_scope = HouseholdScope(UUID("00000000-0000-4000-8000-000000001999"))
    assert reader.current(other_scope) is None
    assert reader.list_contracts(other_scope, limit=50, after=None) is None
    assert reader.get_contract(other_scope, contract.id) is None

    serialized = json.dumps(detail.model_dump(mode="json"), sort_keys=True).lower()
    for forbidden in (
        "source_alias",
        "source_record",
        "source_text_sha256",
        "document_version_id",
        "evidence_id",
        "policy_contract_id",
        "rider_id",
    ):
        assert forbidden not in serialized

    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            UPDATE private_knowledge_subjects
            SET binding_decision = 'NO_MATCH'
            WHERE import_run_id = %s
            """,
            (current.run_id,),
        )
    drifted = reader.current(scope)
    assert drifted is not None
    assert drifted.unsafe_operational_binding_count == 1
