"""Retain relevance only for an already approved rule with shared original identity.

This is an advisory read. It neither selects a terms edition nor publishes a link,
rule, or amount. Current user decisions and uncertain change scopes stay barriers.
"""

from collections import defaultdict
from typing import Any
from uuid import UUID

import psycopg

from familycare_api.clauses.terms_applicability import assess_terms_applicability
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import MedicalEvent
from familycare_api.decisions.terms import RulesForEvent

_INSUFFICIENT = frozenset({"INSUFFICIENT_APPLICABILITY_EVIDENCE", "APPLICATION_REFERENCE_UNPROVEN"})
_IDENTITY = frozenset(
    (side, field) for side in ("policy", "terms") for field in ("insurer", "product_code")
)


def _shared_identity(row: dict[str, Any]) -> bool:
    # Recompare the actual original values; four field names alone prove nothing.
    observed = assess_terms_applicability(row["policy_proof"], row["terms_proof"])
    return (
        row["status"] == "UNKNOWN"
        and row["selection_state"] == "UNRESOLVED"
        and row["gate"] == "LEGACY"
        and "INSUFFICIENT_APPLICABILITY_EVIDENCE" in row["reason_codes"]
        and set(row["reason_codes"]) <= _INSUFFICIENT
        and observed.status == "UNKNOWN"
        and set(observed.reason_codes) <= _INSUFFICIENT
        and set(observed.evidence_fields) >= _IDENTITY
    )


def read_uncertain_rule_relevance(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    event: MedicalEvent,
    policy_id: UUID,
    rider_id: UUID,
    selected: RulesForEvent,
) -> dict[UUID, UUID]:
    """Map the narrowly supported rule IDs to their current assessment identity."""
    unknown = [rule.id for rule in selected if selected.status_for(rule.id) == "UNKNOWN"]
    if not unknown or len(unknown) > 128 or not selected.terms_selections:
        return {}
    rows = connection.execute(
        "SELECT v.id AS rule_version_id,l.clause_id,l.terms_edition_id,"
        "a.id AS assessment_id,a.status,a.selection_state,a.reason_codes,"
        "pc.proof_json AS policy_proof,tc.proof_json AS terms_proof,"
        "policy_terms_applicability_gate(a.policy_contract_id,a.terms_edition_id,"
        "a.household_space_id) AS gate "
        "FROM coverage_rule_versions v JOIN coverage_rules r ON r.id=v.coverage_rule_id "
        "AND r.version=v.version_number AND r.current_status='published' AND r.deleted_at IS NULL "
        "JOIN rider_clause_links l ON l.id=r.rider_clause_link_id "
        "AND l.household_space_id=r.household_space_id AND l.rider_id=%s "
        "AND l.deleted_at IS NULL AND l.review_state IN ('AI_VERIFIED','USER_CONFIRMED') "
        "JOIN clauses c ON c.id=l.clause_id AND c.terms_edition_id=l.terms_edition_id "
        "AND c.household_space_id=l.household_space_id AND c.deleted_at IS NULL "
        "JOIN current_policy_terms_applicability a ON a.terms_edition_id=l.terms_edition_id "
        "AND a.household_space_id=l.household_space_id AND a.policy_contract_id=%s "
        "AND a.family_member_id=%s "
        "JOIN terms_applicability_component_sources pc ON pc.id=a.policy_component_id "
        "AND pc.metadata_publication_id=a.policy_publication_id "
        "JOIN terms_editions e ON e.id=l.terms_edition_id AND e.deleted_at IS NULL "
        "JOIN terms_applicability_component_sources tc ON tc.id=e.source_component_id "
        "AND tc.metadata_publication_id=a.terms_publication_id "
        "WHERE r.household_space_id=%s AND v.id=ANY(%s::uuid[]) "
        "AND v.executable AND v.review_state IN ('AI_VERIFIED','USER_CONFIRMED') "
        "AND v.published_at IS NOT NULL "
        "AND terms_edition_allows_pages(e.id,r.household_space_id,"
        "c.physical_page_start,c.physical_page_end) ORDER BY v.id,a.id LIMIT 129",
        (rider_id, policy_id, event.family_member_id, scope.household_space_id, unknown),
    ).fetchall()
    if len(rows) > 128:
        return {}
    grouped: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["rule_version_id"]].append(row)
    supported = {}
    for identifier, observations in grouped.items():
        if len(observations) != 1 or identifier not in unknown:
            continue
        row = observations[0]
        selections = [
            selection
            for selection in selected.terms_selections
            if selection.scope.clause_id == row["clause_id"]
        ]
        if len(selections) != 1:
            continue
        selection = selections[0]
        if (
            selection.scope.household_space_id != scope.household_space_id
            or selection.scope.family_member_id != event.family_member_id
            or selection.scope.policy_contract_id != policy_id
            or selection.scope.rider_id != rider_id
            or selection.event_date != event.event_date
            or selection.applied_relation_ids
            or selection.uncertain_relation_ids
            or selection.scope_uncertainties
            or selection.scope_relation_ids
            or row["assessment_id"] not in selection.base_assessment_ids
        ):
            continue
        editions = [e for e in selection.editions if e.edition_id == row["terms_edition_id"]]
        if (
            len(editions) != 1
            or editions[0].status != "UNKNOWN"
            or editions[0].relation_ids
            or set(editions[0].reason_codes) != {"BASE_TERMS_SOURCE_UNRESOLVED"}
            or not _shared_identity(row)
        ):
            continue
        supported[identifier] = row["assessment_id"]
    return supported
