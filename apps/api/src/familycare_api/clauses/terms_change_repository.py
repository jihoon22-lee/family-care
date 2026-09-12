"""Project retained changes onto existing source-proven identities and event terms."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import date
from typing import Any, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from familycare_api.clauses.source_repository import read_verified_clause_source
from familycare_api.clauses.terms_applicability_repository import _key, _scalar
from familycare_api.clauses.terms_change_clauses import (
    clause_change_candidates,
    retain_uncertain_clause_targets,
)
from familycare_api.clauses.terms_change_selection import (
    BaseTermsEdition,
    TermsChangeRelation,
    TermsEventSelection,
    TermsSelectionError,
    TermsSelectionScope,
    TermsStatus,
    select_terms_for_event,
)
from familycare_api.clauses.terms_change_source import (
    ChangeMember,
    ObservedTermsChange,
    observe_terms_change,
)
from familycare_api.clauses.terms_change_targets import (
    ResolvedTermsChangeTargets,
    TermsChangeTargetRequest,
    VerifiedContractCandidate,
    VerifiedEditionCandidate,
    VerifiedRiderCandidate,
    resolve_terms_change_targets,
)
from familycare_api.common.document_locks import lock_document_content
from familycare_api.insurance_documents.repository import _database_url
from familycare_api.policies.contract_source_locator import contract_source_locator
from familycare_api.policies.enrollment_locator import physical_enrollment_locator
from familycare_api.policies.source_projection import StructureProjectionReader

REVISION = "terms-change-v2"
logger = logging.getLogger(__name__)


def _context(
    connection: psycopg.Connection[dict[str, Any]],
    component: UUID,
    household: UUID,
    *,
    scope_kind: str | None = None,
    scope_observed: bool = False,
) -> dict[str, Any]:
    placeholders = "%s,%s,%s" if scope_observed else "%s,%s"
    row = connection.execute(
        "SELECT context,context::text AS context_json,"
        "encode(sha256(convert_to(context::text,'UTF8')),'hex') AS digest,"
        "octet_length(context::text) AS bytes FROM ("
        f"SELECT terms_change_input_context({placeholders}) AS context) current_input",
        (component, household, scope_kind) if scope_observed else (component, household),
    ).fetchone()
    assert row is not None
    return row


def _source_candidates(
    connection: psycopg.Connection[dict[str, Any]],
    household: UUID,
    member: UUID,
    members: tuple[ChangeMember, ...],
) -> tuple[tuple[VerifiedContractCandidate, ...], tuple[VerifiedRiderCandidate, ...]]:
    """Reuse immutable enrollment authority, rechecking original anchors and local identity.

    A changed family alias fingerprint requires source reassociation. Existing
    enrollment remains untouched. Current Rider display text is never proof.
    """
    rows = connection.execute(
        "SELECT p.policy_contract_id,p.rider_id,p.field_values,p.authority,"
        "s.association_json,s.source_refs,plan.associations_json,plan.generation_id,"
        "ARRAY(SELECT ce.evidence_id::text FROM analysis_candidate_evidence ce "
        "WHERE ce.candidate_version_id=p.candidate_version_id AND ce.field_id='rider_name') "
        "AS name_evidence_ids,"
        "policy.insurer_display,policy.product_display,policy.source_document_version_id,"
        "g.document_version_id AS publication_document_version_id "
        "FROM range_enrollment_publications p JOIN policy_range_candidate_sources s "
        "ON s.candidate_version_id=p.source_candidate_version_id "
        "JOIN document_policy_range_plans plan ON plan.job_id=s.job_id "
        "JOIN document_structure_generations g ON g.id=plan.generation_id "
        "AND g.household_space_id=p.household_space_id "
        "JOIN document_versions v ON v.id=g.document_version_id "
        "JOIN documents d ON d.id=v.document_id "
        "AND d.deleted_at IS NULL AND d.document_kind='policy' "
        "JOIN policy_contracts policy ON policy.id=p.policy_contract_id "
        "AND policy.household_space_id=p.household_space_id AND policy.deleted_at IS NULL "
        "JOIN evidence pe ON pe.id=policy.source_evidence_id "
        "AND pe.household_space_id=p.household_space_id "
        "AND pe.document_version_id=policy.source_document_version_id "
        "AND pe.content_sha256=v.content_sha256 "
        "AND pe.review_state IN ('AI_VERIFIED','USER_CONFIRMED') "
        "LEFT JOIN riders r ON r.id=p.rider_id AND r.household_space_id=p.household_space_id "
        "AND r.policy_contract_id=p.policy_contract_id AND r.deleted_at IS NULL "
        "WHERE p.household_space_id=%s AND (p.rider_id IS NULL OR r.id IS NOT NULL) "
        "AND EXISTS(SELECT 1 FROM range_enrollment_publications origin "
        "JOIN analysis_candidate_evidence ce "
        "ON ce.candidate_version_id=origin.candidate_version_id "
        "AND ce.field_id='product_name' AND ce.evidence_id=policy.source_evidence_id "
        "WHERE origin.policy_contract_id=policy.id "
        "AND origin.household_space_id=p.household_space_id "
        "AND origin.rider_id IS NULL) "
        "AND (p.rider_id IS NULL OR EXISTS(SELECT 1 FROM analysis_candidate_evidence ce "
        "JOIN evidence re ON re.id=ce.evidence_id AND re.id=r.source_evidence_id "
        "AND re.household_space_id=p.household_space_id AND re.content_sha256=v.content_sha256 "
        "AND re.review_state IN ('AI_VERIFIED','USER_CONFIRMED') "
        "WHERE ce.candidate_version_id=p.candidate_version_id AND ce.field_id='rider_name')) "
        "AND EXISTS(SELECT 1 FROM policy_parties party WHERE party.policy_contract_id=policy.id "
        "AND party.household_space_id=p.household_space_id AND party.family_member_id=%s "
        "AND party.role IN ('primary_insured','additional_insured') AND party.deleted_at IS NULL "
        "AND EXISTS(SELECT 1 FROM range_enrollment_publications origin "
        "JOIN analysis_candidate_evidence ce "
        "ON ce.candidate_version_id=origin.candidate_version_id "
        "AND ce.field_id='product_name' AND ce.evidence_id=policy.source_evidence_id "
        "WHERE origin.policy_contract_id=policy.id "
        "AND origin.household_space_id=p.household_space_id AND origin.rider_id IS NULL "
        "AND origin.insured_evidence_id=party.evidence_id)) "
        "ORDER BY p.candidate_version_id LIMIT 513",
        (household, member),
    ).fetchall()
    if len(rows) > 512:
        raise ValueError("terms change source budget")
    fingerprint = hashlib.sha256(
        json.dumps(
            [
                (str(m.id), m.display_name, m.internal_alias, m.version)
                for m in sorted(members, key=lambda m: str(m.id))
            ],
            ensure_ascii=True,
        ).encode()
    ).hexdigest()
    reader = StructureProjectionReader(connection, household)
    contracts: set[VerifiedContractCandidate] = set()
    contract_sources: set[tuple[UUID, str, str]] = set()
    riders: set[tuple[VerifiedRiderCandidate, str, str]] = set()
    for row in rows:
        association = row["association_json"]
        if (
            association.get("state") != "RESOLVED"
            or association.get("family_member_id") != str(member)
            or row["associations_json"].get("member_fingerprint") != fingerprint
        ):
            continue
        refs = association["anchor_refs"] + row["source_refs"]
        pages = tuple(sorted({ref["page"] for ref in refs}))
        if len(pages) > 25:
            raise ValueError("terms change source budget")
        projection = reader.read(row["generation_id"], pages)
        locator = None if projection is None else contract_source_locator(projection, association)
        if locator is None:
            continue
        if row["rider_id"] is None:
            insurer = row["field_values"].get("insurer")
            product = row["field_values"].get("product_name")
            if (
                isinstance(insurer, str)
                and isinstance(row["insurer_display"], str)
                and _key(insurer) == _key(row["insurer_display"])
                and isinstance(product, str)
                and _key(product) == _key(row["product_display"])
            ):
                contracts.add(
                    VerifiedContractCandidate(
                        row["policy_contract_id"],
                        household,
                        member,
                        locator["contract_number_sha256"],
                        _key(insurer),
                    )
                )
                contract_sources.add(
                    (
                        row["policy_contract_id"],
                        locator["content_sha256"],
                        locator["contract_number_sha256"],
                    )
                )
        else:
            name = row["field_values"].get("rider_name")
            # The immutable publication already establishes enrollment. Retain
            # only a source alias whose exact name is still in its original span.
            if not isinstance(name, str) or not name.strip() or projection is None:
                continue
            name_refs = [
                ref for ref in row["source_refs"] if ref["evidence_id"] in row["name_evidence_ids"]
            ]
            grounded = physical_enrollment_locator(projection, name, name_refs) is not None
            if grounded:
                riders.add(
                    (
                        VerifiedRiderCandidate(
                            row["rider_id"],
                            household,
                            row["policy_contract_id"],
                            member,
                            (_key(name),),
                        ),
                        locator["content_sha256"],
                        locator["contract_number_sha256"],
                    )
                )
    return tuple(contracts), tuple(
        rider
        for rider, content, number in riders
        if (rider.policy_contract_id, content, number) in contract_sources
    )


def _edition_candidates(
    connection: psycopg.Connection[dict[str, Any]],
    household: UUID,
    member: UUID,
) -> tuple[VerifiedEditionCandidate, ...]:
    rows = connection.execute(
        "SELECT e.id,c.proof_json FROM terms_editions e "
        "JOIN terms_applicability_component_sources c "
        "ON c.id=e.source_component_id AND c.role='terms' "
        "WHERE e.household_space_id=%s AND c.family_member_id=%s "
        "AND terms_edition_allows_pages(e.id,e.household_space_id,"
        "e.source_page_start,e.source_page_end) "
        "ORDER BY e.id LIMIT 513",
        (household, member),
    ).fetchall()
    if len(rows) > 512:
        raise ValueError("terms change edition budget")
    result = []
    for row in rows:
        values = tuple(
            _scalar(row["proof_json"], field) for field in ("insurer", "terms_code", "edition_code")
        )
        if all(value is not None for value in values):
            result.append(
                VerifiedEditionCandidate(row["id"], household, *cast(tuple[str, str, str], values))
            )
    return tuple(result)


class TermsChangeProjector:
    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def refresh_pending(
        self, *, limit: int = 5, stop_requested: Callable[[], bool] | None = None
    ) -> int:
        if type(limit) is not int or not 1 <= limit <= 25:
            raise ValueError("invalid terms change limit")
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            candidates = connection.execute(
                "SELECT c.id,c.household_space_id FROM terms_applicability_component_sources c "
                "LEFT JOIN terms_change_refresh_checks checked ON checked.source_component_id=c.id "
                "WHERE c.role='amendment' AND (checked.retry_after IS NULL "
                "OR checked.retry_after<=clock_timestamp()) "
                "ORDER BY checked.checked_at NULLS FIRST,c.id LIMIT %s",
                (limit,),
            ).fetchall()
        count = 0
        for candidate in candidates:
            if stop_requested and stop_requested():
                break
            try:
                with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                    connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    connection.execute("SET LOCAL statement_timeout='30s'")
                    count += self._refresh(
                        connection, candidate["id"], candidate["household_space_id"]
                    )
            except Exception:
                logger.warning("Terms change refresh deferred")
                with psycopg.connect(self.database_url) as connection:
                    connection.execute("SET LOCAL statement_timeout='5s'")
                    connection.execute(
                        "INSERT INTO terms_change_refresh_checks(source_component_id,retry_after) "
                        "VALUES(%s,clock_timestamp()+interval '30 seconds') "
                        "ON CONFLICT(source_component_id) "
                        "DO UPDATE SET checked_at=clock_timestamp(),input_digest=NULL,"
                        "retry_after=clock_timestamp()+interval '30 seconds'",
                        (candidate["id"],),
                    )
        return count

    def _refresh(
        self, connection: psycopg.Connection[dict[str, Any]], component: UUID, household: UUID
    ) -> bool:
        claimed = connection.execute(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS claimed",
            (f"terms-change:{component}",),
        ).fetchone()
        if claimed is None or not claimed["claimed"]:
            return False
        current = _context(connection, component, household)
        context = current["context"]
        if context is None or current["bytes"] > 1048576:
            return False
        prior = connection.execute(
            "SELECT input_digest FROM terms_change_refresh_checks WHERE source_component_id=%s",
            (component,),
        ).fetchone()
        if prior is not None and prior["input_digest"] == current["digest"]:
            connection.execute(
                "UPDATE terms_change_refresh_checks SET checked_at=clock_timestamp() "
                "WHERE source_component_id=%s",
                (component,),
            )
            return False
        source = connection.execute(
            "SELECT c.*,proposal.revision AS metadata_revision "
            "FROM terms_applicability_component_sources c JOIN document_metadata_publications p "
            "ON p.id=c.metadata_publication_id JOIN document_metadata_proposals proposal "
            "ON proposal.id=p.proposal_id WHERE c.id=%s AND c.household_space_id=%s",
            (component, household),
        ).fetchone()
        if source is None:
            return False
        # Coordinate with source publication before taking member/ledger locks.
        hashes = connection.execute(
            "SELECT DISTINCT v.content_sha256 FROM document_versions v JOIN "
            "document_structure_generations g ON g.document_version_id=v.id "
            "WHERE g.household_space_id=%s ORDER BY v.content_sha256",
            (household,),
        ).fetchall()
        if len(hashes) > 512:
            raise ValueError("terms change source budget")
        for row in hashes:
            lock_document_content(connection, household, row["content_sha256"])
        connection.execute(
            "SELECT id FROM family_members WHERE household_space_id=%s ORDER BY id FOR SHARE",
            (household,),
        )
        connection.execute(
            "SELECT id FROM policy_contracts WHERE household_space_id=%s ORDER BY id FOR SHARE",
            (household,),
        )
        connection.execute(
            "SELECT id FROM riders WHERE household_space_id=%s ORDER BY id FOR SHARE", (household,)
        )
        if _context(connection, component, household)["context"] != context:
            return False
        members = tuple(
            ChangeMember(household, UUID(row[0]), row[2], row[3], row[1])
            for row in context["members"]
        )
        member = source["family_member_id"]
        if source["page_end"] - source["page_start"] >= 25:
            raise ValueError("terms change source budget")
        projection = StructureProjectionReader(connection, household).read(
            source["generation_id"],
            tuple(range(source["page_start"], source["page_end"] + 1)),
        )
        if projection is None:
            return False
        observed = observe_terms_change(
            source["proof_json"],
            projection,
            metadata_revision=source["metadata_revision"],
            household_space_id=household,
            family_member_id=member,
            members=members,
        )
        current = _context(
            connection, component, household, scope_kind=observed.scope_kind, scope_observed=True
        )
        context = current["context"]
        if context is None or current["bytes"] > 1048576:
            return False
        contracts, riders = _source_candidates(connection, household, member, members)
        request = TermsChangeTargetRequest(
            household_space_id=household,
            **{
                name: getattr(observed, name)
                for name in TermsChangeTargetRequest.__dataclass_fields__
                if name != "household_space_id"
            },
        )
        editions = _edition_candidates(connection, household, member)
        target = resolve_terms_change_targets(request, contracts, riders, editions)
        clause_candidates = clause_change_candidates(connection, self.database_url, target)
        if observed.new_clause_label_declared and observed.new_clause_label_key is None:
            clause_candidates = tuple(
                candidate
                for candidate in clause_candidates
                if candidate.terms_edition_id != target.new_edition_id
            )
        if request.scope_kind == "CLAUSE":
            target = resolve_terms_change_targets(
                request, contracts, riders, editions, clauses=clause_candidates
            )
        clause_sources = {
            candidate.clause_id: candidate.source_assessment_id for candidate in clause_candidates
        }
        target, retained_sources = retain_uncertain_clause_targets(
            connection,
            target,
            component,
            source["metadata_publication_id"],
            source["generation_id"],
            source["content_sha256"],
        )
        clause_sources.update(retained_sources)
        if target.policy_contract_id is not None and any(
            decision[3] == str(target.policy_contract_id) for decision in context["user_decisions"]
        ):
            target = replace(
                target,
                status="UNKNOWN",
                reason_codes=tuple(sorted({*target.reason_codes, "USER_DOCUMENT_DECISION_EXISTS"})),
            )
        if len(target.reason_codes) > 32:
            raise ValueError("terms change reason budget")
        if (
            _context(
                connection,
                component,
                household,
                scope_kind=observed.scope_kind,
                scope_observed=True,
            )["context"]
            != context
        ):
            return False
        connection.execute(
            "INSERT INTO policy_terms_changes(household_space_id,family_member_id,"
            "source_component_id,source_publication_id,source_generation_id,source_content_sha256,"
            "policy_contract_id,rider_id,clause_id,scope_kind,scope_resolved,operation,change_kind,"
            "previous_edition_id,new_edition_id,effective_from,effective_through,status,revision,"
            "reason_codes,source_fields,input_context,input_digest,previous_clause_id,new_clause_id,"
            "previous_clause_source_id,new_clause_source_id) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
            "%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s) "
            "ON CONFLICT(source_component_id,revision,input_digest) DO NOTHING",
            (
                household,
                member,
                component,
                source["metadata_publication_id"],
                source["generation_id"],
                source["content_sha256"],
                target.policy_contract_id,
                target.rider_id,
                target.clause_id,
                target.scope_kind,
                target.scope_resolved,
                observed.operation,
                observed.change_kind,
                target.previous_edition_id,
                target.new_edition_id,
                observed.effective_from,
                observed.effective_through,
                target.status,
                REVISION,
                Jsonb(list(target.reason_codes)),
                Jsonb([asdict(field) for field in observed.source_fields]),
                current["context_json"],
                current["digest"],
                target.previous_clause_id,
                target.new_clause_id,
                clause_sources.get(target.previous_clause_id)
                if target.previous_clause_id is not None
                else None,
                clause_sources.get(target.new_clause_id)
                if target.new_clause_id is not None
                else None,
            ),
        )
        connection.execute(
            "INSERT INTO terms_change_refresh_checks(source_component_id,input_digest) "
            "VALUES(%s,%s) "
            "ON CONFLICT(source_component_id) DO UPDATE SET checked_at=clock_timestamp(),"
            "input_digest=excluded.input_digest,retry_after=NULL",
            (component, current["digest"]),
        )
        return True


@dataclass(frozen=True, slots=True, repr=False)
class ReplayedChange:
    targets: ResolvedTermsChangeTargets
    observed: ObservedTermsChange
    matches: bool


def replay_terms_change(
    connection: psycopg.Connection[dict[str, Any]], row: dict[str, Any]
) -> ReplayedChange | None:
    """Reconstruct source identities instead of trusting the stored MATCH targets."""
    household, component = row["household_space_id"], row["source_component_id"]
    before = _context(
        connection, component, household, scope_kind=row["scope_kind"], scope_observed=True
    )
    if (
        before["context"] is None
        or before["context"] != row["input_context"]
        or before["digest"] != row["input_digest"]
    ):
        return None
    source = connection.execute(
        "SELECT c.*,proposal.revision AS metadata_revision FROM "
        "terms_applicability_component_sources c "
        "JOIN document_metadata_publications p ON p.id=c.metadata_publication_id "
        "JOIN document_metadata_proposals proposal ON proposal.id=p.proposal_id "
        "WHERE c.id=%s AND c.household_space_id=%s",
        (component, household),
    ).fetchone()
    if source is None or source["page_end"] - source["page_start"] >= 25:
        return None
    members = tuple(
        ChangeMember(household, UUID(m[0]), m[2], m[3], m[1]) for m in before["context"]["members"]
    )
    projection = StructureProjectionReader(connection, household).read(
        source["generation_id"], tuple(range(source["page_start"], source["page_end"] + 1))
    )
    if projection is None:
        return None
    observed = observe_terms_change(
        source["proof_json"],
        projection,
        metadata_revision=source["metadata_revision"],
        household_space_id=household,
        family_member_id=source["family_member_id"],
        members=members,
    )
    request = TermsChangeTargetRequest(
        household_space_id=household,
        **{
            name: getattr(observed, name)
            for name in TermsChangeTargetRequest.__dataclass_fields__
            if name != "household_space_id"
        },
    )
    contracts, riders = _source_candidates(
        connection, household, source["family_member_id"], members
    )
    editions = _edition_candidates(connection, household, source["family_member_id"])
    targets = resolve_terms_change_targets(request, contracts, riders, editions)
    # The repository adapter uses only this supplied connection during preflight.
    candidates = clause_change_candidates(connection, connection.info.dsn, targets)
    if observed.new_clause_label_declared and observed.new_clause_label_key is None:
        candidates = tuple(c for c in candidates if c.terms_edition_id != targets.new_edition_id)
    if request.scope_kind == "CLAUSE":
        targets = resolve_terms_change_targets(
            request, contracts, riders, editions, clauses=candidates
        )
    if targets.policy_contract_id is not None and any(
        decision[3] == str(targets.policy_contract_id)
        for decision in before["context"]["user_decisions"]
    ):
        targets = replace(
            targets,
            status="UNKNOWN",
            reason_codes=tuple(sorted({*targets.reason_codes, "USER_DOCUMENT_DECISION_EXISTS"})),
        )
    sources = {c.clause_id: c.source_assessment_id for c in candidates}
    matches = (
        targets.status == "MATCH"
        and all(
            getattr(targets, name) == row[name]
            for name in (
                "household_space_id",
                "family_member_id",
                "policy_contract_id",
                "rider_id",
                "clause_id",
                "previous_clause_id",
                "new_clause_id",
                "previous_edition_id",
                "new_edition_id",
                "scope_kind",
                "scope_resolved",
            )
        )
        and all(
            getattr(observed, name) == row[name]
            for name in ("operation", "change_kind", "effective_from", "effective_through")
        )
    )
    matches = matches and row["source_fields"] == json.loads(
        json.dumps([asdict(f) for f in observed.source_fields])
    )
    matches = matches and all(
        row[f"{side}_clause_source_id"] == sources.get(getattr(targets, f"{side}_clause_id"))
        for side in ("previous", "new")
    )
    matches = matches and all(
        row[key] == source[field]
        for key, field in (
            ("source_publication_id", "metadata_publication_id"),
            ("source_generation_id", "generation_id"),
            ("source_content_sha256", "content_sha256"),
        )
    )
    if (
        _context(
            connection, component, household, scope_kind=row["scope_kind"], scope_observed=True
        )
        != before
    ):
        return None
    return ReplayedChange(targets, observed, matches)


def read_event_terms(
    connection: psycopg.Connection[dict[str, Any]],
    scope: TermsSelectionScope,
    event_date: date | None,
    *,
    change_cache: dict[tuple[UUID, str], ReplayedChange | None] | None = None,
) -> TermsEventSelection:
    """Select current evidence for a new analysis; stored past selections are never rewritten."""
    valid = connection.execute(
        "SELECT p.id FROM policy_contracts p JOIN family_members m ON m.id=%s "
        "AND m.household_space_id=p.household_space_id AND m.deleted_at IS NULL "
        "WHERE p.id=%s AND p.household_space_id=%s AND p.deleted_at IS NULL "
        "AND EXISTS(SELECT 1 FROM policy_parties party WHERE party.policy_contract_id=p.id "
        "AND party.household_space_id=p.household_space_id AND party.family_member_id=m.id "
        "AND party.deleted_at IS NULL AND party.role IN ('primary_insured','additional_insured')) "
        "AND (%s::uuid IS NULL OR EXISTS(SELECT 1 FROM riders r WHERE r.id=%s "
        "AND r.policy_contract_id=p.id AND r.household_space_id=p.household_space_id "
        "AND r.deleted_at IS NULL)) "
        "AND (%s::uuid IS NULL OR EXISTS(SELECT 1 FROM clauses c WHERE c.id=%s "
        "AND c.household_space_id=p.household_space_id AND c.deleted_at IS NULL))",
        (
            scope.family_member_id,
            scope.policy_contract_id,
            scope.household_space_id,
            scope.rider_id,
            scope.rider_id,
            scope.clause_id,
            scope.clause_id,
        ),
    ).fetchone()
    if valid is None:
        return select_terms_for_event(scope, event_date, (), ())
    editions = connection.execute(
        "SELECT e.id,policy_terms_link_applicability(%s,e.id,%s) AS applies FROM terms_editions e "
        "LEFT JOIN insurance_document_components c ON c.id=e.source_component_id "
        "WHERE e.household_space_id=%s AND e.deleted_at IS NULL "
        "AND (e.source_component_id IS NULL OR c.family_member_id=%s) ORDER BY e.id LIMIT 513",
        (
            scope.policy_contract_id,
            scope.household_space_id,
            scope.household_space_id,
            scope.family_member_id,
        ),
    ).fetchall()
    base_assessments = connection.execute(
        "SELECT id,terms_edition_id,status,selection_state FROM current_policy_terms_applicability "
        "WHERE household_space_id=%s AND family_member_id=%s AND policy_contract_id=%s "
        "ORDER BY id LIMIT 513",
        (scope.household_space_id, scope.family_member_id, scope.policy_contract_id),
    ).fetchall()
    base_statuses: dict[UUID, set[TermsStatus]] = {}
    for assessment in base_assessments:
        base_statuses.setdefault(assessment["terms_edition_id"], set()).add(
            assessment["status"]
            if assessment["status"] != "MATCH"
            or assessment["selection_state"] in ("AUTOMATIC", "USER_SELECTED")
            else "UNKNOWN"
        )
    relations = connection.execute(
        "SELECT a.*,a.input_context IS NOT DISTINCT FROM "
        "terms_change_input_context(a.source_component_id,a.household_space_id,a.scope_kind) AS "
        "source_current "
        "FROM effective_policy_terms_changes a WHERE a.household_space_id=%s "
        "AND a.family_member_id=%s "
        "AND a.policy_contract_id=%s AND (a.scope_resolved OR a.scope_kind='CLAUSE') "
        "AND (a.scope_kind='CONTRACT' OR (a.scope_kind='RIDER' AND a.rider_id=%s) "
        "OR (a.scope_kind='CLAUSE' AND %s::uuid IS NOT NULL AND a.rider_id=%s)) "
        "ORDER BY a.id LIMIT 129",
        (
            scope.household_space_id,
            scope.family_member_id,
            scope.policy_contract_id,
            scope.rider_id,
            scope.clause_id,
            scope.rider_id,
        ),
    ).fetchall()
    if len(editions) > 512 or len(relations) > 128 or len(base_assessments) > 512:
        raise TermsSelectionError
    cache = change_cache if change_cache is not None else {}
    replayed_rows = []
    for row in relations:
        if row["status"] == "MATCH" and row["source_current"]:
            key = (row["id"], row["input_digest"])
            if key not in cache:
                cache[key] = replay_terms_change(connection, row)
            replay = cache[key]
            if replay is None:
                row = dict(row, status="UNKNOWN", reason_codes=["CHANGE_TARGET_PROOF_UNRESOLVED"])
            elif not replay.matches:
                target = replay.targets
                if (
                    target.policy_contract_id != scope.policy_contract_id
                    or target.family_member_id != scope.family_member_id
                    or (target.rider_id is not None and target.rider_id != scope.rider_id)
                ):
                    continue
                row = dict(
                    row,
                    status="UNKNOWN",
                    reason_codes=["CHANGE_TARGET_PROOF_INVALID"],
                    source_fields=json.loads(
                        json.dumps([asdict(f) for f in replay.observed.source_fields])
                    ),
                    **{
                        name: getattr(replay.observed, name)
                        for name in (
                            "operation",
                            "change_kind",
                            "effective_from",
                            "effective_through",
                        )
                    },
                    **{
                        name: getattr(target, name)
                        for name in (
                            "clause_id",
                            "previous_clause_id",
                            "new_clause_id",
                            "previous_edition_id",
                            "new_edition_id",
                            "scope_kind",
                            "scope_resolved",
                        )
                    },
                )
        replayed_rows.append(row)
    relations = replayed_rows
    # Follow exact original-Clause identity edges so a later B->C change also
    # retains the A->B provenance. The requested scope is never broadened to all
    # clauses of an edition or Rider, even when only one side is known.
    related_clauses = {scope.clause_id} if scope.clause_id is not None else set()
    ancestor_rows = (
        connection.execute(
            "WITH RECURSIVE ancestors AS (SELECT c.id,c.parent_clause_id,c.terms_edition_id,"
            "ARRAY[c.id] AS path,0 AS depth FROM clauses c WHERE c.id=%s AND "
            "c.household_space_id=%s "
            "AND c.deleted_at IS NULL UNION ALL SELECT p.id,p.parent_clause_id,p.terms_edition_id,"
            "a.path||p.id,a.depth+1 FROM ancestors a JOIN clauses p ON p.id=a.parent_clause_id "
            "AND p.household_space_id=%s AND p.terms_edition_id=a.terms_edition_id "
            "AND p.deleted_at IS NULL WHERE a.depth<32 AND NOT p.id=ANY(a.path)) "
            "SELECT id FROM ancestors WHERE depth>0",
            (scope.clause_id, scope.household_space_id, scope.household_space_id),
        ).fetchall()
        if scope.clause_id is not None
        else []
    )
    ancestor_clauses = {row["id"] for row in ancestor_rows}
    for identifiers in (related_clauses, ancestor_clauses):
        expanded = True
        while expanded:
            expanded = False
            for row in relations:
                if (
                    row["scope_kind"] != "CLAUSE"
                    or row["status"] != "MATCH"
                    or not row["source_current"]
                ):
                    continue
                sides = {
                    value
                    for value in (row["previous_clause_id"], row["new_clause_id"], row["clause_id"])
                    if value is not None
                }
                if identifiers & sides and not sides <= identifiers:
                    identifiers.update(sides)
                    expanded = True
    for index, row in enumerate(relations):
        sides = {row["previous_clause_id"], row["new_clause_id"], row["clause_id"]}
        if (
            row["scope_kind"] == "CLAUSE"
            and ancestor_clauses & sides
            and not related_clauses & sides
        ):
            relations[index] = dict(
                row,
                status="UNKNOWN",
                descendant_affected=True,
                reason_codes=sorted({*row["reason_codes"], "CLAUSE_DESCENDANT_TARGET_UNRESOLVED"}),
            )
    uncertain_matches: set[UUID] = set()
    if scope.clause_id is not None and any(
        r["scope_kind"] == "CLAUSE" and (r["status"] != "MATCH" or not r["source_current"])
        for r in relations
    ):
        clause = connection.execute(
            "SELECT terms_edition_id FROM clauses WHERE id=%s AND household_space_id=%s "
            "AND deleted_at IS NULL",
            (scope.clause_id, scope.household_space_id),
        ).fetchone()
        source = read_verified_clause_source(connection, scope.household_space_id, scope.clause_id)
        for row in relations:
            if (
                clause is None
                or row["scope_kind"] != "CLAUSE"
                or (row["status"] == "MATCH" and row["source_current"])
            ):
                continue
            for side in ("previous", "new"):
                if clause["terms_edition_id"] != row[f"{side}_edition_id"]:
                    continue
                labels = {
                    _key(f["value"])
                    for f in row["source_fields"]
                    if f["name"] == ("clause_label" if side == "previous" else "new_clause_label")
                }
                if side == "new" and not labels:
                    labels = {
                        _key(f["value"])
                        for f in row["source_fields"]
                        if f["name"] == "clause_label"
                    }
                # Multiple physical/entity candidates do not grant a target ID.
                # They still prevent an affected rule from silently using the
                # old base as confirmed. Unsupported source identity is also
                # uncertainty, never permission for the new edition.
                if source is None or not labels or _key(source.region.label) in labels:
                    uncertain_matches.add(row["id"])
    relations = [
        row
        for row in relations
        if row["scope_kind"] != "CLAUSE"
        or row.get("descendant_affected", False)
        or row["id"] in uncertain_matches
        or related_clauses & {row["previous_clause_id"], row["new_clause_id"], row["clause_id"]}
    ]

    def base_edition(row: dict[str, Any]) -> BaseTermsEdition:
        states = base_statuses.get(row["id"], set())
        status: TermsStatus = "UNKNOWN"
        if states == {"NO_MATCH"}:
            status = "NO_MATCH"
        elif row["applies"] and (not states or states == {"MATCH"}):
            status = "MATCH"
        return BaseTermsEdition(
            row["id"],
            status,
            ("BASE_TERMS_SOURCE_UNRESOLVED",) if status == "UNKNOWN" else (),
        )

    selection = select_terms_for_event(
        scope,
        event_date,
        tuple(base_edition(row) for row in editions),
        tuple(
            TermsChangeRelation(
                row["id"],
                TermsSelectionScope(
                    scope.household_space_id,
                    scope.policy_contract_id,
                    scope.family_member_id,
                    row["rider_id"],
                    scope.clause_id if row["scope_kind"] == "CLAUSE" else row["clause_id"],
                ),
                row["operation"],
                row["previous_edition_id"],
                row["new_edition_id"],
                row["effective_from"],
                row["effective_through"],
                row["status"] if row["source_current"] else "UNKNOWN",
                tuple(row["reason_codes"]) if row["source_current"] else ("CHANGE_SOURCE_STALE",),
            )
            for row in relations
        ),
    )
    return replace(
        selection,
        base_assessment_ids=tuple(row["id"] for row in base_assessments),
        scope_relation_ids=tuple(
            row["id"]
            for row in relations
            if row["scope_kind"] == "CLAUSE"
            and row["status"] == "MATCH"
            and row["source_current"]
            and not row.get("descendant_affected", False)
        ),
    )
