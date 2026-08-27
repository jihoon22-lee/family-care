"""Synthetic confirmation proof for private policy candidates and family projection."""

from __future__ import annotations

import os
from datetime import date
from uuid import UUID

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.main import create_app
from familycare_api.policies.candidate_errors import InvalidCandidateCorrection
from familycare_api.policies.candidate_models import CandidateConfirmationRequest
from familycare_api.policies.candidate_repository import CandidateRepository
from familycare_api.policies.candidate_service import CandidateReviewService
from familycare_worker.ai.provider import EvidenceSlice
from familycare_worker.ai.schemas import CandidateField, CandidatePipelineResult, PolicyCandidate
from familycare_worker.policy_candidates import PolicyCandidatePublisher
from familycare_worker.policy_jobs import PolicyStructuringJobQueue
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

pytestmark = pytest.mark.integration

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000000901")
USER_ID = UUID("00000000-0000-4000-8000-000000000902")
MEMBER_ID = UUID("00000000-0000-4000-8000-000000000903")
BATCH_ID = UUID("00000000-0000-4000-8000-000000000904")
BATCH_ITEM_ID = UUID("00000000-0000-4000-8000-000000000905")
DOCUMENT_ID = UUID("00000000-0000-4000-8000-000000000906")
VERSION_ID = UUID("00000000-0000-4000-8000-000000000907")
EXTRACTION_ID = UUID("00000000-0000-4000-8000-000000000908")
EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000909")
JOB_ID = UUID("00000000-0000-4000-8000-000000000910")
AGGREGATE_ID = UUID("00000000-0000-4000-8000-000000000911")
CONTRACT_CANDIDATE_ID = UUID("00000000-0000-4000-8000-000000000912")
RIDER_CANDIDATE_ID = UUID("00000000-0000-4000-8000-000000000913")


def _database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required")
    return value


def _psycopg_url(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _reset(database_url: str) -> None:
    with psycopg.connect(_psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            """
            TRUNCATE TABLE household_spaces, app_users, family_members,
              document_batches, document_batch_items, documents, document_versions,
              extractions, evidence, policy_structuring_jobs,
              analysis_candidate_versions, policy_contracts, policy_parties, riders
            RESTART IDENTITY CASCADE
            """
        )


def _seed(database_url: str) -> None:
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO household_spaces (id, space_key, display_name)
            VALUES (%s, 'synthetic-private-confirmation', 'Synthetic Household')
            """,
            (HOUSEHOLD_ID,),
        )
        connection.execute(
            """
            INSERT INTO app_users (
              id, household_space_id, username, display_name, password_hash
            ) VALUES (%s, %s, 'synthetic-private-admin', 'Admin A', '$argon2id$synthetic')
            """,
            (USER_ID, HOUSEHOLD_ID),
        )
        connection.execute(
            """
            INSERT INTO family_members (
              id, household_space_id, display_name, internal_alias
            ) VALUES (%s, %s, 'Family Member A', 'family-member-a')
            """,
            (MEMBER_ID, HOUSEHOLD_ID),
        )
        connection.execute(
            """
            INSERT INTO documents (id, source_key, document_kind, status, page_count)
            VALUES (%s, 'synthetic/private-confirmation.pdf', 'policy', 'ready', 1)
            """,
            (DOCUMENT_ID,),
        )
        connection.execute(
            """
            INSERT INTO document_versions (
              id, document_id, version_number, content_sha256, byte_size, page_count
            ) VALUES (%s, %s, 1, %s, 128, 1)
            """,
            (VERSION_ID, DOCUMENT_ID, "a" * 64),
        )
        connection.execute(
            """
            INSERT INTO extractions (
              id, document_version_id, extractor_name, extractor_version,
              extractor_config_hash, quality_rule_version, status, succeeded_at
            ) VALUES (%s, %s, 'synthetic', 'synthetic-v1', %s,
                      'quality-v1', 'succeeded', clock_timestamp())
            """,
            (EXTRACTION_ID, VERSION_ID, "b" * 64),
        )
        connection.execute(
            """
            INSERT INTO evidence (
              id, household_space_id, document_version_id, extraction_id,
              content_sha256, physical_page, review_state
            ) VALUES (%s, %s, %s, %s, %s, 1, 'NEEDS_REVIEW')
            """,
            (EVIDENCE_ID, HOUSEHOLD_ID, VERSION_ID, EXTRACTION_ID, "a" * 64),
        )
        connection.execute(
            """
            INSERT INTO document_batches (
              id, household_space_id, family_member_id, created_by, state, completed_at
            ) VALUES (%s, %s, %s, %s, 'succeeded', clock_timestamp())
            """,
            (BATCH_ID, HOUSEHOLD_ID, MEMBER_ID, USER_ID),
        )
        connection.execute(
            """
            INSERT INTO document_batch_items (
              id, batch_id, document_id, source_id, source_key, display_label,
              document_kind, state, available_at, completed_at
            ) VALUES (%s, %s, %s, %s, 'synthetic/private-confirmation.pdf',
                      'Sample Policy', 'policy', 'succeeded', clock_timestamp(),
                      clock_timestamp())
            """,
            (BATCH_ITEM_ID, BATCH_ID, DOCUMENT_ID, "c" * 64),
        )
        connection.execute(
            """
            INSERT INTO policy_structuring_jobs (
              id, household_space_id, batch_item_id, family_member_id,
              document_version_id, extraction_id, policy_aggregate_id,
              state, pipeline_version, available_at, lease_owner,
              lease_expires_at, heartbeat_at, attempts, max_attempts
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'running',
                      'policy-candidate-batch-v2', clock_timestamp(), 'worker-a',
                      clock_timestamp() + interval '3 minutes', clock_timestamp(), 1, 5)
            """,
            (
                JOB_ID,
                HOUSEHOLD_ID,
                BATCH_ITEM_ID,
                MEMBER_ID,
                VERSION_ID,
                EXTRACTION_ID,
                AGGREGATE_ID,
            ),
        )


def _field(field_id: str, value: object) -> CandidateField:
    return CandidateField.model_validate(
        {"field_id": field_id, "value": value, "evidence_ids": (EVIDENCE_ID,)},
        strict=True,
    )


def _candidate_batch() -> CandidatePipelineResult:
    return CandidatePipelineResult(
        classification="SUCCESS",
        candidates=(
            PolicyCandidate(
                candidate_id=CONTRACT_CANDIDATE_ID,
                candidate_kind="policy_contract",
                status="AI_VERIFIED",
                fields=(
                    _field("insurer", "Sample Insurer"),
                    _field("product_name", "Sample Plan"),
                    _field("contract_start", "2026-01-01"),
                    _field("contract_end", "2026-12-31"),
                    _field("policy_status", "active"),
                ),
                issue_codes=(),
                provider_request_ids=("synthetic-structurer", "synthetic-policy-verifier"),
            ),
            PolicyCandidate(
                candidate_id=RIDER_CANDIDATE_ID,
                candidate_kind="rider",
                status="AI_VERIFIED",
                fields=(
                    _field("rider_name", "Sample Rider"),
                    _field("rider_key", "sample-rider"),
                    _field("benefit_type", "fixed"),
                    _field("sum_assured", 1000),
                    _field("currency", "KRW"),
                    _field("coverage_start", "2026-01-01"),
                    _field("coverage_end", "2026-12-31"),
                    _field("renewable", False),
                    _field("rider_status", "active"),
                ),
                issue_codes=(),
                provider_request_ids=("synthetic-structurer", "synthetic-rider-verifier"),
            ),
        ),
    )


def _publish_candidates(database_url: str) -> CandidateReviewService:
    job = PolicyStructuringJobQueue(database_url).get_job(JOB_ID)
    assert job is not None
    PolicyCandidatePublisher(database_url).publish(
        job=job,
        worker_id="worker-a",
        result=_candidate_batch(),
        evidence=(
            EvidenceSlice(
                evidence_id=EVIDENCE_ID,
                document_version_id=VERSION_ID,
                page=1,
                text="Sample minimized policy evidence.",
                bbox=None,
                document_kind="policy",
            ),
        ),
    )
    return CandidateReviewService(CandidateRepository(database_url))


def test_user_confirmation_promotes_evidence_and_publishes_family_policy_aggregate() -> None:
    database_url = _database_url()
    _reset(database_url)
    _seed(database_url)
    service = _publish_candidates(database_url)
    scope = HouseholdScope(HOUSEHOLD_ID)
    review_items = service.list_review_items(scope=scope)
    by_kind = {item.candidate_kind: item for item in review_items}
    assert set(by_kind) == {"policy_contract", "rider"}
    assert all(item.status == "NEEDS_REVIEW" for item in review_items)

    with psycopg.connect(_psycopg_url(database_url)) as connection:
        before = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM policy_contracts WHERE household_space_id = %s),
              (SELECT count(*) FROM policy_parties WHERE household_space_id = %s),
              (SELECT count(*) FROM riders WHERE household_space_id = %s)
            """,
            (HOUSEHOLD_ID, HOUSEHOLD_ID, HOUSEHOLD_ID),
        ).fetchone()
    assert before == (0, 0, 0)

    contract = by_kind["policy_contract"]
    confirmed_contract = service.confirm(
        scope=scope,
        review_item_id=contract.review_item_id,
        request=CandidateConfirmationRequest(expected_version=contract.expected_version),
        actor_id=USER_ID,
    )
    assert confirmed_contract.status == "USER_CONFIRMED"

    rider = by_kind["rider"]
    confirmed_rider = service.confirm(
        scope=scope,
        review_item_id=rider.review_item_id,
        request=CandidateConfirmationRequest(expected_version=rider.expected_version),
        actor_id=USER_ID,
    )
    assert confirmed_rider.status == "USER_CONFIRMED"

    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        evidence = connection.execute(
            "SELECT review_state FROM evidence WHERE id = %s AND household_space_id = %s",
            (EVIDENCE_ID, HOUSEHOLD_ID),
        ).fetchone()
        policy = connection.execute(
            """
            SELECT id, source_document_version_id, source_evidence_id
            FROM policy_contracts
            WHERE id = %s AND household_space_id = %s AND deleted_at IS NULL
            """,
            (AGGREGATE_ID, HOUSEHOLD_ID),
        ).fetchone()
        party = connection.execute(
            """
            SELECT policy_contract_id, family_member_id, role,
                   effective_from, effective_to, evidence_id
            FROM policy_parties
            WHERE policy_contract_id = %s AND household_space_id = %s
              AND deleted_at IS NULL
            """,
            (AGGREGATE_ID, HOUSEHOLD_ID),
        ).fetchone()
        stored_rider = connection.execute(
            """
            SELECT policy_contract_id, source_evidence_id, status
            FROM riders
            WHERE policy_contract_id = %s AND household_space_id = %s
              AND deleted_at IS NULL
            """,
            (AGGREGATE_ID, HOUSEHOLD_ID),
        ).fetchone()

    assert evidence == {"review_state": "USER_CONFIRMED"}
    assert policy == {
        "id": AGGREGATE_ID,
        "source_document_version_id": VERSION_ID,
        "source_evidence_id": EVIDENCE_ID,
    }
    assert party == {
        "policy_contract_id": AGGREGATE_ID,
        "family_member_id": MEMBER_ID,
        "role": "primary_insured",
        "effective_from": date(2026, 1, 1),
        "effective_to": date(2026, 12, 31),
        "evidence_id": EVIDENCE_ID,
    }
    assert stored_rider == {
        "policy_contract_id": AGGREGATE_ID,
        "source_evidence_id": EVIDENCE_ID,
        "status": "active",
    }

    app = create_app(enable_synthetic_ingestion=False)
    app.dependency_overrides[resolve_household_scope] = lambda: scope
    with TestClient(app) as client:
        response = client.get(f"/api/v1/policies/{AGGREGATE_ID}")
    assert response.status_code == 200
    body = response.json()
    assert body["source_evidence"]["review_state"] == "USER_CONFIRMED"
    assert len(body["parties"]) == 1
    assert body["parties"][0]["family_member_id"] == str(MEMBER_ID)
    assert body["parties"][0]["role"] == "primary_insured"
    assert body["parties"][0]["effective_from"] == "2026-01-01"
    assert body["parties"][0]["effective_to"] == "2026-12-31"
    assert body["parties"][0]["evidence"]["review_state"] == "USER_CONFIRMED"
    assert "source_key" not in response.text
    assert "synthetic/private-confirmation.pdf" not in response.text


def test_failed_private_rider_confirmation_rolls_back_evidence_promotion() -> None:
    database_url = _database_url()
    _reset(database_url)
    _seed(database_url)
    service = _publish_candidates(database_url)
    scope = HouseholdScope(HOUSEHOLD_ID)
    rider = next(
        item for item in service.list_review_items(scope=scope) if item.candidate_kind == "rider"
    )

    with pytest.raises(InvalidCandidateCorrection):
        service.confirm(
            scope=scope,
            review_item_id=rider.review_item_id,
            request=CandidateConfirmationRequest(expected_version=rider.expected_version),
            actor_id=USER_ID,
        )

    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        evidence = connection.execute(
            "SELECT review_state FROM evidence WHERE id = %s AND household_space_id = %s",
            (EVIDENCE_ID, HOUSEHOLD_ID),
        ).fetchone()
        current = connection.execute(
            """
            SELECT status, version
            FROM analysis_candidate_versions
            WHERE review_item_id = %s AND household_space_id = %s AND is_current
            """,
            (rider.review_item_id, HOUSEHOLD_ID),
        ).fetchone()
        rider_count = connection.execute(
            "SELECT count(*) AS count FROM riders WHERE household_space_id = %s",
            (HOUSEHOLD_ID,),
        ).fetchone()

    assert evidence == {"review_state": "NEEDS_REVIEW"}
    assert current == {"status": "NEEDS_REVIEW", "version": 1}
    assert rider_count == {"count": 0}
