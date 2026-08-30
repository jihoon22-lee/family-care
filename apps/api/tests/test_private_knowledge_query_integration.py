"""PostgreSQL proof for bounded household-scoped knowledge reads."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.private_knowledge import query_repository as query_repository_module
from familycare_api.private_knowledge.query_repository import (
    PostgresPrivateKnowledgeQueryRepository,
    PrivateKnowledgeQueryTooLargeError,
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
    detail = reader.get_contract(
        scope,
        contract.id,
        section_limit=20,
        section_after=None,
    )
    assert detail is not None
    assert len(detail.coverages) == 1
    assert len(detail.terms_assignments) == 1
    assert len(detail.coverage_mappings) == 1
    assert len(detail.terms_sections) == 1
    assert len(detail.terms_sections[0].facts) == 1
    assert len(detail.terms_sections[0].facts[0].citations) == 1
    assert detail.next_section_cursor is None
    assert reader.list_contracts(scope, limit=50, after=contract.id).items == ()

    other_scope = HouseholdScope(UUID("00000000-0000-4000-8000-000000001999"))
    assert reader.current(other_scope) is None
    assert reader.list_contracts(other_scope, limit=50, after=None) is None
    assert (
        reader.get_contract(
            other_scope,
            contract.id,
            section_limit=20,
            section_after=None,
        )
        is None
    )

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


def test_contract_section_cursor_and_serialized_byte_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed()
    _, package = _package(tmp_path)
    writer = PostgresPrivateKnowledgeRepository(_database_url())
    applied = writer.apply_snapshot(
        package,
        household_space_id=HOUSEHOLD_ID,
        actor_id=ACTOR_ID,
        approved_report=_report(writer, package),
    )

    with psycopg.connect(_database_url()) as connection:
        original = connection.execute(
            """
            SELECT id FROM private_knowledge_terms_sections
            WHERE import_run_id = %s
            """,
            (applied.run_id,),
        ).fetchone()
        assert original is not None
        original_section_id = original[0]
        second_section_id = UUID(int=original_section_id.int + 1)
        second_review_id = UUID(int=second_section_id.int + 1)
        connection.execute(
            """
            INSERT INTO private_knowledge_terms_sections (
              id, import_run_id, source_section_key, terms_source_alias,
              terms_source_alias_digest_sha256, section_kind, heading,
              page_start, page_end, review_state, source_record_json,
              source_record_digest_sha256, created_at
            )
            SELECT %s, import_run_id, 'synthetic-section-page-2', terms_source_alias,
                   terms_source_alias_digest_sha256, section_kind,
                   'Second Synthetic Section', page_start + 1, page_end + 1,
                   review_state, source_record_json, source_record_digest_sha256,
                   created_at
            FROM private_knowledge_terms_sections
            WHERE id = %s AND import_run_id = %s
            """,
            (second_section_id, original_section_id, applied.run_id),
        )
        connection.execute(
            """
            INSERT INTO private_knowledge_semantic_reviews (
              id, import_run_id, terms_section_id, source_review_key,
              section_summary, analysis_status, confidence, review_state,
              found_categories_json, missing_categories_json, warnings_json,
              source_clause_count, classified_clause_count,
              unclassified_clause_count, legacy_review_only,
              source_record_json, source_record_digest_sha256, created_at
            )
            SELECT %s, import_run_id, %s, 'synthetic-review-page-2',
                   'Second synthetic section summary.', analysis_status,
                   confidence, review_state, found_categories_json,
                   missing_categories_json, warnings_json, source_clause_count,
                   classified_clause_count, unclassified_clause_count,
                   legacy_review_only, source_record_json,
                   source_record_digest_sha256, created_at
            FROM private_knowledge_semantic_reviews
            WHERE import_run_id = %s
            LIMIT 1
            """,
            (second_review_id, second_section_id, applied.run_id),
        )

    reader = PostgresPrivateKnowledgeQueryRepository(_database_url())
    scope = HouseholdScope(HOUSEHOLD_ID)
    contract = reader.list_contracts(scope, limit=1, after=None)
    assert contract is not None
    contract_id = contract.items[0].id
    first = reader.get_contract(
        scope,
        contract_id,
        section_limit=1,
        section_after=None,
    )
    assert first is not None
    assert len(first.terms_sections) == 1
    assert first.next_section_cursor == first.terms_sections[0].id
    second = reader.get_contract(
        scope,
        contract_id,
        section_limit=1,
        section_after=first.next_section_cursor,
    )
    assert second is not None
    assert len(second.terms_sections) == 1
    assert second.next_section_cursor is None

    monkeypatch.setattr(query_repository_module, "_MAX_RESPONSE_BYTES", 1)
    with pytest.raises(PrivateKnowledgeQueryTooLargeError):
        reader.get_contract(
            scope,
            contract_id,
            section_limit=1,
            section_after=None,
        )
