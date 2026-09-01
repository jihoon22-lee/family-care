"""Synthetic PostgreSQL proof for the integrated insurance reconciliation boundary."""

from __future__ import annotations

import os
from uuid import UUID

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.identity.context import AuthContext
from familycare_api.insurance_documents.repository import InsuranceDocumentRepository
from familycare_api.insurance_documents.service import InsuranceDocumentService
from familycare_api.insurance_reconciliation.repository import (
    InsuranceReconciliationRepository,
    ReconciliationRepositoryConflict,
)

from apps.api.tests.test_insurance_document_inventory_integration import (
    HOUSEHOLD_ID,
    LOCKED_ITEM_ID,
    MEMBER_A_ID,
    MEMBER_B_ID,
    OTHER_MEMBER_ITEM_ID,
    POLICY_ID,
    TERMS_BATCH_ITEM_ID,
    USER_ID,
    _seed,
)
from apps.api.tests.test_insurance_reconciliation_migration_integration import (
    CONTRACT_ID,
    RUN_ID,
    _seed_knowledge,
)

pytestmark = pytest.mark.integration

LINK_ID = UUID("00000000-0000-4000-8000-000000002601")
REPLACEMENT_BATCH_ID = UUID("00000000-0000-4000-8000-000000002602")
REPLACEMENT_ITEM_ID = UUID("00000000-0000-4000-8000-000000002603")
OTHER_POLICY_ID = UUID("00000000-0000-4000-8000-000000002604")


def _database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required")
    return value


def _prepare(database_url: str) -> InsuranceReconciliationRepository:
    _seed(database_url)
    with psycopg.connect(
        database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    ) as connection:
        _seed_knowledge(connection)
    return InsuranceReconciliationRepository(database_url)


def test_projection_and_link_history_are_scoped_closed_and_versioned() -> None:
    repository = _prepare(_database_url())
    scope = HouseholdScope(HOUSEHOLD_ID)

    initial = repository.get_member(scope, MEMBER_A_ID)
    assert initial is not None
    assert initial.knowledge_run_id == RUN_ID
    assert initial.summary.total_contracts == 1
    assert initial.summary.link_review_required_contracts == 1
    assert initial.summary.orphan_operational_contracts == 1
    assert initial.summary.unresolved_unreadable_sources == 1
    assert initial.unresolved_sources[0].document_batch_item_id == LOCKED_ITEM_ID
    assert repository.get_member(scope, MEMBER_B_ID).summary.total_contracts == 0

    with psycopg.connect(
        _database_url().replace("postgresql+psycopg://", "postgresql://", 1)
    ) as connection:
        connection.execute(
            """
            INSERT INTO policy_contracts (
              id, household_space_id, source_document_version_id, source_evidence_id,
              insurer_display, insurer_key, product_display, product_key, status
            )
            SELECT %s, household_space_id, source_document_version_id, source_evidence_id,
                   'Sample Insurer', 'sample-insurer-b', 'Sample Policy B',
                   'sample-policy-b', 'unknown'
            FROM policy_contracts WHERE id = %s
            """,
            (OTHER_POLICY_ID, POLICY_ID),
        )
        connection.execute(
            """
            INSERT INTO policy_parties (
              household_space_id, policy_contract_id, family_member_id, role, evidence_id
            )
            SELECT household_space_id, %s, %s, role, evidence_id
            FROM policy_parties WHERE policy_contract_id = %s
            LIMIT 1
            """,
            (OTHER_POLICY_ID, MEMBER_B_ID, POLICY_ID),
        )
    with pytest.raises(ReconciliationRepositoryConflict):
        repository.confirm_operational_link(
            scope,
            actor_id=USER_ID,
            knowledge_contract_id=CONTRACT_ID,
            decision="MATCH",
            conflict=False,
            policy_contract_id=OTHER_POLICY_ID,
            reason_code="USER_CONFIRMED_SAME_CONTRACT",
            expected_current_link_id=None,
        )

    linked = repository.confirm_operational_link(
        scope,
        actor_id=USER_ID,
        knowledge_contract_id=CONTRACT_ID,
        decision="MATCH",
        conflict=False,
        policy_contract_id=POLICY_ID,
        reason_code="USER_CONFIRMED_SAME_CONTRACT",
        expected_current_link_id=None,
    )
    assert linked.id is not None
    assert linked.policy_contract_id == POLICY_ID

    after_link = repository.get_member(scope, MEMBER_A_ID)
    assert after_link is not None
    assert after_link.summary.documents_pending_contracts == 1
    assert after_link.summary.orphan_operational_contracts == 0

    document_service = InsuranceDocumentService(
        AuthContext(
            user_id=USER_ID,
            household_space_id=HOUSEHOLD_ID,
            session_id=UUID("00000000-0000-4000-8000-000000002699"),
            needs_reauthentication=False,
        ),
        InsuranceDocumentRepository(_database_url()),
    )
    terms = document_service.create_component(
        MEMBER_A_ID,
        document_batch_item_id=TERMS_BATCH_ITEM_ID,
        role="terms",
        page_start=1,
        page_end=2,
        evidence_id=None,
        review_state="USER_CONFIRMED",
    )
    document_set = document_service.create_document_set(
        MEMBER_A_ID,
        policy_contract_id=POLICY_ID,
        insurer_display=None,
        product_display=None,
        display_label="Sample Policy",
    )
    document_service.attach_set_item(
        document_set.id,
        insurance_document_component_id=terms.id,
        match_state="USER_CONFIRMED",
        evidence_id=None,
        expected_set_version=1,
    )
    ready = repository.get_member(scope, MEMBER_A_ID)
    assert ready is not None
    assert ready.summary.evidence_ready_contracts == 1
    assert ready.contracts[0].document_readiness.completeness == "CERTIFICATE_AND_TERMS"

    same = repository.confirm_operational_link(
        scope,
        actor_id=USER_ID,
        knowledge_contract_id=CONTRACT_ID,
        decision="MATCH",
        conflict=False,
        policy_contract_id=POLICY_ID,
        reason_code="USER_CONFIRMED_SAME_CONTRACT",
        expected_current_link_id=linked.id,
    )
    assert same.id == linked.id

    reopened = repository.confirm_operational_link(
        scope,
        actor_id=USER_ID,
        knowledge_contract_id=CONTRACT_ID,
        decision="UNKNOWN",
        conflict=False,
        policy_contract_id=None,
        reason_code="USER_REOPENED_OPERATIONAL_REVIEW",
        expected_current_link_id=linked.id,
    )
    assert reopened.id != linked.id
    assert reopened.decision == "UNKNOWN"

    with pytest.raises(ReconciliationRepositoryConflict):
        repository.confirm_operational_link(
            scope,
            actor_id=USER_ID,
            knowledge_contract_id=CONTRACT_ID,
            decision="MATCH",
            conflict=False,
            policy_contract_id=POLICY_ID,
            reason_code="USER_CONFIRMED_SAME_CONTRACT",
            expected_current_link_id=linked.id,
        )

    with psycopg.connect(
        _database_url().replace("postgresql+psycopg://", "postgresql://", 1)
    ) as connection:
        rows = connection.execute(
            """
            SELECT id, is_current FROM private_knowledge_operational_links
            ORDER BY created_at, id
            """
        ).fetchall()
    assert rows == [(linked.id, False), (reopened.id, True)]


def test_document_resolution_requires_a_later_success_in_the_same_member_scope() -> None:
    repository = _prepare(_database_url())
    scope = HouseholdScope(HOUSEHOLD_ID)
    with pytest.raises(ReconciliationRepositoryConflict):
        repository.confirm_document_resolution(
            scope,
            actor_id=USER_ID,
            failed_item_id=LOCKED_ITEM_ID,
            resolution="REPLACED",
            replacement_item_id=OTHER_MEMBER_ITEM_ID,
            reason_code="USER_CONFIRMED_REPLACEMENT",
            expected_current_resolution_id=None,
        )

    with psycopg.connect(
        _database_url().replace("postgresql+psycopg://", "postgresql://", 1)
    ) as connection:
        connection.execute(
            """
            INSERT INTO document_batches (
              id, household_space_id, family_member_id, created_by, state, completed_at
            ) VALUES (%s, %s, %s, %s, 'succeeded', clock_timestamp())
            """,
            (REPLACEMENT_BATCH_ID, HOUSEHOLD_ID, MEMBER_A_ID, USER_ID),
        )
        connection.execute(
            """
            INSERT INTO document_batch_items (
              id, batch_id, document_id, source_id, source_key, display_label,
              document_kind, state, processed_document_version_id,
              available_at, completed_at
            )
            SELECT %s, %s, document_id, %s,
                   'synthetic/replacement-policy.pdf', 'Synthetic replacement',
                   document_kind, 'succeeded', processed_document_version_id,
                   clock_timestamp(), clock_timestamp()
            FROM document_batch_items
            WHERE id = %s
            """,
            (
                REPLACEMENT_ITEM_ID,
                REPLACEMENT_BATCH_ID,
                "2" * 64,
                OTHER_MEMBER_ITEM_ID,
            ),
        )

    replaced = repository.confirm_document_resolution(
        scope,
        actor_id=USER_ID,
        failed_item_id=LOCKED_ITEM_ID,
        resolution="REPLACED",
        replacement_item_id=REPLACEMENT_ITEM_ID,
        reason_code="USER_CONFIRMED_REPLACEMENT",
        expected_current_resolution_id=None,
    )
    assert replaced.replacement_item_id == REPLACEMENT_ITEM_ID
    assert repository.get_member(scope, MEMBER_A_ID).summary.unresolved_unreadable_sources == 0

    reopened = repository.confirm_document_resolution(
        scope,
        actor_id=USER_ID,
        failed_item_id=LOCKED_ITEM_ID,
        resolution="REOPENED",
        replacement_item_id=None,
        reason_code="USER_REOPENED_DOCUMENT_REVIEW",
        expected_current_resolution_id=replaced.id,
    )
    assert reopened.resolution == "REOPENED"
    assert repository.get_member(scope, MEMBER_A_ID).summary.unresolved_unreadable_sources == 1

    dismissed = repository.confirm_document_resolution(
        scope,
        actor_id=USER_ID,
        failed_item_id=LOCKED_ITEM_ID,
        resolution="DISMISSED",
        replacement_item_id=None,
        reason_code="USER_DISMISSED_STALE_FAILURE",
        expected_current_resolution_id=reopened.id,
    )
    assert dismissed.resolution == "DISMISSED"
    assert repository.get_member(scope, MEMBER_A_ID).summary.unresolved_unreadable_sources == 0
