"Retain canonical coverage identity without changing either source's field values."

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from familycare_api.common.coverage_identity import (
    CanonicalCoverageIdentity,
    CanonicalCoverageRef,
    CoverageConflictField,
)
from familycare_api.common.scope import HouseholdScope
from familycare_api.insurance_reconciliation.canonical_match import (
    ProgramEnrollmentSource,
    VerifiedDocumentBinding,
    match_canonical_enrollment,
)
from familycare_api.insurance_reconciliation.source_inventory import unique_native_name_location
from familycare_api.policies.enrollment_locator import physical_enrollment_locator

_MAX_INVENTORY_BYTES = 64 * 1024 * 1024


class CanonicalLinkError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("CANONICAL_LINK_UNAVAILABLE")


@dataclass(frozen=True, repr=False)
class CanonicalCoverageLink:
    knowledge_coverage_id: UUID
    knowledge_contract_id: UUID
    import_run_id: UUID
    family_member_id: UUID
    policy_contract_id: UUID
    rider_id: UUID
    ledger_version: int
    field_value_conflict: bool
    proofs: tuple[dict[str, Any], ...]
    fingerprint: str
    field_conflicts: tuple[CoverageConflictField, ...] = ()

    def identity(self) -> CanonicalCoverageIdentity:
        operational = CanonicalCoverageRef(
            kind="OPERATIONAL_RIDER", contract_id=self.policy_contract_id, coverage_id=self.rider_id
        )
        return CanonicalCoverageIdentity(
            ref=operational,
            source_refs=(
                CanonicalCoverageRef(
                    kind="PRIVATE_KNOWLEDGE_COVERAGE",
                    contract_id=self.knowledge_contract_id,
                    coverage_id=self.knowledge_coverage_id,
                ),
                operational,
            ),
            authority="PROGRAM_VERIFIED_SOURCE_IDENTITY",
            ledger_version=self.ledger_version,
            verification_digest_sha256=self.fingerprint,
            field_conflicts=self.field_conflicts,
        )


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()


class _SourceInventory:
    """Read every generation of one bound page; retain only the last page in RAM."""

    def __init__(
        self, connection: psycopg.Connection[dict[str, Any]], scope: HouseholdScope
    ) -> None:
        self.connection = connection
        self.scope = scope
        self.key: tuple[str, int] | None = None
        self.value: dict[UUID, dict[str, Any]] = {}

    def page(self, content_sha256: str, physical_page: int) -> dict[UUID, dict[str, Any]]:
        key = content_sha256, physical_page
        if self.key == key:
            return self.value
        # Read generation metadata first. Stop before building further projections
        # once the complete-page budget cannot be met; never approve a subset.
        generations = self.connection.execute(
            """
            SELECT g.id FROM document_structure_generations g
            JOIN document_versions v ON v.id=g.document_version_id
            JOIN documents d ON d.id=v.document_id AND d.deleted_at IS NULL
            WHERE g.household_space_id=%s AND v.content_sha256=%s
            ORDER BY g.id LIMIT 10001
            """,
            (self.scope.household_space_id, content_sha256),
        ).fetchall()
        self.key, self.value = key, {}
        if len(generations) > 10000:
            return self.value
        remaining = _MAX_INVENTORY_BYTES
        inventory: dict[UUID, dict[str, Any]] = {}
        for generation in generations:
            if remaining <= 0:
                return self.value
            row = self.connection.execute(
                """
                WITH projected AS MATERIALIZED (
                  SELECT document_structure_projection(%s,%s,ARRAY[%s]::integer[]) AS value
                )
                SELECT octet_length(value::text) AS byte_count,
                  CASE WHEN octet_length(value::text)<=%s THEN value ELSE NULL END AS structure
                FROM projected
                """,
                (generation["id"], self.scope.household_space_id, physical_page, remaining),
            ).fetchone()
            if row is None or row["structure"] is None:
                return self.value
            remaining -= row["byte_count"]
            inventory[generation["id"]] = row["structure"]
        self.value = inventory
        return self.value


def _name_source(
    inventory: _SourceInventory,
    row: dict[str, Any],
    refs: list[dict[str, Any]],
    bound_pages: set[int],
) -> tuple[dict[str, Any], UUID] | None:
    """Select name evidence only after every original primary view agrees.

    A primary table header supports the name field without locating the name.
    Equivalent row/line views may nominate a representative only when each
    independently reproduces the complete field's single native locator.
    """
    if not 1 <= len(refs) <= 64:
        return None
    primary = [ref for ref in refs if ref["primary"] and ref["source_role"] == "policy"]
    pages = {ref["page"] for ref in primary} & bound_pages
    located = []
    for page in sorted(pages):
        structure = inventory.page(row["content_sha256"], page).get(row["generation_id"])
        if structure is None:
            continue
        locator = physical_enrollment_locator(structure, row["original_rider_name"], refs)
        if (
            locator is None
            or locator["content_sha256"] != row["content_sha256"]
            or locator["physical_page"] != page
        ):
            continue
        name_evidence = {
            UUID(ref["evidence_id"])
            for ref in primary
            if ref["page"] == page
            and physical_enrollment_locator(structure, row["original_rider_name"], [ref]) == locator
        }
        if name_evidence:
            located.append((locator, min(name_evidence)))
    return located[0] if len(located) == 1 else None


def _certificate_pages(coverages: list[dict[str, Any]]) -> dict[str, set[int]]:
    pages: dict[str, set[int]] = defaultdict(set)
    for coverage in coverages:
        source = coverage["source_record_json"]
        if _digest(source) != coverage["source_record_digest_sha256"]:
            continue
        review = source.get("certificate_review")
        if not isinstance(review, dict):
            continue
        locations = review.get("evidence_locations")
        if not isinstance(locations, list):
            continue
        for location in locations:
            if not isinstance(location, dict):
                continue
            alias, page = location.get("document_alias"), location.get("physical_page")
            if isinstance(alias, str) and type(page) is int and 1 <= page <= 500:
                pages[alias].add(page)
    return pages


def _proposals(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    *,
    import_run_id: UUID | None = None,
) -> tuple[CanonicalCoverageLink, ...]:
    coverages = connection.execute(
        """
        SELECT c.*, k.source_contract_key, k.policy_contract_id AS snapshot_policy_id,
        s.family_member_id
        FROM private_knowledge_coverages c
        JOIN household_spaces h ON h.id=c.household_space_id AND h.deleted_at IS NULL
        JOIN private_knowledge_import_runs run ON run.id=c.import_run_id
          AND run.household_space_id=c.household_space_id
          AND ((%s::uuid IS NULL AND run.is_current AND run.state='APPLIED')
            OR (run.id=%s AND run.state IN ('APPLIED','SUPERSEDED')))
        JOIN private_knowledge_contracts k ON k.id=c.knowledge_contract_id
          AND k.import_run_id=c.import_run_id
        JOIN private_knowledge_subjects s ON s.id=k.subject_id AND s.import_run_id=k.import_run_id
          AND s.binding_decision='MATCH' AND NOT s.binding_conflict
        JOIN family_members m ON m.id=s.family_member_id AND
        m.household_space_id=c.household_space_id
          AND m.deleted_at IS NULL
        WHERE c.household_space_id=%s AND c.enrollment_decision='MATCH'
          AND c.component_classification='BENEFIT_COVERAGE'
        ORDER BY c.id LIMIT 10001
    """,
        (import_run_id, import_run_id, scope.household_space_id),
    ).fetchall()
    if len(coverages) > 10000:
        raise CanonicalLinkError
    if not coverages:
        return ()
    requested_pages = _certificate_pages(coverages)
    if not requested_pages:
        return ()
    run_id = coverages[0]["import_run_id"]
    bindings = connection.execute(
        """
        SELECT b.*, d.source_alias FROM private_knowledge_source_bindings b
        JOIN private_knowledge_document_bindings d ON d.id=b.document_binding_id
          AND d.import_run_id=b.import_run_id AND d.household_space_id=b.household_space_id
          AND d.source_alias_digest_sha256=b.source_alias_digest_sha256
        JOIN document_versions v ON v.id=b.document_version_id AND v.content_sha256=b.content_sha256
          AND v.page_count=b.page_count
        JOIN documents doc ON doc.id=v.document_id AND doc.deleted_at IS NULL
          AND doc.document_kind=b.document_kind
        JOIN evidence e ON e.id=b.evidence_id AND e.document_version_id=v.id
          AND e.content_sha256=v.content_sha256 AND e.household_space_id=b.household_space_id
          AND e.physical_page BETWEEN 1 AND v.page_count
        JOIN extractions x ON x.id=e.extraction_id AND x.document_version_id=v.id AND
        x.status='succeeded'
        WHERE b.import_run_id=%s AND b.household_space_id=%s AND b.is_current
          AND d.source_alias=ANY(%s)
    """,
        (run_id, scope.household_space_id, list(requested_pages)),
    ).fetchall()
    by_alias = {
        row["source_alias"]: VerifiedDocumentBinding(
            row["id"],
            row["document_version_id"],
            row["content_sha256"],
            row["page_count"],
            row["document_kind"],
        )
        for row in bindings
    }
    if not by_alias:
        return ()
    bound_pages: dict[str, set[int]] = defaultdict(set)
    for alias, binding in by_alias.items():
        bound_pages[binding.content_sha256].update(requested_pages[alias])
    rows = connection.execute(
        """
        SELECT p.candidate_version_id, p.policy_contract_id,p.rider_id,
          p.authority AS publication_authority,
          r.version AS ledger_version,r.insured_amount,r.display_name,r.currency,
          s.source_refs, f.value AS original_rider_name, g.id AS generation_id,
          f.candidate_version_id AS name_source_candidate_version_id,
          g.document_version_id,v.content_sha256,j.family_member_id,
          ARRAY(SELECT a.evidence_id::text FROM analysis_candidate_evidence a
            WHERE a.candidate_version_id=f.candidate_version_id AND a.field_id='rider_name')
            name_ids
        FROM range_enrollment_publications p
        JOIN riders r ON r.id=p.rider_id AND r.household_space_id=p.household_space_id
          AND r.policy_contract_id=p.policy_contract_id AND r.deleted_at IS NULL
        JOIN policy_contracts policy ON policy.id=p.policy_contract_id
          AND policy.household_space_id=p.household_space_id AND policy.deleted_at IS NULL
        JOIN policy_range_candidate_sources s ON
        s.candidate_version_id=p.source_candidate_version_id
        JOIN analysis_candidate_fields f ON f.candidate_version_id IN
          (s.candidate_version_id,p.candidate_version_id) AND f.field_id='rider_name'
        JOIN analysis_candidate_versions root ON root.id=s.candidate_version_id
        JOIN policy_structuring_jobs j ON j.id=s.job_id AND
        j.household_space_id=p.household_space_id
        JOIN document_policy_range_plans plan ON plan.job_id=j.id
        JOIN document_structure_generations g ON g.id=plan.generation_id
          AND g.household_space_id=p.household_space_id
        JOIN document_versions v ON v.id=g.document_version_id
        JOIN documents doc ON doc.id=v.document_id AND doc.deleted_at IS NULL AND
        doc.document_kind='policy'
        JOIN extractions x ON x.id=g.extraction_id AND x.status='succeeded' AND
        x.document_version_id=v.id
        JOIN family_members member ON member.id=j.family_member_id
          AND member.household_space_id=p.household_space_id AND member.deleted_at IS NULL
        WHERE p.household_space_id=%s
          AND p.authority IN ('PROGRAM_VERIFIED','USER_CONFIRMED')
          AND v.content_sha256=ANY(%s)
          AND NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements(s.source_refs) ref WHERE NOT EXISTS (
              SELECT 1 FROM evidence e WHERE e.id::text=ref->>'evidence_id'
                AND e.household_space_id=p.household_space_id
                AND e.document_version_id=g.document_version_id AND e.extraction_id=g.extraction_id
                AND e.content_sha256=v.content_sha256 AND e.physical_page::text=ref->>'page'
                AND e.document_version_id::text=ref->>'document_version_id'
                AND e.extraction_id::text=ref->>'extraction_id'
            )
          )
          AND EXISTS (
            SELECT 1 FROM range_enrollment_publications source_publication
            JOIN analysis_candidate_evidence ce ON
            ce.candidate_version_id=source_publication.candidate_version_id
              AND ce.field_id='rider_name' AND ce.evidence_id=r.source_evidence_id
            JOIN evidence e ON e.id=ce.evidence_id AND e.household_space_id=p.household_space_id
            JOIN document_versions source_version ON source_version.id=e.document_version_id
              AND source_version.content_sha256=e.content_sha256
            JOIN documents source_document ON source_document.id=source_version.document_id
              AND source_document.deleted_at IS NULL AND source_document.document_kind='policy'
            JOIN extractions source_extraction ON source_extraction.id=e.extraction_id
              AND source_extraction.document_version_id=e.document_version_id
              AND source_extraction.status='succeeded'
            WHERE source_publication.rider_id=r.id
              AND source_publication.household_space_id=p.household_space_id
              AND source_publication.policy_contract_id=p.policy_contract_id
          )
          AND EXISTS (SELECT 1 FROM policy_parties party WHERE party.policy_contract_id=policy.id
            AND party.household_space_id=p.household_space_id AND
            party.family_member_id=j.family_member_id
            AND party.role='primary_insured' AND party.deleted_at IS NULL)
        ORDER BY p.candidate_version_id,
          (f.candidate_version_id=s.candidate_version_id) DESC, f.candidate_version_id
        LIMIT 10001
    """,
        (scope.household_space_id, list({b.content_sha256 for b in by_alias.values()})),
    ).fetchall()
    if len(rows) > 10000:
        raise CanonicalLinkError
    inventory = _SourceInventory(connection, scope)
    candidates: list[ProgramEnrollmentSource] = []
    operational = {}
    located_publications: set[UUID] = set()
    native_names: dict[UUID, str] = {}
    for row in rows:
        if row["candidate_version_id"] in located_publications:
            continue
        refs = [ref for ref in row["source_refs"] if ref["evidence_id"] in row["name_ids"]]
        name_source = _name_source(inventory, row, refs, bound_pages[row["content_sha256"]])
        if name_source is None:
            continue
        locator, name_evidence_id = name_source
        # Identity follows the original proven mention. A corrected native name
        # is considered only when the original name has no physical proof.
        located_publications.add(row["candidate_version_id"])
        enrollment = ProgramEnrollmentSource(
            household_space_id=scope.household_space_id,
            family_member_id=row["family_member_id"],
            policy_contract_id=row["policy_contract_id"],
            rider_id=row["rider_id"],
            original_rider_name=row["original_rider_name"],
            document_version_id=row["document_version_id"],
            content_sha256=row["content_sha256"],
            physical_page=locator["physical_page"],
            publication_candidate_version_id=row["candidate_version_id"],
            evidence_id=name_evidence_id,
            generation_id=row["generation_id"],
            physical_locator=locator,
            source_refs=tuple(refs),
            publication_authority=row["publication_authority"],
            name_source_candidate_version_id=row["name_source_candidate_version_id"],
        )
        native_names[row["candidate_version_id"]] = row["original_rider_name"]
        candidates.append(enrollment)
        operational[row["rider_id"]] = row
    user_links = connection.execute(
        """
        SELECT link.*, k.source_contract_key FROM private_knowledge_operational_links link
        JOIN private_knowledge_contracts k ON k.id=link.knowledge_contract_id
          AND k.import_run_id=link.import_run_id
        WHERE link.household_space_id=%s AND link.is_current
    """,
        (scope.household_space_id,),
    ).fetchall()
    reserved_policies = connection.execute(
        "SELECT id,policy_contract_id FROM private_knowledge_contracts WHERE "
        "import_run_id=%s AND policy_contract_id IS NOT NULL",
        (run_id,),
    ).fetchall()
    reserved_riders = connection.execute(
        "SELECT id,rider_id FROM private_knowledge_coverages WHERE import_run_id=%s AND "
        "rider_id IS NOT NULL",
        (run_id,),
    ).fetchall()
    result = []
    for coverage in coverages:
        source = coverage["source_record_json"]
        if _digest(source) != coverage["source_record_digest_sha256"]:
            continue
        match = match_canonical_enrollment(
            source, by_alias, candidates, expected_family_member_id=coverage["family_member_id"]
        )
        if match is None or (
            coverage["snapshot_policy_id"] is not None
            and coverage["snapshot_policy_id"] != match.policy_contract_id
        ):
            continue
        if coverage["rider_id"] is not None and coverage["rider_id"] != match.rider_id:
            continue
        if any(
            row["policy_contract_id"] == match.policy_contract_id
            and row["id"] != coverage["knowledge_contract_id"]
            for row in reserved_policies
        ) or any(
            row["rider_id"] == match.rider_id and row["id"] != coverage["id"]
            for row in reserved_riders
        ):
            continue
        if any(
            (
                link["source_contract_key"] == coverage["source_contract_key"]
                and (
                    link["decision"] != "MATCH"
                    or link["link_conflict"]
                    or link["policy_contract_id"] != match.policy_contract_id
                )
            )
            or (
                link["policy_contract_id"] == match.policy_contract_id
                and link["source_contract_key"] != coverage["source_contract_key"]
            )
            for link in user_links
        ):
            continue
        source_unique = True
        for proof in match.proofs:
            structures = inventory.page(
                proof.physical_locator["content_sha256"],
                proof.private_evidence_location["physical_page"],
            )
            if proof.generation_id not in structures or not all(
                unique_native_name_location(
                    structure,
                    proof.private_evidence_location["physical_page"],
                    native_names[proof.publication_candidate_version_id],
                    proof.physical_locator,
                )
                for structure in structures.values()
            ):
                source_unique = False
                break
        if not source_unique:
            continue
        row = operational[match.rider_id]
        retained_proofs = tuple(
            json.loads(json.dumps(asdict(p), default=str)) for p in match.proofs
        )
        fields: tuple[CoverageConflictField, ...] = ("insured_amount", "currency", "display_name")
        field_conflicts = tuple(field for field in fields if coverage[field] != row[field])
        data = dict(
            knowledge_coverage_id=coverage["id"],
            knowledge_contract_id=coverage["knowledge_contract_id"],
            import_run_id=run_id,
            family_member_id=coverage["family_member_id"],
            policy_contract_id=match.policy_contract_id,
            rider_id=match.rider_id,
            ledger_version=row["ledger_version"],
            field_value_conflict=bool(field_conflicts),
            field_conflicts=field_conflicts,
            proofs=retained_proofs,
        )
        fingerprint = _digest({"source_digest": coverage["source_record_digest_sha256"], **data})
        result.append(CanonicalCoverageLink(**data, fingerprint=fingerprint))
    counts = Counter(item.rider_id for item in result)
    contract_policies: dict[UUID, set[UUID]] = defaultdict(set)
    policy_contracts: dict[UUID, set[UUID]] = defaultdict(set)
    for item in result:
        contract_policies[item.knowledge_contract_id].add(item.policy_contract_id)
        policy_contracts[item.policy_contract_id].add(item.knowledge_contract_id)
    return tuple(
        item
        for item in result
        if counts[item.rider_id] == 1
        and len(contract_policies[item.knowledge_contract_id]) == 1
        and len(policy_contracts[item.policy_contract_id]) == 1
    )


class CanonicalLinkRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    def refresh_pending(self) -> int:
        try:
            with psycopg.connect(
                self.database_url, row_factory=dict_row, connect_timeout=5
            ) as connection:
                households = connection.execute("""
                    SELECT DISTINCT b.household_space_id FROM private_knowledge_source_bindings b
                    JOIN private_knowledge_import_runs r ON r.id=b.import_run_id
                      AND r.household_space_id=b.household_space_id AND r.is_current AND
                      r.state='APPLIED'
                    JOIN household_spaces h ON h.id=b.household_space_id AND h.deleted_at IS NULL
                    WHERE b.is_current ORDER BY b.household_space_id LIMIT 26
                """).fetchall()
            if len(households) > 25:
                raise CanonicalLinkError
            return sum(
                self.refresh(HouseholdScope(row["household_space_id"])) for row in households
            )
        except psycopg.Error:
            raise CanonicalLinkError from None

    def refresh(self, scope: HouseholdScope) -> int:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET LOCAL statement_timeout='10s'")
                if (
                    connection.execute(
                        "SELECT id FROM household_spaces WHERE id=%s "
                        "AND deleted_at IS NULL FOR UPDATE",
                        (scope.household_space_id,),
                    ).fetchone()
                    is None
                ):
                    return 0
                proposals = _proposals(connection, scope)
                count = 0
                for item in proposals:
                    row = connection.execute(
                        """INSERT INTO private_knowledge_canonical_links
                        (household_space_id,import_run_id,knowledge_coverage_id,knowledge_contract_id,
                         family_member_id,policy_contract_id,rider_id,ledger_version,
                         field_value_conflict,
                         proofs,fingerprint) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (knowledge_coverage_id,fingerprint) DO NOTHING RETURNING id""",
                        (
                            scope.household_space_id,
                            item.import_run_id,
                            item.knowledge_coverage_id,
                            item.knowledge_contract_id,
                            item.family_member_id,
                            item.policy_contract_id,
                            item.rider_id,
                            item.ledger_version,
                            item.field_value_conflict,
                            Jsonb(item.proofs),
                            item.fingerprint,
                        ),
                    ).fetchone()
                    count += row is not None
                return count
        except psycopg.Error, KeyError, ValueError, TypeError:
            raise CanonicalLinkError from None

    def read_current(self, scope: HouseholdScope) -> tuple[CanonicalCoverageLink, ...]:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                return self.read_in_transaction(connection, scope)
        except psycopg.Error, KeyError, ValueError, TypeError:
            raise CanonicalLinkError from None

    @staticmethod
    def read_in_transaction(
        connection: psycopg.Connection[dict[str, Any]], scope: HouseholdScope
    ) -> tuple[CanonicalCoverageLink, ...]:
        current = _proposals(connection, scope)
        if not current:
            return ()
        saved = connection.execute(
            "SELECT knowledge_coverage_id,fingerprint FROM "
            "private_knowledge_canonical_links WHERE household_space_id=%s AND "
            "import_run_id=%s",
            (scope.household_space_id, current[0].import_run_id),
        ).fetchall()
        keys = {(row["knowledge_coverage_id"], row["fingerprint"]) for row in saved}
        return tuple(
            item for item in current if (item.knowledge_coverage_id, item.fingerprint) in keys
        )
