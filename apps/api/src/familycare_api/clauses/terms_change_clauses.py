"""Verify exact native Clause affiliations before resolving a source amendment.

This preflight does not confirm a link or publish a rule. The caller has already
verified the amendment's contract, insurer, Rider and edition identities; the
independent event selector owns temporal applicability.
"""

from dataclasses import replace
from typing import Any
from uuid import UUID

import psycopg

from familycare_api.clauses.errors import RiderClauseLinkInvalid
from familycare_api.clauses.links import validate_rider_clause_link
from familycare_api.clauses.repository import CoverageRuleRepository, RiderClauseLinkRepository
from familycare_api.clauses.source_repository import read_verified_clause_source
from familycare_api.clauses.terms_change_targets import (
    ResolvedTermsChangeTargets,
    VerifiedClauseCandidate,
)
from familycare_api.common.scope import HouseholdScope


def clause_change_candidates(
    connection: psycopg.Connection[dict[str, Any]],
    database_url: str,
    target: ResolvedTermsChangeTargets,
) -> tuple[VerifiedClauseCandidate, ...]:
    if (
        target.scope_kind != "CLAUSE"
        or target.policy_contract_id is None
        or target.rider_id is None
        or target.family_member_id is None
    ):
        return ()
    editions = [
        value for value in (target.previous_edition_id, target.new_edition_id) if value is not None
    ]
    scope = HouseholdScope(target.household_space_id)
    rows = connection.execute(
        "SELECT l.* FROM rider_clause_links l JOIN riders r ON r.id=l.rider_id "
        "AND r.household_space_id=l.household_space_id AND r.deleted_at IS NULL "
        "JOIN policy_contracts p ON p.id=r.policy_contract_id AND "
        "p.household_space_id=l.household_space_id "
        "AND p.deleted_at IS NULL WHERE l.household_space_id=%s AND l.rider_id=%s "
        "AND p.id=%s AND l.terms_edition_id=ANY(%s) AND l.deleted_at IS NULL "
        "AND l.review_state<>'rejected' AND EXISTS(SELECT 1 FROM policy_parties party "
        "JOIN family_members m ON m.id=party.family_member_id AND "
        "m.household_space_id=party.household_space_id "
        "AND m.deleted_at IS NULL WHERE party.policy_contract_id=p.id AND "
        "party.household_space_id=p.household_space_id "
        "AND party.family_member_id=%s AND party.deleted_at IS NULL "
        "AND party.role IN ('primary_insured','additional_insured')) ORDER BY l.id LIMIT 513",
        (
            target.household_space_id,
            target.rider_id,
            target.policy_contract_id,
            editions,
            target.family_member_id,
        ),
    ).fetchall()
    if len(rows) > 512:
        raise ValueError("CLAUSE_CHANGE_CANDIDATE_LIMIT")
    repository = RiderClauseLinkRepository(database_url)
    result = []
    for row in rows:
        source = read_verified_clause_source(
            connection, target.household_space_id, row["clause_id"]
        )
        if source is None or source.terms_edition_id != row["terms_edition_id"]:
            continue
        try:
            context = repository._validation_context(
                connection, scope, row, include_change_gate=False, lock_source=False
            )
            # The verified amendment identifies both editions independently of
            # display names and contract-day applicability. All other native
            # scope, source, approval, Clause and exact evidence checks remain.
            validate_rider_clause_link(
                scope,
                replace(
                    context,
                    program_applicability_verified=True,
                    program_applicability_blocked=False,
                ),
            )
        except RiderClauseLinkInvalid:
            continue
        evidence_ids, valid = CoverageRuleRepository._candidate_evidence_ids(
            connection, scope, row["candidate_version_id"]
        )
        declared = connection.execute(
            "SELECT DISTINCT evidence_id FROM analysis_candidate_evidence WHERE "
            "candidate_version_id=%s",
            (row["candidate_version_id"],),
        ).fetchall()
        if (
            not valid
            or evidence_ids != frozenset(e.evidence_id for e in context.link.evidence)
            or evidence_ids != frozenset(e["evidence_id"] for e in declared)
        ):
            continue
        result.append(
            VerifiedClauseCandidate(
                source.clause_id,
                source.terms_edition_id,
                target.household_space_id,
                target.family_member_id,
                target.policy_contract_id,
                target.rider_id,
                source.region.label,
                source.assessment_id,
            )
        )
    return tuple(result)


def change_allows_clause_publication(
    connection: psycopg.Connection[dict[str, Any]],
    household: UUID,
    policy: UUID,
    rider: UUID,
    clause: UUID,
    edition: UUID,
) -> bool:
    """Permit native review of one scope; event-day use still requires selection."""
    rows = connection.execute(
        "SELECT a.* FROM current_policy_terms_changes a WHERE a.household_space_id=%s "
        "AND a.policy_contract_id=%s AND a.new_edition_id=%s AND a.status='MATCH' "
        "AND a.scope_resolved AND EXISTS(SELECT 1 FROM policy_parties p JOIN family_members m "
        "ON m.id=p.family_member_id AND m.household_space_id=p.household_space_id AND "
        "m.deleted_at IS NULL "
        "WHERE p.household_space_id=a.household_space_id AND "
        "p.policy_contract_id=a.policy_contract_id "
        "AND p.family_member_id=a.family_member_id AND p.deleted_at IS NULL "
        "AND p.role IN ('primary_insured','additional_insured')) "
        "AND (a.scope_kind='CONTRACT' OR (a.scope_kind='RIDER' AND a.rider_id=%s) OR "
        "(a.scope_kind='CLAUSE' AND a.rider_id=%s AND a.new_clause_id=%s)) ORDER BY a.id LIMIT 129",
        (household, policy, edition, rider, rider, clause),
    ).fetchall()
    if len(rows) > 128:
        return False
    for row in rows:
        from familycare_api.clauses.terms_change_repository import replay_terms_change

        replay = replay_terms_change(connection, row)
        if replay is None or not replay.matches:
            continue
        # The scope-bound audit must still describe the current inputs after replay.
        if (
            connection.execute(
                "SELECT 1 FROM current_policy_terms_changes WHERE id=%s AND household_space_id=%s",
                (row["id"], household),
            ).fetchone()
            is not None
        ):
            return True
    return False


def retain_uncertain_clause_targets(
    connection: psycopg.Connection[dict[str, Any]],
    target: ResolvedTermsChangeTargets,
    component: UUID,
    publication: UUID,
    generation: UUID,
    content_hash: str,
) -> tuple[ResolvedTermsChangeTargets, dict[UUID, UUID]]:
    """Keep prior exact addresses as uncertainty when their current proof is lost."""
    if target.status != "UNKNOWN" or target.scope_kind != "CLAUSE" or target.rider_id is None:
        return target, {}
    prior = connection.execute(
        "SELECT * FROM policy_terms_changes WHERE source_component_id=%s AND "
        "source_publication_id=%s "
        "AND source_generation_id=%s AND source_content_sha256=%s AND household_space_id=%s "
        "AND family_member_id=%s AND policy_contract_id=%s AND rider_id=%s AND scope_kind='CLAUSE' "
        "AND status='MATCH' AND revision='terms-change-v2' ORDER BY created_at DESC,id DESC "
        "LIMIT 129",
        (
            component,
            publication,
            generation,
            content_hash,
            target.household_space_id,
            target.family_member_id,
            target.policy_contract_id,
            target.rider_id,
        ),
    ).fetchall()
    if len(prior) > 128:
        return target, {}
    kept = {}
    changes = {}
    for side in ("previous", "new"):
        if getattr(target, f"{side}_clause_id") is not None:
            continue
        edition = getattr(target, f"{side}_edition_id")
        matches = {
            (r[f"{side}_clause_id"], r[f"{side}_clause_source_id"])
            for r in prior
            if edition is not None
            and r[f"{side}_edition_id"] == edition
            and r[f"{side}_clause_id"] is not None
            and r[f"{side}_clause_source_id"] is not None
        }
        if len(matches) != 1:
            continue
        clause_id, source_id = next(iter(matches))
        if (
            connection.execute(
                "SELECT 1 FROM clauses WHERE id=%s AND household_space_id=%s AND "
                "terms_edition_id=%s "
                "AND deleted_at IS NULL",
                (clause_id, target.household_space_id, edition),
            ).fetchone()
            is None
        ):
            continue
        changes[f"{side}_clause_id"] = clause_id
        kept[clause_id] = source_id
    if not changes:
        return target, {}
    updated = replace(target, **changes)
    return replace(
        updated,
        clause_id=updated.previous_clause_id or updated.new_clause_id,
        scope_resolved=True,
        reason_codes=tuple(sorted({*updated.reason_codes, "CLAUSE_SOURCE_HISTORY_UNCERTAIN"})),
    ), kept
