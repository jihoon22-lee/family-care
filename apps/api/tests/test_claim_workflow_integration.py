"""Synthetic PostgreSQL proof for independent immutable ClaimCases."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

import psycopg
import pytest
from familycare_api.claims.errors import ClaimNotFound, InvalidClaimTransitionError
from familycare_api.claims.repository import ClaimRepository
from familycare_api.common.scope import HouseholdScope
from psycopg.rows import dict_row

from apps.api.tests.test_benefit_integration import (
    _create_event as create_benefit_event,
)
from apps.api.tests.test_benefit_integration import (
    _decision_service as benefit_decision_service,
)
from apps.api.tests.test_benefit_integration import (
    _publish_changed_fixed_rule as publish_changed_fixed_rule,
)
from apps.api.tests.test_benefit_integration import _seed as seed_benefit_graph

pytestmark = pytest.mark.integration


def _uuid(number: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{number:012d}")


SCOPE_A = HouseholdScope(_uuid(1))
SCOPE_B = HouseholdScope(_uuid(2))
MEMBER_ID = _uuid(3)
EVENT_ID = _uuid(4)
POLICY_A = _uuid(5)
POLICY_B = _uuid(6)
RIDER_A = _uuid(7)
RIDER_B = _uuid(8)
CLAIM_A = _uuid(9)
CLAIM_B = _uuid(10)
SNAPSHOT_A = _uuid(11)
SNAPSHOT_B = _uuid(12)
CHECKLIST_A = _uuid(13)
NOW = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)


def _psycopg_url(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture()
def database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required for PostgreSQL integration tests")
    return value


def _seed(database_url: str) -> None:
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        connection.execute(
            """
            TRUNCATE TABLE household_spaces, documents
            RESTART IDENTITY CASCADE
            """
        )
        connection.execute(
            """
            INSERT INTO household_spaces (id, space_key, display_name)
            VALUES (%s, 'synthetic-claim-a', 'Synthetic Claim Household A'),
                   (%s, 'synthetic-claim-b', 'Synthetic Claim Household B')
            """,
            (SCOPE_A.household_space_id, SCOPE_B.household_space_id),
        )
        connection.execute(
            """
            INSERT INTO family_members (id, household_space_id, display_name, internal_alias)
            VALUES (%s, %s, 'Family Member A', 'family-member-a')
            """,
            (MEMBER_ID, SCOPE_A.household_space_id),
        )
        document = connection.execute(
            """
            INSERT INTO documents (source_key, document_kind, status)
            VALUES ('synthetic/claim-policy.pdf', 'policy', 'ready')
            RETURNING id
            """
        ).fetchone()
        assert document is not None
        version = connection.execute(
            """
            INSERT INTO document_versions (
              document_id, version_number, content_sha256, byte_size, page_count
            ) VALUES (%s, 1, %s, 256, 1)
            RETURNING id
            """,
            (document["id"], "a" * 64),
        ).fetchone()
        assert version is not None
        extraction = connection.execute(
            """
            INSERT INTO extractions (
              document_version_id, extractor_name, extractor_version,
              extractor_config_hash, quality_rule_version, status, succeeded_at
            ) VALUES (%s, 'synthetic', '1', %s, 'quality-v1', 'succeeded', clock_timestamp())
            RETURNING id
            """,
            (version["id"], "b" * 64),
        ).fetchone()
        assert extraction is not None
        connection.execute(
            """
            INSERT INTO extraction_pages (
              extraction_id, page_number, width_points, height_points,
              non_whitespace_chars, alphanumeric_ratio,
              replacement_character_ratio, maximum_repeated_character_run,
              classification
            ) VALUES (%s, 1, 612, 792, 80, 0.8, 0, 1, 'TEXT_SUFFICIENT')
            """,
            (extraction["id"],),
        )
        evidence = connection.execute(
            """
            INSERT INTO evidence (
              household_space_id, document_version_id, extraction_id,
              content_sha256, physical_page, review_state
            ) VALUES (%s, %s, %s, %s, 1, 'USER_CONFIRMED')
            RETURNING id
            """,
            (
                SCOPE_A.household_space_id,
                version["id"],
                extraction["id"],
                "a" * 64,
            ),
        ).fetchone()
        assert evidence is not None
        connection.execute(
            """
            INSERT INTO policy_contracts (
              id, household_space_id, source_document_version_id, source_evidence_id,
              insurer_display, insurer_key, product_display, product_key, status
            ) VALUES
              (%s, %s, %s, %s, 'Synthetic Insurer A', 'synthetic-insurer-a',
               'Sample Policy A', 'sample-policy-a', 'active'),
              (%s, %s, %s, %s, 'Synthetic Insurer B', 'synthetic-insurer-b',
               'Sample Policy B', 'sample-policy-b', 'active')
            """,
            (
                POLICY_A,
                SCOPE_A.household_space_id,
                version["id"],
                evidence["id"],
                POLICY_B,
                SCOPE_A.household_space_id,
                version["id"],
                evidence["id"],
            ),
        )
        connection.execute(
            """
            INSERT INTO riders (
              id, household_space_id, policy_contract_id, source_evidence_id,
              display_name, normalized_key, benefit_type, currency, status
            ) VALUES
              (%s, %s, %s, %s, 'Sample Rider A', 'sample-rider-a', 'fixed', 'KRW', 'active'),
              (%s, %s, %s, %s, 'Sample Rider B', 'sample-rider-b', 'fixed', 'KRW', 'active')
            """,
            (
                RIDER_A,
                SCOPE_A.household_space_id,
                POLICY_A,
                evidence["id"],
                RIDER_B,
                SCOPE_A.household_space_id,
                POLICY_B,
                evidence["id"],
            ),
        )
        connection.execute(
            """
            INSERT INTO medical_events (
              id, household_space_id, family_member_id, mode,
              event_date, facts_json, confirmation_json, situation_text
            ) VALUES (%s, %s, %s, 'post_treatment', '2026-08-25',
                      '{}'::jsonb, '{}'::jsonb, 'synthetic situation')
            """,
            (EVENT_ID, SCOPE_A.household_space_id, MEMBER_ID),
        )
        connection.execute(
            """
            INSERT INTO claim_cases (
              id, household_space_id, medical_event_id, family_member_id,
              policy_contract_id, insurer_key, status
            ) VALUES
              (%s, %s, %s, %s, %s, 'synthetic-insurer-a', 'preparing'),
              (%s, %s, %s, %s, %s, 'synthetic-insurer-b', 'preparing')
            """,
            (
                CLAIM_A,
                SCOPE_A.household_space_id,
                EVENT_ID,
                MEMBER_ID,
                POLICY_A,
                CLAIM_B,
                SCOPE_A.household_space_id,
                EVENT_ID,
                MEMBER_ID,
                POLICY_B,
            ),
        )
        for snapshot_id, claim_id, policy_id, rider_id, digest in (
            (SNAPSHOT_A, CLAIM_A, POLICY_A, RIDER_A, "c" * 64),
            (SNAPSHOT_B, CLAIM_B, POLICY_B, RIDER_B, "d" * 64),
        ):
            connection.execute(
                """
                INSERT INTO claim_case_snapshots (
                  id, claim_case_id, snapshot_version,
                  candidate_snapshot_json, rule_snapshot_json,
                  policy_snapshot_json, evidence_snapshot_json,
                  calculation_snapshot_json, snapshot_sha256
                ) VALUES (
                  %s, %s, 1,
                  %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    snapshot_id,
                    claim_id,
                    psycopg.types.json.Jsonb(
                        {
                            "candidates": [
                                {
                                    "id": str(_uuid(100 + snapshot_id.int % 10)),
                                    "rider_id": str(rider_id),
                                    "aggregate_result": "MATCH",
                                }
                            ]
                        }
                    ),
                    psycopg.types.json.Jsonb({"versions": []}),
                    psycopg.types.json.Jsonb(
                        {
                            "snapshots": [
                                {
                                    "policy_id": str(policy_id),
                                    "rider_id": str(rider_id),
                                    "effective_status": "active",
                                }
                            ]
                        }
                    ),
                    psycopg.types.json.Jsonb({"evidence": []}),
                    psycopg.types.json.Jsonb({"calculations": []}),
                    digest,
                ),
            )
            connection.execute(
                """
                INSERT INTO claim_status_events (
                  claim_case_id, from_status, to_status, occurred_at,
                  reason_code, metadata_json
                ) VALUES (%s, NULL, 'preparing', %s, 'CLAIM_CREATED', '{}'::jsonb)
                """,
                (claim_id, NOW),
            )
        connection.execute(
            """
            INSERT INTO claim_checklist_items (
              id, claim_case_id, document_kind, requirement_code,
              required, conditional, prepared
            ) VALUES (%s, %s, 'claim_form', 'CLAIM_FORM_REQUIRED', true, false, false)
            """,
            (CHECKLIST_A, CLAIM_A),
        )


def test_independent_transitions_history_snapshots_and_restore(database_url: str) -> None:
    _seed(database_url)
    repository = ClaimRepository(database_url)
    initial_hash = cast(
        str, repository.get_claim_case(SCOPE_A, CLAIM_A)["snapshot"]["snapshot_sha256"]
    )

    updated = repository.update_claim_case(
        SCOPE_A,
        CLAIM_A,
        expected_version=1,
        changes={
            "receipt_number": "synthetic-receipt-a",
            "claimed_amount": Decimal("125000.00"),
            "currency": "KRW",
        },
    )
    assert updated["version"] == 2
    submitted_a = repository.transition_claim(
        SCOPE_A,
        CLAIM_A,
        target_status="submitted",
        expected_version=2,
        occurred_at=NOW,
        metadata={},
    )
    partial = repository.transition_claim(
        SCOPE_A,
        CLAIM_A,
        target_status="partially_paid",
        expected_version=3,
        occurred_at=NOW,
        metadata={
            "amount": Decimal("80000.00"),
            "currency": "KRW",
            "payment_date": date(2026, 8, 26),
            "reason_code": "PARTIAL_SETTLEMENT",
        },
    )
    submitted_b = repository.transition_claim(
        SCOPE_A,
        CLAIM_B,
        target_status="submitted",
        expected_version=1,
        occurred_at=NOW,
        metadata={},
    )
    denied = repository.transition_claim(
        SCOPE_A,
        CLAIM_B,
        target_status="denied",
        expected_version=2,
        occurred_at=NOW,
        metadata={"reason_code": "SYNTHETIC_DENIAL"},
    )

    assert submitted_a["status"] == submitted_b["status"] == "submitted"
    assert partial["status"] == "partially_paid"
    assert partial["paid_amount"] == Decimal("80000.00")
    assert denied["status"] == "denied"
    assert (
        repository.get_claim_case(SCOPE_A, CLAIM_A)["snapshot"]["snapshot_sha256"] == initial_hash
    )
    history = repository.for_family_member(SCOPE_A, MEMBER_ID)
    assert [(item.outcome, item.counted_occurrence) for item in history] == [
        ("partially_paid", True),
        ("denied", False),
    ]

    with pytest.raises(InvalidClaimTransitionError):
        repository.transition_claim(
            SCOPE_A,
            CLAIM_A,
            target_status="denied",
            expected_version=4,
            occurred_at=NOW,
            metadata={"reason_code": "NOT_ALLOWED"},
        )
    with pytest.raises(ClaimNotFound):
        repository.get_claim_case(SCOPE_B, CLAIM_A)

    repository.soft_delete_claim_case(SCOPE_A, CLAIM_A, expected_version=4)
    with pytest.raises(ClaimNotFound):
        repository.get_claim_case(SCOPE_A, CLAIM_A)
    deleted = repository.get_claim_case(SCOPE_A, CLAIM_A, deleted_only=True)
    assert deleted["version"] == 5 and deleted["deleted"] is True
    restored = repository.restore_claim_case(SCOPE_A, CLAIM_A, expected_version=5)
    assert restored["version"] == 6 and restored["deleted"] is False


def test_checklist_update_is_metadata_only_and_versioned(database_url: str) -> None:
    _seed(database_url)
    repository = ClaimRepository(database_url)

    updated = repository.update_checklist_item(
        SCOPE_A,
        CLAIM_A,
        CHECKLIST_A,
        expected_version=1,
        prepared=True,
        note_code="USER_PREPARED",
    )

    assert updated["checklist"][0]["prepared"] is True
    assert updated["checklist"][0]["version"] == 2
    assert set(updated["checklist"][0]) == {
        "id",
        "document_kind",
        "requirement_code",
        "required",
        "conditional",
        "prepared",
        "note_code",
        "source_rule_version_id",
        "source_evidence_id",
        "version",
    }


def test_create_claim_captures_latest_result_and_survives_later_rule_change(
    database_url: str,
) -> None:
    seed = seed_benefit_graph(database_url)
    event = create_benefit_event(database_url, seed)
    decision_service = benefit_decision_service(database_url, seed.scope_a)
    decision_service.analyze_medical_event(event.id)
    repository = ClaimRepository(database_url)

    created = repository.create_claim_case(
        seed.scope_a,
        event.id,
        insurer_key="synthetic-insurer",
        policy_contract_id=seed.policy_id,
    )
    original_hash = created["snapshot"]["snapshot_sha256"]
    original_candidates = list(created["snapshot"]["candidate"]["candidate_ids"])

    publish_changed_fixed_rule(database_url, seed)
    decision_service.analyze_medical_event(event.id)
    unchanged = repository.get_claim_case(seed.scope_a, cast(UUID, created["id"]))

    assert created["status"] == "preparing"
    assert original_candidates
    assert created["snapshot"]["calculation"]["calculation_ids"]
    assert unchanged["snapshot"]["snapshot_sha256"] == original_hash
    assert unchanged["snapshot"]["candidate"]["candidate_ids"] == original_candidates
