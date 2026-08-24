"""PostgreSQL proof for the household-scoped policy ledger."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.main import create_app
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

pytestmark = pytest.mark.integration

_POLICY_TABLES = (
    "policy_status_snapshots",
    "riders",
    "policy_parties",
    "policy_contracts",
    "evidence",
    "family_members",
    "household_spaces",
)
_SYNTHETIC_PRIVATE_MARKERS = (
    "synthetic-private-password",
    "/synthetic/private/policy.pdf",
    "synthetic-policy-number-private",
    "synthetic document body private",
)


def _psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@dataclass(frozen=True)
class _Seed:
    scope_a: HouseholdScope
    scope_b: HouseholdScope
    policy_document_version_id: UUID
    terms_document_version_id: UUID
    source_evidence_id: UUID
    party_evidence_id: UUID
    terms_evidence_id: UUID


def _seed(database_url: str) -> _Seed:
    table_list = ", ".join(_POLICY_TABLES)
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        connection.execute(f"TRUNCATE TABLE {table_list}")
        household_a = connection.execute(
            """
            INSERT INTO household_spaces (space_key, display_name)
            VALUES ('synthetic-household-a', 'Synthetic Household A')
            RETURNING id
            """
        ).fetchone()
        household_b = connection.execute(
            """
            INSERT INTO household_spaces (space_key, display_name)
            VALUES ('synthetic-household-b', 'Synthetic Household B')
            RETURNING id
            """
        ).fetchone()
        policy_document = connection.execute(
            """
            INSERT INTO documents (source_key, document_kind, status)
            VALUES ('synthetic/ledger-policy.pdf', 'policy', 'ready')
            ON CONFLICT (source_key) WHERE deleted_at IS NULL
            DO UPDATE SET status = 'ready'
            RETURNING id
            """
        ).fetchone()
        terms_document = connection.execute(
            """
            INSERT INTO documents (source_key, document_kind, status)
            VALUES ('synthetic/ledger-terms.pdf', 'terms', 'ready')
            ON CONFLICT (source_key) WHERE deleted_at IS NULL
            DO UPDATE SET status = 'ready'
            RETURNING id
            """
        ).fetchone()
        assert household_a and household_b and policy_document and terms_document

        policy_version = connection.execute(
            """
            INSERT INTO document_versions (
                document_id, version_number, content_sha256, byte_size, page_count
            ) VALUES (%s, 1, %s, 100, 3)
            ON CONFLICT (document_id, content_sha256)
            DO UPDATE SET page_count = EXCLUDED.page_count
            RETURNING id
            """,
            (policy_document["id"], "a" * 64),
        ).fetchone()
        terms_version = connection.execute(
            """
            INSERT INTO document_versions (
                document_id, version_number, content_sha256, byte_size, page_count
            ) VALUES (%s, 1, %s, 100, 3)
            ON CONFLICT (document_id, content_sha256)
            DO UPDATE SET page_count = EXCLUDED.page_count
            RETURNING id
            """,
            (terms_document["id"], "b" * 64),
        ).fetchone()
        assert policy_version and terms_version

        policy_extraction = connection.execute(
            """
            INSERT INTO extractions (
                document_version_id, extractor_name, extractor_version,
                extractor_config_hash, quality_rule_version, status, succeeded_at
            ) VALUES (%s, 'synthetic', '1', %s, 'quality-v1', 'succeeded', clock_timestamp())
            ON CONFLICT (document_version_id, extractor_config_hash)
              WHERE status = 'succeeded'
            DO UPDATE SET succeeded_at = EXCLUDED.succeeded_at
            RETURNING id
            """,
            (policy_version["id"], "c" * 64),
        ).fetchone()
        terms_extraction = connection.execute(
            """
            INSERT INTO extractions (
                document_version_id, extractor_name, extractor_version,
                extractor_config_hash, quality_rule_version, status, succeeded_at
            ) VALUES (%s, 'synthetic', '1', %s, 'quality-v1', 'succeeded', clock_timestamp())
            ON CONFLICT (document_version_id, extractor_config_hash)
              WHERE status = 'succeeded'
            DO UPDATE SET succeeded_at = EXCLUDED.succeeded_at
            RETURNING id
            """,
            (terms_version["id"], "d" * 64),
        ).fetchone()
        assert policy_extraction and terms_extraction

        for extraction_id in (policy_extraction["id"], terms_extraction["id"]):
            for page_number in (1, 2, 3):
                connection.execute(
                    """
                    INSERT INTO extraction_pages (
                        extraction_id, page_number, width_points, height_points,
                        non_whitespace_chars, alphanumeric_ratio,
                        replacement_character_ratio, maximum_repeated_character_run,
                        classification
                    ) VALUES (%s, %s, 612, 792, 10, 0.8, 0, 1, 'TEXT_SUFFICIENT')
                    ON CONFLICT (extraction_id, page_number)
                    DO UPDATE SET classification = EXCLUDED.classification
                    """,
                    (extraction_id, page_number),
                )

        def insert_evidence(
            household_id: UUID,
            document_version_id: UUID,
            extraction_id: UUID,
            content_sha256: str,
            page: int,
        ) -> UUID:
            row = connection.execute(
                """
                INSERT INTO evidence (
                    household_space_id, document_version_id, extraction_id,
                    content_sha256, physical_page, review_state
                ) VALUES (%s, %s, %s, %s, %s, 'USER_CONFIRMED')
                RETURNING id
                """,
                (
                    household_id,
                    document_version_id,
                    extraction_id,
                    content_sha256,
                    page,
                ),
            ).fetchone()
            assert row
            return cast(UUID, row["id"])

        scope_a_id = household_a["id"]
        source_evidence_id = insert_evidence(
            scope_a_id,
            policy_version["id"],
            policy_extraction["id"],
            "a" * 64,
            1,
        )
        party_evidence_id = insert_evidence(
            scope_a_id,
            policy_version["id"],
            policy_extraction["id"],
            "a" * 64,
            2,
        )
        terms_evidence_id = insert_evidence(
            scope_a_id,
            terms_version["id"],
            terms_extraction["id"],
            "b" * 64,
            1,
        )
    return _Seed(
        scope_a=HouseholdScope(scope_a_id),
        scope_b=HouseholdScope(household_b["id"]),
        policy_document_version_id=policy_version["id"],
        terms_document_version_id=terms_version["id"],
        source_evidence_id=source_evidence_id,
        party_evidence_id=party_evidence_id,
        terms_evidence_id=terms_evidence_id,
    )


@pytest.fixture()
def database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required for PostgreSQL integration tests")
    return value


def _client(scope: HouseholdScope) -> TestClient:
    app = create_app(enable_synthetic_ingestion=False)
    app.dependency_overrides[resolve_household_scope] = lambda: scope
    return TestClient(app)


def _policy_request(seed: _Seed, family_member_id: str) -> dict[str, Any]:
    return {
        "source_document_version_id": str(seed.policy_document_version_id),
        "source_evidence_id": str(seed.source_evidence_id),
        "insurer_display": "Sample Insurer",
        "insurer_key": "sample-insurer",
        "product_display": "Sample Policy",
        "product_key": "sample-policy",
        "status": "unknown",
        "parties": [
            {
                "family_member_id": family_member_id,
                "role": "primary_insured",
                "evidence_id": str(seed.party_evidence_id),
            }
        ],
    }


def _insert_synthetic_rider(
    database_url: str,
    seed: _Seed,
    policy_id: str,
    *,
    evidence_id: UUID | None = None,
) -> UUID:
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        row = connection.execute(
            """
            INSERT INTO riders (
                household_space_id, policy_contract_id, source_evidence_id,
                display_name, normalized_key, benefit_type, insured_amount,
                currency, renewable, status
            ) VALUES (%s, %s, %s, 'Sample Rider', 'sample-rider', 'fixed',
                      100000.00, 'KRW', false, 'unknown')
            RETURNING id
            """,
            (
                seed.scope_a.household_space_id,
                UUID(policy_id),
                evidence_id or seed.source_evidence_id,
            ),
        ).fetchone()
    assert row
    return cast(UUID, row["id"])


def test_postgresql_ledger_enforces_scope_evidence_versions_and_trash(
    database_url: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    seed = _seed(database_url)
    with _client(seed.scope_a) as client_a:
        member_response = client_a.post(
            "/api/v1/family-members",
            json={"display_name": "Family Member A", "internal_alias": "member-a"},
        )
        assert member_response.status_code == 201
        member = member_response.json()

        policy_response = client_a.post(
            "/api/v1/policies",
            json=_policy_request(seed, member["id"]),
        )
        assert policy_response.status_code == 201
        policy = policy_response.json()
        assert policy["source_evidence"]["physical_page"] == 1
        assert policy["parties"][0]["evidence"]["physical_page"] == 2

        stale = client_a.patch(
            f"/api/v1/policies/{policy['id']}",
            json={"expected_version": 2, "coverage_end_date": "2030-12-31"},
        )
        assert stale.status_code == 409
        assert stale.json()["error_code"] == "VERSION_CONFLICT"
        assert client_a.get(f"/api/v1/policies/{policy['id']}").json()["version"] == 1

        rider_id = _insert_synthetic_rider(database_url, seed, policy["id"])
        riders = client_a.get(f"/api/v1/policies/{policy['id']}/riders")
        assert riders.status_code == 200
        assert riders.json()[0]["id"] == str(rider_id)
        assert riders.json()[0]["benefit_type"] == "fixed"
        assert riders.json()[0]["insured_amount"] == "100000.00"
        assert riders.json()[0]["source_evidence"]["physical_page"] == 1

        deleted = client_a.request(
            "DELETE",
            f"/api/v1/policies/{policy['id']}",
            json={"expected_version": 1},
        )
        assert deleted.status_code == 204
        assert client_a.get(f"/api/v1/policies/{policy['id']}").status_code == 404
        assert client_a.get("/api/v1/policies/trash").json()[0]["id"] == policy["id"]
        restored = client_a.post(
            f"/api/v1/policies/{policy['id']}/restore",
            json={"expected_version": 2},
        )
        assert restored.status_code == 200

    with _client(seed.scope_b) as client_b:
        assert client_b.get(f"/api/v1/family-members/{member['id']}").status_code == 404
        assert client_b.get(f"/api/v1/policies/{policy['id']}").status_code == 404
        assert client_b.get(f"/api/v1/policies/{policy['id']}/riders").status_code == 404

    serialized = (str(policy) + caplog.text).lower()
    assert all(marker.lower() not in serialized for marker in _SYNTHETIC_PRIVATE_MARKERS)


def test_terms_only_evidence_cannot_create_an_actual_policy(database_url: str) -> None:
    seed = _seed(database_url)
    with _client(seed.scope_a) as client:
        member = client.post(
            "/api/v1/family-members",
            json={"display_name": "Family Member A", "internal_alias": "member-a"},
        ).json()
        request = _policy_request(seed, member["id"])
        request["source_document_version_id"] = str(seed.terms_document_version_id)
        request["source_evidence_id"] = str(seed.terms_evidence_id)
        request["parties"][0]["evidence_id"] = str(seed.terms_evidence_id)

        response = client.post("/api/v1/policies", json=request)

    assert response.status_code == 422
    assert response.json() == {
        "error_code": "EVIDENCE_INVALID",
        "message": "evidence is invalid",
    }


@pytest.mark.parametrize("corruption", ["policy-source", "party-source", "rider-source"])
def test_corrupt_cross_document_lineage_is_never_projected(
    database_url: str,
    corruption: str,
) -> None:
    seed = _seed(database_url)
    with _client(seed.scope_a) as client:
        member = client.post(
            "/api/v1/family-members",
            json={"display_name": "Family Member A", "internal_alias": "member-a"},
        ).json()
        policy = client.post(
            "/api/v1/policies",
            json=_policy_request(seed, member["id"]),
        ).json()

        with psycopg.connect(_psycopg_url(database_url)) as connection:
            if corruption == "policy-source":
                connection.execute(
                    """
                    UPDATE policy_contracts
                    SET source_document_version_id = %s
                    WHERE id = %s
                    """,
                    (seed.terms_document_version_id, UUID(policy["id"])),
                )
            elif corruption == "party-source":
                connection.execute(
                    """
                    UPDATE policy_parties
                    SET evidence_id = %s
                    WHERE policy_contract_id = %s
                    """,
                    (seed.terms_evidence_id, UUID(policy["id"])),
                )
            else:
                _insert_synthetic_rider(
                    database_url,
                    seed,
                    policy["id"],
                    evidence_id=seed.terms_evidence_id,
                )

        endpoint = (
            f"/api/v1/policies/{policy['id']}/riders"
            if corruption == "rider-source"
            else f"/api/v1/policies/{policy['id']}"
        )
        response = client.get(endpoint)

    assert response.status_code == 503
    assert response.json() == {
        "error_code": "RESOURCE_LIMIT_EXCEEDED",
        "message": "policy service unavailable",
    }
