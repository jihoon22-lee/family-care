"""Project retained changes onto existing source-proven identities and event terms."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import date
from typing import Any, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from familycare_api.clauses.terms_applicability_repository import _key, _scalar
from familycare_api.clauses.terms_change_selection import (
    BaseTermsEdition,
    TermsChangeRelation,
    TermsEventSelection,
    TermsSelectionError,
    TermsSelectionScope,
    select_terms_for_event,
)
from familycare_api.clauses.terms_change_source import ChangeMember, observe_terms_change
from familycare_api.clauses.terms_change_targets import (
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

REVISION = "terms-change-v1"
logger = logging.getLogger(__name__)


def _context(
    connection: psycopg.Connection[dict[str, Any]], component: UUID, household: UUID
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT context,encode(sha256(convert_to(context::text,'UTF8')),'hex') AS digest,"
        "octet_length(context::text) AS bytes FROM ("
        "SELECT terms_change_input_context(%s,%s) AS context) current_input",
        (component, household),
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

    @staticmethod
    def _refresh(
        connection: psycopg.Connection[dict[str, Any]], component: UUID, household: UUID
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
        contracts, riders = _source_candidates(connection, household, member, members)
        request = TermsChangeTargetRequest(
            household_space_id=household,
            **{
                name: getattr(observed, name)
                for name in TermsChangeTargetRequest.__dataclass_fields__
                if name != "household_space_id"
            },
        )
        target = resolve_terms_change_targets(
            request, contracts, riders, _edition_candidates(connection, household, member)
        )
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
        if _context(connection, component, household)["context"] != context:
            return False
        connection.execute(
            "INSERT INTO policy_terms_changes(household_space_id,family_member_id,"
            "source_component_id,source_publication_id,source_generation_id,source_content_sha256,"
            "policy_contract_id,rider_id,clause_id,scope_kind,scope_resolved,operation,change_kind,"
            "previous_edition_id,new_edition_id,effective_from,effective_through,status,revision,"
            "reason_codes,source_fields,input_context,input_digest) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
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
                Jsonb(context),
                current["digest"],
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


def read_event_terms(
    connection: psycopg.Connection[dict[str, Any]],
    scope: TermsSelectionScope,
    event_date: date | None,
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
    relations = connection.execute(
        "SELECT a.*,a.input_context IS NOT DISTINCT FROM "
        "terms_change_input_context(a.source_component_id,a.household_space_id) AS source_current "
        "FROM effective_policy_terms_changes a WHERE a.household_space_id=%s "
        "AND a.family_member_id=%s "
        "AND a.policy_contract_id=%s AND a.scope_resolved "
        "AND (a.scope_kind='CONTRACT' OR (a.scope_kind='RIDER' AND a.rider_id=%s) "
        "OR (a.scope_kind='CLAUSE' AND a.clause_id=%s AND (a.rider_id IS NULL OR a.rider_id=%s))) "
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
    if len(editions) > 512 or len(relations) > 128:
        raise TermsSelectionError
    return select_terms_for_event(
        scope,
        event_date,
        tuple(
            BaseTermsEdition(row["id"], "MATCH" if row["applies"] else "NO_MATCH")
            for row in editions
        ),
        tuple(
            TermsChangeRelation(
                row["id"],
                TermsSelectionScope(
                    scope.household_space_id,
                    scope.policy_contract_id,
                    scope.family_member_id,
                    row["rider_id"],
                    row["clause_id"],
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
