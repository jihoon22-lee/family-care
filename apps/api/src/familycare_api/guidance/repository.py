"""Read the actual operational ledger into common guidance inside the analysis snapshot."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import date
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import psycopg

from familycare_api.clauses.dsl import validate_rule_document
from familycare_api.common.coverage_identity import CanonicalCoverageRef
from familycare_api.common.evidence import EvidenceRef
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import MedicalEvent
from familycare_api.decisions.knowledge_domain import KnowledgeFact, KnowledgeStatusInterval
from familycare_api.guidance.amount_projection import operational_contract_amount
from familycare_api.guidance.amount_source import read_operational_amount_source
from familycare_api.guidance.domain import (
    GuidanceCalculationInput,
    GuidanceCitation,
    GuidanceContext,
    GuidanceCoverageInput,
    GuidanceRuleInput,
)
from familycare_api.guidance.models import GuidanceEvidence, GuidanceVersions
from familycare_api.guidance.semantic_binding import BoundSemanticRoot
from familycare_api.guidance.semantic_repository import SemanticGuidanceReader
from familycare_api.insurance_reconciliation import claim_aliases
from familycare_api.insurance_reconciliation.canonical_repository import (
    CanonicalLinkError,
    CanonicalLinkRepository,
)

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
        if not rows
        else (
            KnowledgeStatusInterval(
                at,
                at,
                "UNKNOWN" if status == "unknown" else "MATCH",
                status,
                "REVIEWED_STATUS_DOCUMENT",
            ),
        )
    )
    return intervals, rows


def read_subject_guidance(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    event: MedicalEvent,
) -> GuidanceContext:
    members = connection.execute(
        "SELECT id,display_name,internal_alias,version FROM family_members "
        "WHERE household_space_id=%s AND deleted_at IS NULL ORDER BY id",
        (scope.household_space_id,),
    ).fetchall()
    selected_terms = tuple(
        value
        for member in members
        if member["id"] == event.family_member_id
        for key in ("display_name", "internal_alias")
        if (value := member[key])
    )
    other_terms = tuple(
        value
        for member in members
        if member["id"] != event.family_member_id
        for key in ("display_name", "internal_alias")
        if (value := member[key])
    )
    from familycare_api.guidance.expenses import read_expenses
    from familycare_api.guidance.interpretation import INTERPRETATION_REVISION

    expenses = read_expenses(connection, scope, event.id, event.version)
    # Names stay in transient parser inputs; snapshots retain only their aggregate digest.
    digest = hashlib.sha256(
        json.dumps(
            [INTERPRETATION_REVISION, members, expenses.digest_sha256],
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return GuidanceContext(
        household_space_id=scope.household_space_id,
        family_member_id=event.family_member_id,
        coverages=(),
        selected_subject_terms=selected_terms,
        other_subject_terms=other_terms,
        expenses=expenses,
        versions=GuidanceVersions(engine="local-guidance-v2", status_digest=digest),
    )


def read_operational_guidance(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    event: MedicalEvent,
    repository: DecisionRepository,
    *,
    semantic_overlay: Mapping[CanonicalCoverageRef, tuple[BoundSemanticRoot, ...]] | None = None,
) -> GuidanceContext:
    snapshots = repository._policy_snapshots(
        connection, scope, event.family_member_id, event.event_date
    )
    overlay: Mapping[CanonicalCoverageRef, tuple[BoundSemanticRoot, ...]] = {}
    if semantic_overlay:
        from familycare_api.guidance_review.reassessment import validate_review_overlay

        allowed = {
            CanonicalCoverageRef(
                kind="OPERATIONAL_RIDER", contract_id=item.policy_id, coverage_id=item.rider_id
            )
            for item in snapshots
        }
        if not set(semantic_overlay) <= allowed:
            raise ValueError("GUIDANCE_REVIEW_COVERAGE_SCOPE_INVALID")
        overlay = validate_review_overlay(
            connection, scope, event, semantic_overlay, decisions=repository
        )
    review_failures: list[str] = []
    coverages = []
    subjects = read_subject_guidance(connection, scope, event)
    versions: list[object] = [{"subject_scope": subjects.versions.status_digest}]
    semantics = SemanticGuidanceReader(connection, scope, event, repository.database_url)
    links = tuple(
        link
        for link in CanonicalLinkRepository.read_in_transaction(connection, scope)
        if link.family_member_id == event.family_member_id
    )
    history_failures: tuple[str, ...] = ()
    try:
        with connection.transaction():
            history_aliases = claim_aliases.read_claim_coverage_aliases(
                connection,
                scope,
                family_member_id=event.family_member_id,
                rider_ids=tuple(snapshot.rider_id for snapshot in snapshots),
                current_links=links,
            )
    except psycopg.Error, CanonicalLinkError:
        history_aliases = ()
        history_failures = ("CLAIM_HISTORY_ALIAS_UNAVAILABLE",)
    versions.append({"claim_history_alias_failures": history_failures})
    for snapshot in snapshots:
        history = connection.execute(
            """
            SELECT count(*)::integer AS count FROM claim_history
            WHERE household_space_id=%s AND family_member_id=%s
              AND medical_event_id<>%s AND payment_date<=%s AND counted_occurrence
              AND (rider_id=%s OR private_coverage_id=ANY(%s))
            """,
            (
                scope.household_space_id,
                event.family_member_id,
                event.id,
                event.event_date,
                snapshot.rider_id,
                [
                    link.knowledge_coverage_id
                    for link in history_aliases
                    if link.rider_id == snapshot.rider_id
                ],
            ),
        ).fetchone()
        history_count = int(history["count"]) if history and not history_failures else 0
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
        amount_source = read_operational_amount_source(connection, scope, snapshot.rider_id)
        semantic = semantics.for_rider(snapshot.policy_id, snapshot.rider_id)
        ref = CanonicalCoverageRef(
            kind="OPERATIONAL_RIDER", contract_id=snapshot.policy_id, coverage_id=snapshot.rider_id
        )
        if ref in overlay:
            if not enrolled or not subject:
                raise ValueError("GUIDANCE_REVIEW_ENROLLMENT_CHANGED")
            merged, changes = merge_review_roots(semantic.roots, overlay[ref])
            review_failures.extend(
                code
                for code in changes
                if code in {"REVIEW_SEMANTIC_PARTIAL", "REVIEW_SEMANTIC_DISAGREEMENT"}
            )
            semantic = replace(
                semantic,
                roots=merged,
                versions=(
                    *semantic.versions,
                    {
                        "review_roots": [
                            {
                                "anchor": root.original_anchor,
                                "manifest": root.manifest_sha256,
                                "publications": sorted(
                                    {str(rule.publication_id) for rule in root.rules}
                                    | (
                                        {str(root.calculation.publication_id)}
                                        if root.calculation
                                        else set()
                                    )
                                ),
                            }
                            for root in overlay[ref]
                        ],
                        "changes": changes,
                    },
                ),
            )
        semantic_kinds = {root.benefit_kind for root in semantic.roots} - {"UNKNOWN"}
        benefit = (
            "FIXED"
            if snapshot.rider_type == "fixed"
            else "INDEMNITY"
            if snapshot.rider_type == "indemnity"
            else next(iter(semantic_kinds))
            if len(semantic_kinds) == 1
            else "UNKNOWN"
        )
        from familycare_api.guidance.payout_cases import semantic_payout_cases

        cases = semantic_payout_cases(
            semantic.roots,
            operational_rules=tuple(rules),
            operational_calculations=tuple(calculations),
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
                benefit_type=benefit,
                insured_amount=amount_source.amount,
                currency=amount_source.currency,
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
                claim_history_counted_occurrence=(
                    KnowledgeFact(value=history_count, provenance="DERIVED_CONFIRMED")
                    if history_count > 0
                    else None
                ),
                # Each replayed original root owns its referenced conditions.
                # Legacy aggregate rules remain the fallback when none is available.
                rules=() if cases else tuple(rules),
                calculation=None if cases else calculations[0] if len(calculations) == 1 else None,
                cases=cases,
                contract_amount=operational_contract_amount(amount_source),
                certificate_amount_decision=amount_source.amount_decision,
                certificate_amount_evidence_state=(
                    "DIRECT" if amount_source.amount_decision == "MATCH" else "UNAVAILABLE"
                ),
            )
        )
        versions.append(
            {
                "ref": str(snapshot.rider_id),
                "ledger": row,
                "snapshot": asdict(snapshot),
                "amount_source": amount_source.digest_sha256,
                "status": status_rows,
                "claim_history_counted_occurrence": history_count or None,
                "rules": [
                    {
                        "id": str(rule.id),
                        "document": rule.rule_document,
                        "evidence": [asdict(e) for e in rule.evidence],
                        "event_status": selected.status_for(rule.id),
                    }
                    for rule in selected
                ],
                "evidence": sorted(str(item.evidence_id) for item in retained),
                "semantic": semantic.versions,
            }
        )
    digest = hashlib.sha256(
        json.dumps(
            versions,
            sort_keys=True,
            default=lambda item: dict(item) if isinstance(item, Mapping) else str(item),
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return GuidanceContext(
        household_space_id=scope.household_space_id,
        family_member_id=event.family_member_id,
        coverages=tuple(coverages),
        selected_subject_terms=subjects.selected_subject_terms,
        other_subject_terms=subjects.other_subject_terms,
        expenses=subjects.expenses,
        failure_codes=tuple(dict.fromkeys((*history_failures, *review_failures))),
        versions=GuidanceVersions(engine="local-guidance-v2", status_digest=digest),
    )


def merge_review_roots(
    original: tuple[BoundSemanticRoot, ...], reviewed: tuple[BoundSemanticRoot, ...]
) -> tuple[tuple[BoundSemanticRoot, ...], tuple[str, ...]]:
    """Merge only independently verified roots; preserve unrelated source meanings.

    Authority belongs to ``validate_review_overlay``. This pure step only selects
    complete roots by their original address and records the resulting differences.
    """
    if len(original) > 32 or len(reviewed) > 32:
        raise ValueError("GUIDANCE_REVIEW_ROOT_LIMIT")

    def meaning(root: BoundSemanticRoot) -> str:
        def document(value: Mapping[str, object]) -> dict[str, object]:
            return {
                key: item
                for key, item in value.items()
                if key not in {"evidence_ids", "result_reason_code"}
            }

        return json.dumps(
            {
                "kind": root.benefit_kind,
                "complete": root.complete,
                "rules": sorted(
                    json.dumps(
                        [
                            document(rule.rule_document),
                            sorted(
                                json.dumps(
                                    {
                                        key: value
                                        for key, value in scope.items()
                                        if key != "node_id"
                                    },
                                    sort_keys=True,
                                )
                                for scope in rule.classification_scopes
                            ),
                        ],
                        sort_keys=True,
                        default=str,
                    )
                    for rule in root.rules
                ),
                "calculation": document(root.calculation.calculation_document)
                if root.calculation
                else None,
                "currency": root.calculation.source_currency if root.calculation else None,
            },
            sort_keys=True,
            default=str,
        )

    merged = list(original)
    positions = {root.original_anchor: position for position, root in enumerate(original)}
    if len(positions) != len(original):
        raise ValueError("GUIDANCE_REVIEW_ROOT_IDENTITY_INVALID")
    reviewed_meanings: dict[tuple[object, ...], set[str]] = {}
    for root in reviewed:
        if root.complete:
            reviewed_meanings.setdefault(root.original_anchor, set()).add(meaning(root))
    conflicts = {anchor for anchor, values in reviewed_meanings.items() if len(values) > 1}
    codes = ["REVIEW_SEMANTIC_DISAGREEMENT"] if conflicts else []
    for root in reviewed:
        if not root.complete:
            codes.append("REVIEW_SEMANTIC_PARTIAL")
            continue
        if root.original_anchor in conflicts:
            continue
        position = positions.get(root.original_anchor)
        if position is None:
            positions[root.original_anchor] = len(merged)
            merged.append(root)
            codes.append("REVIEW_SEMANTIC_ADDITION")
        elif meaning(merged[position]) != meaning(root):
            if merged[position].complete:
                codes.append("REVIEW_SEMANTIC_DISAGREEMENT")
            merged[position] = root
            codes.append("REVIEW_SEMANTIC_CORRECTION")
    if len(merged) > 32:
        raise ValueError("GUIDANCE_REVIEW_ROOT_LIMIT")
    return tuple(merged), tuple(dict.fromkeys(codes))


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
    # Canonical identity joins sources, not their amount, status or rule authority.
    # Keep the full source inputs; the engine groups only their evaluated cases.
    identities = {
        coverage.canonical_identity.ref: coverage.canonical_identity
        for coverage in private.coverages
        if coverage.canonical_identity is not None
    }
    coverages = list(private.coverages)
    for coverage in operational.coverages:
        identity = identities.get(coverage.ref)
        coverages.append(
            replace(coverage, canonical_identity=identity) if identity is not None else coverage
        )
    digest = hashlib.sha256(
        json.dumps(
            {
                "source_selection": "canonical-source-variants-v1",
                "private": private.versions.model_dump(mode="json"),
                "operational": operational.versions.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return replace(
        private,
        coverages=tuple(coverages),
        selected_subject_terms=operational.selected_subject_terms,
        other_subject_terms=operational.other_subject_terms,
        expenses=operational.expenses,
        failure_codes=tuple(dict.fromkeys((*operational.failure_codes, *private.failure_codes))),
        versions=private.versions.model_copy(update={"status_digest": digest}),
    )
