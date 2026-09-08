"""Read the actual operational ledger into common guidance inside the analysis snapshot."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import psycopg

from familycare_api.clauses.dsl import validate_rule_document
from familycare_api.common.coverage_identity import CanonicalCoverageRef
from familycare_api.common.evidence import EvidenceRef
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import MedicalEvent
from familycare_api.decisions.knowledge_domain import KnowledgeStatusInterval
from familycare_api.guidance.domain import (
    GuidanceCalculationInput,
    GuidanceCitation,
    GuidanceContext,
    GuidanceCoverageInput,
    GuidanceRuleInput,
)
from familycare_api.guidance.models import GuidanceEvidence, GuidanceVersions

if TYPE_CHECKING:
    from familycare_api.decisions.repository import DecisionRepository


def _citation(item: EvidenceRef, publication: UUID, valid: bool) -> GuidanceCitation:
    return GuidanceCitation(
        str(item.evidence_id),
        GuidanceEvidence(
            kind="OPERATIONAL_EVIDENCE",
            evidence_id=item.evidence_id,
            page_start=item.physical_page,
            page_end=item.physical_page,
            publication_id=publication,
            source_sha256=item.content_sha256,
        ),
        valid,
    )


def _event_statuses(
    connection: psycopg.Connection[dict[str, Any]],
    repository: DecisionRepository,
    scope: HouseholdScope,
    policy: UUID,
    rider: UUID,
    at: date | None,
) -> tuple[tuple[KnowledgeStatusInterval, ...], list[dict[str, Any]]]:
    if at is None:
        return (), []
    rows = connection.execute(
        "SELECT s.id,s.status,s.effective_at,s.evidence_id,s.policy_contract_id,s.rider_id "
        "FROM policy_status_snapshots s WHERE s.household_space_id=%s AND s.deleted_at IS NULL "
        "AND (s.policy_contract_id=%s OR s.rider_id=%s) AND s.effective_at<(%s::date+1) "
        "AND s.effective_at=(SELECT max(n.effective_at) FROM policy_status_snapshots n "
        "WHERE n.household_space_id=s.household_space_id AND n.deleted_at IS NULL "
        "AND n.effective_at<(%s::date+1) "
        "AND n.policy_contract_id IS NOT DISTINCT FROM s.policy_contract_id "
        "AND n.rider_id IS NOT DISTINCT FROM s.rider_id) ORDER BY s.id",
        (scope.household_space_id, policy, rider, at, at),
    ).fetchall()
    valid = {
        e.evidence_id
        for e in repository._evidence_many(
            connection, scope, tuple(row["evidence_id"] for row in rows)
        )
    }
    decisions = []
    for field, key in (("policy_contract_id", policy), ("rider_id", rider)):
        selected = [row for row in rows if row[field] == key]
        values = {row["status"] for row in selected}
        decisions.append(
            next(iter(values))
            if len(values) == 1 and all(row["evidence_id"] in valid for row in selected)
            else "unknown"
        )
    status: Literal["active", "inactive", "unknown"] = (
        "inactive"
        if any(value in {"inactive", "expired", "cancelled"} for value in decisions)
        else "active"
        if decisions == ["active", "active"]
        else "unknown"
    )
    # This is the result of an event-date ledger selection, not a new stored status interval.
    intervals = (
        ()
        if status == "unknown"
        else (KnowledgeStatusInterval(at, at, "MATCH", status, "REVIEWED_STATUS_DOCUMENT"),)
    )
    return intervals, rows


def read_operational_guidance(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    event: MedicalEvent,
    repository: DecisionRepository,
) -> GuidanceContext:
    snapshots = repository._policy_snapshots(
        connection, scope, event.family_member_id, event.event_date
    )
    coverages = []
    versions: list[object] = []
    for snapshot in snapshots:
        row = connection.execute(
            "SELECT p.product_display,p.source_evidence_id AS policy_evidence,"
            "p.version AS policy_version,"
            "p.status AS policy_status,r.source_evidence_id AS rider_evidence,"
            "r.version AS rider_version,"
            "r.status AS rider_status,ARRAY(SELECT party.evidence_id FROM policy_parties party "
            "WHERE party.policy_contract_id=p.id AND party.household_space_id=p.household_space_id "
            "AND party.family_member_id=%s "
            "AND party.role IN ('primary_insured','additional_insured') "
            "AND party.deleted_at IS NULL AND (%s::date IS NULL OR party.effective_from IS NULL "
            "OR party.effective_from<=%s) AND (%s::date IS NULL OR party.effective_to IS NULL "
            "OR party.effective_to>=%s) ORDER BY party.id) AS party_evidence "
            "FROM riders r JOIN policy_contracts p "
            "ON p.id=r.policy_contract_id AND p.household_space_id=r.household_space_id "
            "WHERE r.id=%s AND p.id=%s AND r.household_space_id=%s "
            "AND r.deleted_at IS NULL AND p.deleted_at IS NULL",
            (
                event.family_member_id,
                event.event_date,
                event.event_date,
                event.event_date,
                event.event_date,
                snapshot.rider_id,
                snapshot.policy_id,
                scope.household_space_id,
            ),
        ).fetchone()
        if row is None:
            continue
        ids = tuple(
            dict.fromkeys([row["policy_evidence"], row["rider_evidence"], *row["party_evidence"]])
        )
        retained = repository._evidence_many(connection, scope, ids)
        valid = {item.evidence_id for item in retained}
        enrolled = row["policy_evidence"] in valid and row["rider_evidence"] in valid
        subject = any(key in valid for key in row["party_evidence"])
        selected = repository._rule_versions(
            connection,
            scope,
            snapshot.rider_id,
            family_member_id=event.family_member_id,
            event_date=event.event_date,
        )
        rules = []
        calculations = []
        for rule in selected:
            if selected.status_for(rule.id) == "NO_MATCH":
                continue
            evidence_valid = {
                item.evidence_id: item
                for item in repository._evidence_many(
                    connection, scope, tuple(item.evidence_id for item in rule.evidence)
                )
            }
            citations = tuple(
                _citation(
                    item,
                    rule.id,
                    evidence_valid.get(item.evidence_id) == item
                    and selected.status_for(rule.id) == "MATCH",
                )
                for item in rule.evidence
            )
            try:
                validated = validate_rule_document(
                    rule.rule_document, tuple(c.citation_key for c in citations)
                )
            except ValueError:
                validated = None
            if validated is not None and validated.calculation is not None:
                if snapshot.rider_type in {"fixed", "indemnity"}:
                    calculations.append(
                        GuidanceCalculationInput(
                            publication_id=rule.id,
                            calculation_key=str(rule.coverage_rule_id),
                            calculation_kind="FIXED"
                            if snapshot.rider_type == "fixed"
                            else "INDEMNITY",
                            result_reason_code=rule.result_reason_code,
                            calculation_document=rule.rule_document,
                            citations=citations,
                            source_kind="OPERATIONAL_RULE_VERSION",
                        )
                    )
            else:
                rules.append(
                    GuidanceRuleInput(
                        publication_id=rule.id,
                        rule_key=str(rule.coverage_rule_id),
                        rule_kind=rule.rule_kind,
                        required=rule.required,
                        result_reason_code=rule.result_reason_code,
                        rule_document=rule.rule_document,
                        citations=citations,
                        source_kind="OPERATIONAL_RULE_VERSION",
                    )
                )
        intervals, status_rows = _event_statuses(
            connection, repository, scope, snapshot.policy_id, snapshot.rider_id, event.event_date
        )
        starts = [
            d for d in (snapshot.contract_start, snapshot.rider_coverage_start) if d is not None
        ]
        ends = [d for d in (snapshot.contract_end, snapshot.rider_coverage_end) if d is not None]
        coverages.append(
            GuidanceCoverageInput(
                ref=CanonicalCoverageRef(
                    kind="OPERATIONAL_RIDER",
                    contract_id=snapshot.policy_id,
                    coverage_id=snapshot.rider_id,
                ),
                contract_label=row["product_display"],
                coverage_label=snapshot.rider_label or "담보",
                benefit_type="FIXED"
                if snapshot.rider_type == "fixed"
                else "INDEMNITY"
                if snapshot.rider_type == "indemnity"
                else "UNKNOWN",
                insured_amount=snapshot.insured_amount,
                currency=snapshot.currency,
                contract_start=max(starts) if starts else None,
                contract_end=min(ends) if ends else None,
                disposition="PUBLISHED",
                subject_binding_decision="MATCH" if subject else "UNKNOWN",
                enrollment_decision="MATCH" if enrolled else "UNKNOWN",
                component_classification="BENEFIT_COVERAGE",
                mapping_applicability="APPLICABLE",
                mapping_enrollment_decision="MATCH" if enrolled else "UNKNOWN",
                document_identity_decision="MATCH" if enrolled else "UNKNOWN",
                edition_applicability_decision="MATCH",
                section_mapping_decision="MATCH",
                overall_mapping_decision="MATCH",
                current_confirmation_decision=None,
                current_confirmed_status="inactive"
                if any(
                    row[k] in {"inactive", "expired", "cancelled"}
                    for k in ("policy_status", "rider_status")
                )
                else None,
                status_intervals=intervals,
                rules=tuple(rules),
                calculation=calculations[0] if len(calculations) == 1 else None,
                certificate_amount_decision="MATCH" if enrolled else "UNKNOWN",
                certificate_amount_evidence_state="DIRECT" if enrolled else "UNAVAILABLE",
            )
        )
        versions.append(
            {
                "ref": str(snapshot.rider_id),
                "ledger": row,
                "status": status_rows,
                "rules": [str(rule.id) for rule in selected],
                "evidence": sorted(str(item.evidence_id) for item in retained),
            }
        )
    digest = hashlib.sha256(
        json.dumps(versions, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()
    return GuidanceContext(
        household_space_id=scope.household_space_id,
        family_member_id=event.family_member_id,
        coverages=tuple(coverages),
        versions=GuidanceVersions(status_digest=digest),
    )


def combine_guidance_contexts(
    operational: GuidanceContext, private: GuidanceContext | None
) -> GuidanceContext:
    if private is None:
        return operational
    if (operational.household_space_id, operational.family_member_id) != (
        private.household_space_id,
        private.family_member_id,
    ):
        raise ValueError("GUIDANCE_SCOPE_MISMATCH")
    # Preserve the existing verified private calculation until a canonical source-specific
    # selection can replace it. One canonical coverage must never appear twice.
    preferred = {
        (c.canonical_identity.ref.kind, c.canonical_identity.ref.coverage_id)
        if c.canonical_identity
        else (c.ref.kind, c.ref.coverage_id): c
        for c in private.coverages
    }
    for coverage in operational.coverages:
        key = (coverage.ref.kind, coverage.ref.coverage_id)
        preferred.setdefault(key, coverage)
    return replace(private, coverages=tuple(preferred.values()))
