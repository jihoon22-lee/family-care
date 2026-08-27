"""Synthetic PostgreSQL proof for reviewed insurance-document inventory."""

from __future__ import annotations

import os
from uuid import UUID

import psycopg
import pytest
from familycare_api.identity.context import AuthContext
from familycare_api.insurance_documents.repository import InsuranceDocumentRepository
from familycare_api.insurance_documents.service import InsuranceDocumentService
from familycare_api.policies.errors import EvidenceInvalid, PolicyStateConflict

pytestmark = pytest.mark.integration

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000000801")
USER_ID = UUID("00000000-0000-4000-8000-000000000802")
MEMBER_A_ID = UUID("00000000-0000-4000-8000-000000000803")
MEMBER_B_ID = UUID("00000000-0000-4000-8000-000000000804")
POLICY_DOCUMENT_ID = UUID("00000000-0000-4000-8000-000000000805")
TERMS_DOCUMENT_ID = UUID("00000000-0000-4000-8000-000000000806")
POLICY_VERSION_ID = UUID("00000000-0000-4000-8000-000000000807")
TERMS_VERSION_ID = UUID("00000000-0000-4000-8000-000000000808")
POLICY_REISSUE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000816")
POLICY_EXTRACTION_ID = UUID("00000000-0000-4000-8000-000000000809")
EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000810")
POLICY_ID = UUID("00000000-0000-4000-8000-000000000811")
POLICY_BATCH_ITEM_ID = UUID("00000000-0000-4000-8000-000000000812")
TERMS_BATCH_ITEM_ID = UUID("00000000-0000-4000-8000-000000000813")
OTHER_MEMBER_ITEM_ID = UUID("00000000-0000-4000-8000-000000000814")
LOCKED_ITEM_ID = UUID("00000000-0000-4000-8000-000000000815")
POLICY_REISSUE_ITEM_ID = UUID("00000000-0000-4000-8000-000000000817")


def _database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required")
    return value


def _psycopg_url(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _seed(database_url: str) -> None:
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        database_name = connection.execute("SELECT current_database()").fetchone()
        assert database_name is not None
        if not any(marker in str(database_name[0]).lower() for marker in ("test", "ci")):
            pytest.skip("inventory integration test requires an isolated test database")
        connection.execute("TRUNCATE TABLE household_spaces, documents RESTART IDENTITY CASCADE")
        connection.execute(
            """
            INSERT INTO household_spaces (id, space_key, display_name)
            VALUES (%s, 'synthetic-inventory', 'Synthetic Household')
            """,
            (HOUSEHOLD_ID,),
        )
        connection.execute(
            """
            INSERT INTO app_users (
              id, household_space_id, username, display_name, password_hash
            ) VALUES (%s, %s, 'synthetic-inventory-admin', 'Admin A',
                      '$argon2id$synthetic')
            """,
            (USER_ID, HOUSEHOLD_ID),
        )
        connection.execute(
            """
            INSERT INTO family_members (id, household_space_id, display_name, internal_alias)
            VALUES
              (%s, %s, 'Family Member A', 'family-member-a'),
              (%s, %s, 'Family Member B', 'family-member-b')
            """,
            (MEMBER_A_ID, HOUSEHOLD_ID, MEMBER_B_ID, HOUSEHOLD_ID),
        )
        connection.execute(
            """
            INSERT INTO documents (id, source_key, document_kind, status, page_count)
            VALUES
              (%s, 'synthetic/inventory-policy.pdf', 'policy', 'ready', 5),
              (%s, 'synthetic/inventory-terms.pdf', 'terms', 'ready', 5)
            """,
            (POLICY_DOCUMENT_ID, TERMS_DOCUMENT_ID),
        )
        connection.execute(
            """
            INSERT INTO document_versions (
              id, document_id, version_number, content_sha256, byte_size, page_count
            ) VALUES
              (%s, %s, 1, %s, 128, 5),
              (%s, %s, 1, %s, 128, 5),
              (%s, %s, 2, %s, 160, 5)
            """,
            (
                POLICY_VERSION_ID,
                POLICY_DOCUMENT_ID,
                "a" * 64,
                TERMS_VERSION_ID,
                TERMS_DOCUMENT_ID,
                "b" * 64,
                POLICY_REISSUE_VERSION_ID,
                POLICY_DOCUMENT_ID,
                "d" * 64,
            ),
        )
        connection.execute(
            """
            INSERT INTO extractions (
              id, document_version_id, extractor_name, extractor_version,
              extractor_config_hash, quality_rule_version, status, succeeded_at
            ) VALUES (%s, %s, 'synthetic', 'v1', %s, 'quality-v1',
                      'succeeded', clock_timestamp())
            """,
            (POLICY_EXTRACTION_ID, POLICY_VERSION_ID, "c" * 64),
        )
        connection.execute(
            """
            INSERT INTO evidence (
              id, household_space_id, document_version_id, extraction_id,
              content_sha256, physical_page, review_state
            ) VALUES (%s, %s, %s, %s, %s, 1, 'USER_CONFIRMED')
            """,
            (
                EVIDENCE_ID,
                HOUSEHOLD_ID,
                POLICY_VERSION_ID,
                POLICY_EXTRACTION_ID,
                "a" * 64,
            ),
        )
        connection.execute(
            """
            INSERT INTO document_batches (
              id, household_space_id, family_member_id, created_by, state, completed_at
            ) VALUES
              ('00000000-0000-4000-8000-000000000821', %s, %s, %s,
               'succeeded', clock_timestamp()),
              ('00000000-0000-4000-8000-000000000822', %s, %s, %s,
               'succeeded', clock_timestamp()),
              ('00000000-0000-4000-8000-000000000823', %s, %s, %s,
               'partial', NULL)
            """,
            (
                HOUSEHOLD_ID,
                MEMBER_A_ID,
                USER_ID,
                HOUSEHOLD_ID,
                MEMBER_B_ID,
                USER_ID,
                HOUSEHOLD_ID,
                MEMBER_A_ID,
                USER_ID,
            ),
        )
        connection.execute(
            """
            INSERT INTO document_batch_items (
              id, batch_id, document_id, source_id, source_key, display_label,
              document_kind, state, processed_document_version_id,
              available_at, completed_at
            ) VALUES
              (%s, '00000000-0000-4000-8000-000000000821', %s, %s,
               'synthetic/inventory-policy.pdf', 'Sample Policy', 'policy',
               'succeeded', %s, clock_timestamp(), clock_timestamp()),
              (%s, '00000000-0000-4000-8000-000000000821', %s, %s,
               'synthetic/inventory-terms.pdf', 'Sample Terms', 'terms',
               'succeeded', %s, clock_timestamp(), clock_timestamp()),
              (%s, '00000000-0000-4000-8000-000000000822', %s, %s,
               'synthetic/inventory-terms-copy.pdf', 'Sample Terms Copy', 'terms',
               'succeeded', %s, clock_timestamp(), clock_timestamp()),
              (%s, '00000000-0000-4000-8000-000000000822', %s, %s,
               'synthetic/inventory-policy-reissue.pdf', 'Sample Policy Reissue',
               'policy', 'succeeded', %s, clock_timestamp(), clock_timestamp()),
              (%s, '00000000-0000-4000-8000-000000000823', NULL, %s,
               'synthetic/inventory-locked.pdf', 'Encrypted Policy', 'policy',
               'password_required', NULL, clock_timestamp(), NULL)
            """,
            (
                POLICY_BATCH_ITEM_ID,
                POLICY_DOCUMENT_ID,
                "d" * 64,
                POLICY_VERSION_ID,
                TERMS_BATCH_ITEM_ID,
                TERMS_DOCUMENT_ID,
                "e" * 64,
                TERMS_VERSION_ID,
                OTHER_MEMBER_ITEM_ID,
                TERMS_DOCUMENT_ID,
                "f" * 64,
                TERMS_VERSION_ID,
                POLICY_REISSUE_ITEM_ID,
                POLICY_DOCUMENT_ID,
                "9" * 64,
                POLICY_REISSUE_VERSION_ID,
                LOCKED_ITEM_ID,
                "1" * 64,
            ),
        )
        connection.execute(
            """
            INSERT INTO policy_contracts (
              id, household_space_id, source_document_version_id, source_evidence_id,
              insurer_display, insurer_key, product_display, product_key, status
            ) VALUES (%s, %s, %s, %s, 'Sample Insurer', 'sample-insurer',
                      'Sample Policy', 'sample-policy', 'unknown')
            """,
            (POLICY_ID, HOUSEHOLD_ID, POLICY_VERSION_ID, EVIDENCE_ID),
        )
        connection.execute(
            """
            INSERT INTO policy_parties (
              household_space_id, policy_contract_id, family_member_id, role, evidence_id
            ) VALUES (%s, %s, %s, 'primary_insured', %s)
            """,
            (HOUSEHOLD_ID, POLICY_ID, MEMBER_A_ID, EVIDENCE_ID),
        )


def test_postgresql_inventory_enforces_authority_scope_and_conflict_states() -> None:
    database_url = _database_url()
    _seed(database_url)
    context = AuthContext(
        user_id=USER_ID,
        household_space_id=HOUSEHOLD_ID,
        session_id=UUID("00000000-0000-4000-8000-000000000899"),
        needs_reauthentication=False,
    )
    service = InsuranceDocumentService(
        context,
        InsuranceDocumentRepository(database_url),
    )

    policy_component = service.create_component(
        MEMBER_A_ID,
        document_batch_item_id=POLICY_BATCH_ITEM_ID,
        role="policy",
        page_start=1,
        page_end=2,
        evidence_id=EVIDENCE_ID,
        review_state="USER_CONFIRMED",
    )
    terms_component = service.create_component(
        MEMBER_A_ID,
        document_batch_item_id=TERMS_BATCH_ITEM_ID,
        role="terms",
        page_start=1,
        page_end=3,
        evidence_id=None,
        review_state="USER_CONFIRMED",
    )
    overlap = service.create_component(
        MEMBER_A_ID,
        document_batch_item_id=TERMS_BATCH_ITEM_ID,
        role="terms",
        page_start=3,
        page_end=5,
        evidence_id=None,
        review_state="USER_CONFIRMED",
    )
    assert overlap.review_state == "CONFLICT"

    document_set = service.create_document_set(
        MEMBER_A_ID,
        policy_contract_id=POLICY_ID,
        insurer_display="Untrusted label",
        product_display="Untrusted product",
        display_label="Untrusted display",
    )
    assert document_set.insurer_display == "Sample Insurer"
    assert document_set.product_display == "Sample Policy"
    service.attach_set_item(
        document_set.id,
        insurance_document_component_id=policy_component.id,
        match_state="USER_CONFIRMED",
        evidence_id=EVIDENCE_ID,
        expected_set_version=1,
    )
    service.attach_set_item(
        document_set.id,
        insurance_document_component_id=terms_component.id,
        match_state="USER_CONFIRMED",
        evidence_id=None,
        expected_set_version=2,
    )
    shared_terms_set = service.create_document_set(
        MEMBER_A_ID,
        policy_contract_id=None,
        insurer_display="Sample Insurer",
        product_display="Sample Shared Terms",
        display_label="Sample shared terms",
    )
    service.attach_set_item(
        shared_terms_set.id,
        insurance_document_component_id=terms_component.id,
        match_state="USER_CONFIRMED",
        evidence_id=None,
        expected_set_version=1,
    )
    with pytest.raises(PolicyStateConflict):
        service.attach_set_item(
            document_set.id,
            insurance_document_component_id=overlap.id,
            match_state="USER_CONFIRMED",
            evidence_id=None,
            expected_set_version=3,
        )

    wrong_policy_component = service.create_component(
        MEMBER_A_ID,
        document_batch_item_id=TERMS_BATCH_ITEM_ID,
        role="policy",
        page_start=4,
        page_end=5,
        evidence_id=None,
        review_state="USER_CONFIRMED",
    )
    with pytest.raises(EvidenceInvalid):
        service.attach_set_item(
            document_set.id,
            insurance_document_component_id=wrong_policy_component.id,
            match_state="USER_CONFIRMED",
            evidence_id=None,
            expected_set_version=3,
        )

    other_member_component = service.create_component(
        MEMBER_B_ID,
        document_batch_item_id=OTHER_MEMBER_ITEM_ID,
        role="terms",
        page_start=1,
        page_end=2,
        evidence_id=None,
        review_state="USER_CONFIRMED",
    )
    with pytest.raises(PolicyStateConflict):
        service.attach_set_item(
            document_set.id,
            insurance_document_component_id=other_member_component.id,
            match_state="USER_CONFIRMED",
            evidence_id=None,
            expected_set_version=3,
        )

    inventory = service.get_inventory(MEMBER_A_ID)
    assert inventory.summary.certificate_backed_policies == 1
    assert inventory.summary.certificate_and_terms == 1
    assert inventory.summary.certificate_only == 0
    assert inventory.summary.pairing_conflicts == 1
    assert inventory.summary.terms_only_documents == 2
    assert inventory.summary.unreadable_documents == 1
    assert inventory.unreadable_sources[0].document_batch_item_id == LOCKED_ITEM_ID
    assert inventory.unreadable_sources[0].source_kind == "policy"
    assert inventory.unreadable_sources[0].processing_state == "PASSWORD_REQUIRED"
    assert inventory.registered_policies[0].policy.status == "unknown"
    policy_document = next(
        document
        for document in inventory.registered_policies[0].documents
        if document.role == "policy"
    )
    assert policy_document.items[0].component.duplicate_state == "UNIQUE"
    assert inventory.unregistered_document_sets[0].primary_classification == "TERMS_ONLY"
    assert terms_component.id not in {item.id for item in inventory.unpaired_components}
    assert overlap.id in {item.id for item in inventory.unpaired_components}
    projected_overlap = next(
        item for item in inventory.unpaired_components if item.id == overlap.id
    )
    assert projected_overlap.duplicate_state == "CROSS_MEMBER_COPY_POSSIBLE"

    service.delete_document_set(document_set.id, expected_version=3)
    after_delete = service.get_inventory(MEMBER_A_ID)
    assert after_delete.summary.certificate_only == 1
    assert after_delete.summary.certificate_and_terms == 0
    with pytest.raises(PolicyStateConflict):
        service.delete_document_set(document_set.id, expected_version=4)
