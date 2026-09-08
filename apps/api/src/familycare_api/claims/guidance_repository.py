"""Select a persisted local candidate inside the claim creation transaction."""

from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from familycare_api.claims.errors import ClaimInvalid
from familycare_api.claims.snapshot import build_guidance_claim_snapshot
from familycare_api.common.coverage_identity import CanonicalCoverageRef
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.repository import DecisionRepository, _medical_event
from familycare_api.guidance.models import LocalGuidanceResponse
from familycare_api.insurance_reconciliation import claim_aliases
from familycare_api.insurance_reconciliation.canonical_repository import CanonicalLinkRepository


def _existing_source_claim(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    event_id: UUID,
    family_member_id: UUID,
    rider_ids: list[UUID],
    private_ids: list[UUID],
) -> UUID | None:
    row = connection.execute(
        """
        SELECT id FROM claim_cases WHERE household_space_id=%s AND medical_event_id=%s
          AND family_member_id=%s AND deleted_at IS NULL
          AND (rider_id=ANY(%s) OR private_coverage_id=ANY(%s))
        ORDER BY created_at,id LIMIT 1
        """,
        (scope.household_space_id, event_id, family_member_id, rider_ids, private_ids),
    ).fetchone()
    return UUID(str(row["id"])) if row is not None else None


def existing_operational_claim(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    event_id: UUID,
    family_member_id: UUID,
    rider_id: UUID,
) -> UUID | None:
    """Reuse a private source only after replaying its current identity proof."""
    links = CanonicalLinkRepository.read_in_transaction(connection, scope)
    private_ids = [
        link.knowledge_coverage_id
        for link in claim_aliases.read_claim_coverage_aliases(
            connection,
            scope,
            family_member_id=family_member_id,
            rider_ids=(rider_id,),
            current_links=links,
        )
        if link.family_member_id == family_member_id and link.rider_id == rider_id
    ]
    return _existing_source_claim(
        connection, scope, event_id, family_member_id, [rider_id], private_ids
    )


def existing_private_claim(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    event_id: UUID,
    family_member_id: UUID,
    private_coverage_id: UUID,
) -> UUID | None:
    links = tuple(
        link
        for link in CanonicalLinkRepository.read_in_transaction(connection, scope)
        if link.family_member_id == family_member_id
    )
    proposed_riders = {
        link.rider_id for link in links if link.knowledge_coverage_id == private_coverage_id
    }
    # Retained mappings are lookup hints only. The reader below must replay the
    # superseded source before one can identify an operational replacement.
    proposed_riders.update(
        row["rider_id"]
        for row in connection.execute(
            "SELECT DISTINCT rider_id FROM private_knowledge_canonical_links "
            "WHERE household_space_id=%s AND family_member_id=%s AND knowledge_coverage_id=%s",
            (scope.household_space_id, family_member_id, private_coverage_id),
        ).fetchall()
    )
    aliases = claim_aliases.read_claim_coverage_aliases(
        connection,
        scope,
        family_member_id=family_member_id,
        rider_ids=tuple(proposed_riders),
        current_links=links,
    )
    rider_ids = {
        link.rider_id for link in aliases if link.knowledge_coverage_id == private_coverage_id
    }
    private_ids = {
        private_coverage_id,
        *(link.knowledge_coverage_id for link in aliases if link.rider_id in rider_ids),
    }
    return _existing_source_claim(
        connection, scope, event_id, family_member_id, list(rider_ids), list(private_ids)
    )


def create_guidance_claim(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    event_id: UUID,
    *,
    repository: DecisionRepository,
    run_id: UUID,
    expected_event_version: int,
    coverage: CanonicalCoverageRef,
) -> UUID:
    event_row = repository._event_row(connection, scope, event_id, for_update=True)
    if event_row is None or event_row["version"] != expected_event_version:
        raise ClaimInvalid
    event = _medical_event(event_row)
    run = connection.execute(
        """
        SELECT local_guidance_json FROM decision_runs
        WHERE id=%s AND household_space_id=%s AND medical_event_id=%s
          AND event_version=%s
        """,
        (run_id, scope.household_space_id, event_id, expected_event_version),
    ).fetchone()
    if run is None or run["local_guidance_json"] is None:
        raise ClaimInvalid
    guidance = LocalGuidanceResponse.model_validate(run["local_guidance_json"])
    if (
        guidance.medical_event_id != event.id
        or guidance.family_member_id != event.family_member_id
        or repository._local_guidance_is_stale(connection, scope, event, guidance) is not False
    ):
        raise ClaimInvalid
    candidates = tuple(item for item in guidance.candidates if item.ref == coverage)
    if len(candidates) != 1:
        raise ClaimInvalid
    policy_id = rider_id = private_contract_id = private_coverage_id = None
    insurer_key = insurer_display = None
    if coverage.kind == "OPERATIONAL_RIDER":
        identity = connection.execute(
            """
            SELECT p.insurer_key FROM riders r JOIN policy_contracts p
              ON p.id=r.policy_contract_id AND p.household_space_id=r.household_space_id
            WHERE r.id=%s AND p.id=%s AND r.household_space_id=%s
              AND r.deleted_at IS NULL AND p.deleted_at IS NULL
            FOR SHARE OF r,p
            """,
            (coverage.coverage_id, coverage.contract_id, scope.household_space_id),
        ).fetchone()
        if identity is None:
            raise ClaimInvalid
        policy_id, rider_id = coverage.contract_id, coverage.coverage_id
        insurer_key = identity["insurer_key"]
    else:
        identity = connection.execute(
            """
            SELECT c.insurer_display FROM private_knowledge_coverages v
            JOIN private_knowledge_contracts c ON c.id=v.knowledge_contract_id
              AND c.import_run_id=v.import_run_id AND c.household_space_id=v.household_space_id
            JOIN private_knowledge_subjects s ON s.id=c.subject_id
              AND s.import_run_id=c.import_run_id
            WHERE v.id=%s AND c.id=%s AND c.household_space_id=%s
              AND s.family_member_id=%s AND s.binding_decision='MATCH'
            FOR SHARE OF v,c,s
            """,
            (
                coverage.coverage_id,
                coverage.contract_id,
                scope.household_space_id,
                event.family_member_id,
            ),
        ).fetchone()
        if identity is None:
            raise ClaimInvalid
        private_contract_id, private_coverage_id = coverage.contract_id, coverage.coverage_id
        insurer_display = identity["insurer_display"]
    lookup = (
        existing_operational_claim
        if coverage.kind == "OPERATIONAL_RIDER"
        else existing_private_claim
    )
    existing = lookup(connection, scope, event_id, event.family_member_id, coverage.coverage_id)
    if existing is not None:
        return existing
    snapshot = build_guidance_claim_snapshot(guidance, coverage, run_id=run_id)
    claim_id = uuid4()
    connection.execute(
        """
        INSERT INTO claim_cases(id,household_space_id,medical_event_id,family_member_id,
          policy_contract_id,rider_id,private_contract_id,private_coverage_id,
          insurer_key,insurer_display,status)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'preparing')
        """,
        (
            claim_id,
            scope.household_space_id,
            event.id,
            event.family_member_id,
            policy_id,
            rider_id,
            private_contract_id,
            private_coverage_id,
            insurer_key,
            insurer_display,
        ),
    )
    values = snapshot.persistence_values()
    connection.execute(
        """
        INSERT INTO claim_case_snapshots(id,claim_case_id,snapshot_version,
          candidate_snapshot_json,rule_snapshot_json,policy_snapshot_json,evidence_snapshot_json,
          calculation_snapshot_json,snapshot_sha256)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            uuid4(),
            claim_id,
            values["snapshot_version"],
            Jsonb(values["candidate_snapshot"]),
            Jsonb(values["rule_snapshot"]),
            Jsonb(values["policy_snapshot"]),
            Jsonb(values["evidence_snapshot"]),
            Jsonb(values["calculation_snapshot"]),
            values["snapshot_sha256"],
        ),
    )
    connection.execute(
        """
        INSERT INTO claim_status_events(claim_case_id,from_status,to_status,occurred_at,
          reason_code,metadata_json)
        VALUES (%s,NULL,'preparing',clock_timestamp(),'CLAIM_CREATED','{}'::jsonb)
        """,
        (claim_id,),
    )
    return claim_id
