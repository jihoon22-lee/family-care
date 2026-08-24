"""Synthetic PostgreSQL proof for the Worker-to-policy-candidate boundary.

The candidate API modules are intentionally imported here even before Task 3
implements them.  The test contract assumes ``CandidateRepository`` is the
Worker ``CandidateBatchPublisher`` (``publish(result, evidence)``) and that
``CandidateReviewService`` accepts that repository while receiving the
server-derived ``HouseholdScope`` on each use case.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.errors import ApiBoundaryError
from familycare_api.policies.candidate_models import (
    CandidateConfirmationRequest,
    CandidateCorrectionRequest,
)
from familycare_api.policies.candidate_repository import CandidateRepository
from familycare_api.policies.candidate_service import CandidateReviewService
from familycare_worker.ai.provider import EvidenceSlice
from familycare_worker.runner import PolicyCandidatePipelineRunner
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from workers.analyzer.tests.fixtures.policy_ai_responses import (
    VALID_VERIFIED,
    VERIFIER_NEEDS_REVIEW,
    FakeProvider,
)

pytestmark = pytest.mark.integration

SYNTHETIC_ADMIN_ID = UUID("00000000-0000-4000-8000-000000000901")
SYNTHETIC_CANDIDATE_ID = UUID("00000000-0000-4000-8000-000000000902")
SYNTHETIC_PRIVATE_MARKERS = (
    "synthetic-private-password",
    "/synthetic/private/policy.pdf",
    "synthetic-policy-number-private",
    "synthetic document body private",
)
FORBIDDEN_RESPONSE_KEYS = (
    "absolute_path",
    "archive_key",
    "password",
    "policy_number",
    "raw_pdf",
    "raw_provider_response",
    "source_path",
)

_CANDIDATE_TABLES = (
    "analysis_candidate_evidence",
    "analysis_candidate_fields",
    "analysis_candidate_versions",
)
_RESET_TABLES = _CANDIDATE_TABLES + (
    "policy_status_snapshots",
    "riders",
    "policy_parties",
    "policy_contracts",
    "evidence",
    "family_members",
    "household_spaces",
    "extraction_cells",
    "extraction_tables",
    "extraction_blocks",
    "extraction_pages",
    "extractions",
    "analysis_jobs",
    "document_versions",
    "documents",
)


def _psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@dataclass(frozen=True)
class _Seed:
    scope_a: HouseholdScope
    scope_b: HouseholdScope
    policy_document_version_id: UUID
    terms_document_version_id: UUID
    policy_evidence_id: UUID
    terms_evidence_id: UUID


def _reset_database(database_url: str) -> None:
    table_list = ", ".join(_RESET_TABLES)
    with psycopg.connect(_psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(f"TRUNCATE TABLE {table_list} RESTART IDENTITY")


def _seed(database_url: str) -> _Seed:
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        household_a = connection.execute(
            """
            INSERT INTO household_spaces (space_key, display_name)
            VALUES ('synthetic-candidate-household-a', 'Synthetic Household A')
            RETURNING id
            """
        ).fetchone()
        household_b = connection.execute(
            """
            INSERT INTO household_spaces (space_key, display_name)
            VALUES ('synthetic-candidate-household-b', 'Synthetic Household B')
            RETURNING id
            """
        ).fetchone()
        policy_document = connection.execute(
            """
            INSERT INTO documents (source_key, document_kind, status)
            VALUES ('synthetic/candidate-policy.pdf', 'policy', 'ready')
            RETURNING id
            """
        ).fetchone()
        terms_document = connection.execute(
            """
            INSERT INTO documents (source_key, document_kind, status)
            VALUES ('synthetic/candidate-terms.pdf', 'terms', 'ready')
            RETURNING id
            """
        ).fetchone()
        assert household_a and household_b and policy_document and terms_document

        policy_version = connection.execute(
            """
            INSERT INTO document_versions (
                document_id, version_number, content_sha256, byte_size, page_count
            ) VALUES (%s, 1, %s, 100, 2)
            RETURNING id
            """,
            (policy_document["id"], "a" * 64),
        ).fetchone()
        terms_version = connection.execute(
            """
            INSERT INTO document_versions (
                document_id, version_number, content_sha256, byte_size, page_count
            ) VALUES (%s, 1, %s, 100, 2)
            RETURNING id
            """,
            (terms_document["id"], "b" * 64),
        ).fetchone()
        assert policy_version and terms_version

        extraction_ids: list[UUID] = []
        for document_version, config_hash in (
            (policy_version, "c" * 64),
            (terms_version, "d" * 64),
        ):
            extraction = connection.execute(
                """
                INSERT INTO extractions (
                    document_version_id, extractor_name, extractor_version,
                    extractor_config_hash, quality_rule_version, status, succeeded_at
                ) VALUES (%s, 'synthetic', '1', %s, 'quality-v1', 'succeeded', clock_timestamp())
                RETURNING id
                """,
                (document_version["id"], config_hash),
            ).fetchone()
            assert extraction
            extraction_id = cast(UUID, extraction["id"])
            extraction_ids.append(extraction_id)
            for page_number in (1, 2):
                connection.execute(
                    """
                    INSERT INTO extraction_pages (
                        extraction_id, page_number, width_points, height_points,
                        non_whitespace_chars, alphanumeric_ratio,
                        replacement_character_ratio, maximum_repeated_character_run,
                        classification
                    ) VALUES (%s, %s, 612, 792, 40, 0.8, 0, 1, 'TEXT_SUFFICIENT')
                    """,
                    (extraction_id, page_number),
                )

        def insert_evidence(
            household_id: UUID,
            document_version_id: UUID,
            extraction_id: UUID,
            content_sha256: str,
        ) -> UUID:
            row = connection.execute(
                """
                INSERT INTO evidence (
                    household_space_id, document_version_id, extraction_id,
                    content_sha256, physical_page, review_state
                ) VALUES (%s, %s, %s, %s, 1, 'USER_CONFIRMED')
                RETURNING id
                """,
                (household_id, document_version_id, extraction_id, content_sha256),
            ).fetchone()
            assert row
            return cast(UUID, row["id"])

        policy_evidence_id = insert_evidence(
            cast(UUID, household_a["id"]),
            cast(UUID, policy_version["id"]),
            extraction_ids[0],
            "a" * 64,
        )
        terms_evidence_id = insert_evidence(
            cast(UUID, household_a["id"]),
            cast(UUID, terms_version["id"]),
            extraction_ids[1],
            "b" * 64,
        )

    return _Seed(
        scope_a=HouseholdScope(cast(UUID, household_a["id"])),
        scope_b=HouseholdScope(cast(UUID, household_b["id"])),
        policy_document_version_id=cast(UUID, policy_version["id"]),
        terms_document_version_id=cast(UUID, terms_version["id"]),
        policy_evidence_id=policy_evidence_id,
        terms_evidence_id=terms_evidence_id,
    )


@pytest.fixture()
def database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required for PostgreSQL integration tests")
    _reset_database(value)
    return value


@pytest.fixture()
def seed(database_url: str) -> _Seed:
    return _seed(database_url)


def _service(database_url: str) -> CandidateReviewService:
    return CandidateReviewService(CandidateRepository(database_url))


def _worker_evidence(seed: _Seed, *, terms_only: bool = False) -> tuple[EvidenceSlice, ...]:
    if terms_only:
        return (
            EvidenceSlice(
                evidence_id=seed.terms_evidence_id,
                document_version_id=seed.terms_document_version_id,
                page=1,
                text="Sample Wellness Benefit is described as an available option.",
                bbox=(10.0, 20.0, 220.0, 80.0),
                document_kind="terms",
            ),
        )
    return (
        EvidenceSlice(
            evidence_id=seed.policy_evidence_id,
            document_version_id=seed.policy_document_version_id,
            page=1,
            text="Sample Policy confirms the synthetic coverage record.",
            bbox=(10.0, 20.0, 220.0, 80.0),
        ),
    )


def _structured_payload(
    evidence_id: UUID,
    *,
    candidate_kind: str,
) -> dict[str, object]:
    if candidate_kind == "policy_contract":
        fields = [
            {"field_id": "insurer", "value": "Sample Insurer", "evidence_ids": [str(evidence_id)]},
            {
                "field_id": "product_name",
                "value": "Sample Policy",
                "evidence_ids": [str(evidence_id)],
            },
            {
                "field_id": "contract_start",
                "value": "2026-01-01",
                "evidence_ids": [str(evidence_id)],
            },
            {"field_id": "contract_end", "value": "2026-12-31", "evidence_ids": [str(evidence_id)]},
            {"field_id": "policy_status", "value": "active", "evidence_ids": [str(evidence_id)]},
        ]
    else:
        fields = [
            {"field_id": "rider_name", "value": "Sample Rider", "evidence_ids": [str(evidence_id)]},
            {"field_id": "rider_key", "value": "sample-rider", "evidence_ids": [str(evidence_id)]},
            {"field_id": "benefit_type", "value": "fixed", "evidence_ids": [str(evidence_id)]},
            {"field_id": "sum_assured", "value": 1000, "evidence_ids": [str(evidence_id)]},
            {"field_id": "currency", "value": "USD", "evidence_ids": [str(evidence_id)]},
            {"field_id": "rider_status", "value": "active", "evidence_ids": [str(evidence_id)]},
        ]
    return {
        "schema_version": "1",
        "candidate_id": str(SYNTHETIC_CANDIDATE_ID),
        "candidate_kind": candidate_kind,
        "fields": fields,
    }


def _persist_worker_candidate(
    database_url: str,
    seed: _Seed,
    *,
    candidate_kind: str = "rider",
    terms_only: bool = False,
    needs_review: bool = True,
) -> tuple[CandidateRepository, Any, tuple[EvidenceSlice, ...]]:
    evidence = _worker_evidence(seed, terms_only=terms_only)
    evidence_id = evidence[0].evidence_id
    structured = _structured_payload(evidence_id, candidate_kind=candidate_kind)
    verified = copy.deepcopy(VALID_VERIFIED)
    verified["candidate_id"] = str(SYNTHETIC_CANDIDATE_ID)
    verified["evidence_ids"] = [str(evidence_id)]
    verifier = copy.deepcopy(VERIFIER_NEEDS_REVIEW if needs_review else verified)
    verifier["candidate_id"] = str(SYNTHETIC_CANDIDATE_ID)
    verifier["evidence_ids"] = [str(evidence_id)]
    provider = FakeProvider(structurer=structured, verifier=verifier)
    repository = CandidateRepository(database_url)
    result = PolicyCandidatePipelineRunner(provider=provider, publisher=repository).run(
        evidence=evidence
    )
    return repository, result, evidence


def _candidate_rows(database_url: str, scope: HouseholdScope) -> list[dict[str, Any]]:
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        return connection.execute(
            """
            SELECT id, parent_version_id, version, status
            FROM analysis_candidate_versions
            WHERE household_space_id = %s
            ORDER BY version, id
            """,
            (scope.household_space_id,),
        ).fetchall()


def _ledger_count(database_url: str, table: str, scope: HouseholdScope) -> int:
    if table not in {"policy_contracts", "riders"}:
        raise AssertionError("test helper only accepts ledger projection tables")
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        row = connection.execute(
            f"SELECT count(*) FROM {table} WHERE household_space_id = %s AND deleted_at IS NULL",
            (scope.household_space_id,),
        ).fetchone()
    assert row
    return int(row[0])


def test_worker_candidate_is_persisted_as_a_scoped_review_item(
    database_url: str,
    seed: _Seed,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    _, result, _ = _persist_worker_candidate(database_url, seed)
    assert result.classification == "NEEDS_REVIEW"

    service = _service(database_url)
    items = service.list_review_items(scope=seed.scope_a)
    assert len(items) == 1
    item = items[0]
    assert item.status == "NEEDS_REVIEW"
    assert item.candidate_kind == "rider"
    assert item.evidence[0].document_version_id == seed.policy_document_version_id
    assert item.fields[0].field_id == "rider_name"
    serialized = json.dumps(item.model_dump(mode="json"), sort_keys=True)
    assert all(key not in serialized for key in FORBIDDEN_RESPONSE_KEYS)
    assert all(
        marker.lower() not in (serialized + caplog.text).lower()
        for marker in SYNTHETIC_PRIVATE_MARKERS
    )


def test_user_correction_creates_a_child_without_overwriting_the_parent(
    database_url: str,
    seed: _Seed,
) -> None:
    _, _, _ = _persist_worker_candidate(database_url, seed)
    service = _service(database_url)
    original = service.list_review_items(scope=seed.scope_a)[0]

    corrected = service.correct_field(
        scope=seed.scope_a,
        review_item_id=original.review_item_id,
        request=CandidateCorrectionRequest(
            expected_version=original.expected_version,
            field_id="rider_name",
            value="Sample Rider Corrected",
            evidence_id=seed.policy_evidence_id,
        ),
        actor_id=SYNTHETIC_ADMIN_ID,
    )
    assert corrected.expected_version == original.expected_version + 1
    rows = _candidate_rows(database_url, seed.scope_a)
    assert len(rows) == 2
    assert rows[0]["status"] == "NEEDS_REVIEW"
    assert rows[1]["parent_version_id"] == rows[0]["id"]

    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        parent_field = connection.execute(
            """
            SELECT value
            FROM analysis_candidate_fields
            WHERE candidate_version_id = %s AND field_id = 'rider_name'
            """,
            (rows[0]["id"],),
        ).fetchone()
        child_field = connection.execute(
            """
            SELECT value
            FROM analysis_candidate_fields
            WHERE candidate_version_id = %s AND field_id = 'rider_name'
            """,
            (rows[1]["id"],),
        ).fetchone()
    assert parent_field and parent_field["value"] == "Sample Rider"
    assert child_field and child_field["value"] == "Sample Rider Corrected"


def test_stale_correction_is_a_value_free_conflict(
    database_url: str,
    seed: _Seed,
) -> None:
    _, _, _ = _persist_worker_candidate(database_url, seed)
    service = _service(database_url)
    original = service.list_review_items(scope=seed.scope_a)[0]
    request = CandidateCorrectionRequest(
        expected_version=original.expected_version,
        field_id="rider_name",
        value="Sample Rider Corrected",
        evidence_id=seed.policy_evidence_id,
    )
    service.correct_field(
        scope=seed.scope_a,
        review_item_id=original.review_item_id,
        request=request,
        actor_id=SYNTHETIC_ADMIN_ID,
    )

    with pytest.raises(ApiBoundaryError) as raised:
        service.correct_field(
            scope=seed.scope_a,
            review_item_id=original.review_item_id,
            request=request,
            actor_id=SYNTHETIC_ADMIN_ID,
        )
    assert raised.value.error_code == "VERSION_CONFLICT"
    assert str(original.review_item_id) not in str(raised.value)


def test_cross_household_review_item_is_not_found(
    database_url: str,
    seed: _Seed,
) -> None:
    _, _, _ = _persist_worker_candidate(database_url, seed)
    service = _service(database_url)
    item = service.list_review_items(scope=seed.scope_a)[0]

    try:
        hidden = service.get_review_item(scope=seed.scope_b, review_item_id=item.review_item_id)
    except ApiBoundaryError as error:
        assert error.error_code == "REVIEW_ITEM_NOT_FOUND"
    else:
        assert hidden is None
    assert service.list_review_items(scope=seed.scope_b) == []


def test_ai_verified_policy_candidate_is_published(
    database_url: str,
    seed: _Seed,
) -> None:
    _, result, _ = _persist_worker_candidate(
        database_url,
        seed,
        candidate_kind="policy_contract",
        needs_review=False,
    )
    assert result.classification == "SUCCESS"
    assert result.candidates[0].status == "AI_VERIFIED"
    assert _ledger_count(database_url, "policy_contracts", seed.scope_a) == 1


def test_user_confirmed_policy_candidate_is_published(
    database_url: str,
    seed: _Seed,
) -> None:
    _, _, _ = _persist_worker_candidate(
        database_url,
        seed,
        candidate_kind="policy_contract",
        needs_review=True,
    )
    service = _service(database_url)
    item = service.list_review_items(scope=seed.scope_a)[0]
    corrected = service.correct_field(
        scope=seed.scope_a,
        review_item_id=item.review_item_id,
        request=CandidateCorrectionRequest(
            expected_version=item.expected_version,
            field_id="product_name",
            value="Sample Policy Corrected",
            evidence_id=seed.policy_evidence_id,
        ),
        actor_id=SYNTHETIC_ADMIN_ID,
    )
    confirmed = service.confirm(
        scope=seed.scope_a,
        review_item_id=corrected.review_item_id,
        request=CandidateConfirmationRequest(expected_version=corrected.expected_version),
        actor_id=SYNTHETIC_ADMIN_ID,
    )
    assert confirmed.status == "USER_CONFIRMED"
    assert _ledger_count(database_url, "policy_contracts", seed.scope_a) == 1


def test_terms_only_rider_never_creates_an_enrolled_rider(
    database_url: str,
    seed: _Seed,
) -> None:
    _, result, _ = _persist_worker_candidate(database_url, seed, terms_only=True)
    assert result.candidates[0].status == "NEEDS_REVIEW"
    service = _service(database_url)
    item = service.list_review_items(scope=seed.scope_a)[0]
    assert "TERMS_ONLY_RIDER" in {issue.code for issue in item.issues}

    confirmed = service.confirm(
        scope=seed.scope_a,
        review_item_id=item.review_item_id,
        request=CandidateConfirmationRequest(expected_version=item.expected_version),
        actor_id=SYNTHETIC_ADMIN_ID,
    )
    assert confirmed.status == "USER_CONFIRMED"
    assert _ledger_count(database_url, "riders", seed.scope_a) == 0


def test_http_not_found_boundary_does_not_echo_candidate_or_sensitive_values(
    database_url: str,
    seed: _Seed,
) -> None:
    _, _, _ = _persist_worker_candidate(database_url, seed)
    item = _service(database_url).list_review_items(scope=seed.scope_a)[0]

    from familycare_api.common.scope import resolve_household_scope
    from familycare_api.main import create_app

    app = create_app(enable_synthetic_ingestion=False)
    app.dependency_overrides[resolve_household_scope] = lambda: seed.scope_b
    with TestClient(app) as client:
        response = client.get(f"/api/v1/review-items/{item.review_item_id}")
    assert response.status_code == 404
    assert response.json() == {
        "error_code": "REVIEW_ITEM_NOT_FOUND",
        "message": "review item not found",
    }
    body = response.text.lower()
    assert str(item.review_item_id).lower() not in body
    assert all(marker.lower() not in body for marker in SYNTHETIC_PRIVATE_MARKERS)
