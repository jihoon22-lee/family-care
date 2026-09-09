"""Bounded, immutable local originals for explicitly requested guidance review.

``ReviewSources.to_payload()`` is ``guidance-review-sources-v1``. It returns a
fresh JSON object with scope/event binding, ``index``, ``packets`` (each contains
an unchanged ``SemanticWorkEnvelope`` under ``envelope``), ``manifest`` and
``digest_sha256``. It is local queue input, NOT a provider-safe projection.
Worker minimization and token budgets must run separately before transmission.
Neither a retrieved original nor its retrieval score establishes eligibility.
The digest hashes canonical JSON with the digest_sha256 field omitted.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import date
from typing import Any, Literal
from uuid import UUID

import psycopg

from familycare_api.clauses.errors import RiderClauseLinkInvalid, TermsEditionNotFound
from familycare_api.clauses.links import validate_rider_clause_link
from familycare_api.clauses.repository import RiderClauseLinkRepository
from familycare_api.clauses.source_regions import ClauseSourceSpan
from familycare_api.clauses.source_repository import (
    VerifiedClauseSource,
    read_verified_clause_source,
)
from familycare_api.clauses.terms_change_repository import read_event_terms
from familycare_api.clauses.terms_change_selection import TermsSelectionScope
from familycare_api.common.coverage_identity import CanonicalCoverageRef
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.assistance import normalize_search_tokens
from familycare_api.decisions.domain import MedicalEvent
from familycare_api.decisions.repository import DecisionRepository
from familycare_api.insurance_reconciliation.canonical_repository import (
    CanonicalLinkError,
    CanonicalLinkRepository,
)
from familycare_api.terms_knowledge.core import SemanticKnowledgeError
from familycare_api.terms_knowledge.repository import SemanticSourcePlan, _plan
from familycare_api.terms_knowledge.work_repository import (
    SemanticWorkUnsupported,
    build_work_envelope,
)

SOURCE_REVISION = "guidance-review-sources-v1"
MAX_INDEX_COVERAGES = 128
MAX_SOURCE_PACKETS = 8
MAX_PACKET_BYTES = 128 * 1024
MAX_SOURCE_BYTES = 512 * 1024
MAX_LINKS_PER_COVERAGE = 16
MAX_SOURCE_ATTEMPTS = 32
MAX_MANIFEST_REGION_IDS = 32


class ReviewSourcesInvalid(ValueError):
    def __init__(self) -> None:
        super().__init__("GUIDANCE_REVIEW_SOURCE_SCOPE_INVALID")


class _SourceUnavailable(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class ReviewCoverageSource:
    ref: CanonicalCoverageRef
    contract_label: str
    coverage_label: str
    enrollment_decision: Literal["MATCH", "UNKNOWN"]
    enrollment_authority: str
    source_revision_sha256: str
    native_ref: CanonicalCoverageRef | None = None
    source_document_version_ids: tuple[UUID, ...] = ()
    source_state: Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE"] = "UNAVAILABLE"
    packet_ids: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    linked_clause_count: int = 0
    omitted_clause_count: int = 0


@dataclass(frozen=True, slots=True, repr=False)
class ReviewNativePacket:
    packet_id: str
    coverage_ref: CanonicalCoverageRef
    link_id: UUID
    link_version: int
    clause_id: UUID
    source_assessment_id: UUID
    source_digest_sha256: str
    selection_digest_sha256: str
    retrieval_score: int
    envelope_json: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "coverage_ref": self.coverage_ref.model_dump(mode="json"),
            "link_id": str(self.link_id),
            "link_version": self.link_version,
            "clause_id": str(self.clause_id),
            "source_assessment_id": str(self.source_assessment_id),
            "source_digest_sha256": self.source_digest_sha256,
            "selection_digest_sha256": self.selection_digest_sha256,
            "retrieval_score": self.retrieval_score,
            "envelope": json.loads(self.envelope_json),
        }


@dataclass(frozen=True, slots=True, repr=False)
class ReviewRegionManifest:
    terms_edition_id: UUID
    input_digest_sha256: str
    expected_region_ids: tuple[str, ...]
    supplied_region_ids: tuple[str, ...]
    omitted_region_ids: tuple[str, ...]
    expected_region_count: int
    omitted_region_count: int
    unlisted_region_count: int
    expected_regions_digest_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class ReviewSourceManifest:
    total_coverage_count: int
    indexed_coverage_count: int
    omitted_coverage_count: int
    inventory_digest_sha256: str
    source_index_digest_sha256: str
    regions: tuple[ReviewRegionManifest, ...]
    reason_codes: tuple[str, ...]
    complete: bool


@dataclass(frozen=True, slots=True, repr=False)
class ReviewSources:
    household_space_id: UUID
    family_member_id: UUID
    medical_event_id: UUID
    event_version: int
    event_date: date | None
    index: tuple[ReviewCoverageSource, ...]
    packets: tuple[ReviewNativePacket, ...]
    manifest: ReviewSourceManifest
    digest_sha256: str
    schema_revision: str = SOURCE_REVISION

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_revision": self.schema_revision,
            "household_space_id": str(self.household_space_id),
            "family_member_id": str(self.family_member_id),
            "medical_event_id": str(self.medical_event_id),
            "event_version": self.event_version,
            "event_date": self.event_date.isoformat() if self.event_date else None,
            "index": [
                {
                    **asdict(item),
                    "ref": item.ref.model_dump(mode="json"),
                    "native_ref": item.native_ref.model_dump(mode="json")
                    if item.native_ref
                    else None,
                    "source_document_version_ids": [
                        str(value) for value in item.source_document_version_ids
                    ],
                    "packet_ids": list(item.packet_ids),
                    "reason_codes": list(item.reason_codes),
                }
                for item in self.index
            ],
            "packets": [packet.to_payload() for packet in self.packets],
            "manifest": json.loads(_json(asdict(self.manifest))),
            "digest_sha256": self.digest_sha256,
        }


def _inventory(
    connection: psycopg.Connection[dict[str, Any]], scope: HouseholdScope, event: MedicalEvent
) -> tuple[list[dict[str, Any]], int, str]:
    # The digest/count include every inventory entry, even when the JSON index is
    # capped. No rule publication, local candidate or relevance predicate is used.
    row = connection.execute(
        """
        WITH insured AS (
          SELECT party.policy_contract_id,
            array_agg(DISTINCT party.evidence_id) AS evidence_ids,
            jsonb_agg(jsonb_build_array(party.id,party.version,party.evidence_id,
              party.effective_from,party.effective_to) ORDER BY party.id) AS revisions
          FROM policy_parties party WHERE party.household_space_id=%(household)s
            AND party.family_member_id=%(member)s AND party.deleted_at IS NULL
            AND party.role IN ('primary_insured','additional_insured')
            AND (%(day)s::date IS NULL OR party.effective_from IS NULL
              OR party.effective_from<=%(day)s)
            AND (%(day)s::date IS NULL OR party.effective_to IS NULL
              OR party.effective_to>=%(day)s)
          GROUP BY party.policy_contract_id
        ), inventory AS (
          SELECT 'OPERATIONAL_RIDER'::text AS kind,p.id AS contract_id,r.id AS coverage_id,
            p.product_display AS contract_label,r.display_name AS coverage_label,
            'POLICY_LEDGER'::text AS authority,
            ARRAY[p.source_evidence_id,r.source_evidence_id] AS enrollment_evidence_ids,
            insured.evidence_ids AS subject_evidence_ids,
            jsonb_build_object('policy',jsonb_build_array(p.id,p.version,p.source_evidence_id),
              'rider',jsonb_build_array(r.id,r.version,r.source_evidence_id),
              'parties',insured.revisions) AS revision
          FROM insured JOIN policy_contracts p ON p.id=insured.policy_contract_id
            AND p.household_space_id=%(household)s AND p.deleted_at IS NULL
          JOIN riders r ON r.policy_contract_id=p.id AND r.household_space_id=p.household_space_id
            AND r.deleted_at IS NULL
          UNION ALL
          SELECT 'PRIVATE_KNOWLEDGE_COVERAGE',c.id,v.id,c.product_display,v.display_name,
            CASE WHEN v.enrollment_decision='MATCH' THEN 'CERTIFICATE_SNAPSHOT'
              ELSE 'USER_CONFIRMED_COVERAGE_ENROLLMENT' END,
            '{}'::uuid[],'{}'::uuid[],
            jsonb_build_object('import',run.id,'package',run.package_digest_sha256,
              'contract',c.source_record_digest_sha256,'coverage',v.source_record_digest_sha256,
              'subject',jsonb_build_array(s.id,s.family_member_id,s.binding_decision,
                s.binding_conflict,s.binding_confirmed_at),'enrollment',v.enrollment_decision)
          FROM private_knowledge_import_runs run
          JOIN private_knowledge_subjects s ON s.import_run_id=run.id
            AND s.household_space_id=run.household_space_id
            AND s.family_member_id=%(member)s AND s.binding_decision='MATCH'
            AND NOT s.binding_conflict
          JOIN private_knowledge_contracts c ON c.subject_id=s.id AND c.import_run_id=run.id
            AND c.household_space_id=run.household_space_id AND c.certificate_decision='MATCH'
          JOIN private_knowledge_coverages v ON v.knowledge_contract_id=c.id
            AND v.import_run_id=run.id AND v.household_space_id=run.household_space_id
            AND v.component_classification='BENEFIT_COVERAGE'
          WHERE run.household_space_id=%(household)s AND run.is_current AND run.state='APPLIED'
            AND (v.enrollment_decision='MATCH' OR EXISTS (
              SELECT 1 FROM private_knowledge_coverage_execution_dispositions d
              JOIN private_knowledge_rule_import_runs rules ON rules.id=d.rule_import_run_id
                AND rules.knowledge_import_run_id=run.id AND rules.is_current
                AND rules.state='APPLIED' AND rules.household_space_id=run.household_space_id
              WHERE d.knowledge_coverage_id=v.id AND d.knowledge_import_run_id=run.id
                AND d.household_space_id=run.household_space_id
                AND d.enrollment_authority='USER_CONFIRMED_COVERAGE_ENROLLMENT'
                AND d.enrollment_confirmed_by IS NOT NULL))
        ), limited AS (
          SELECT * FROM inventory ORDER BY kind,contract_id,coverage_id LIMIT %(limit)s
        )
        SELECT (SELECT count(*) FROM inventory) AS count,
          (SELECT encode(sha256(convert_to(coalesce(jsonb_agg(to_jsonb(i)
            ORDER BY kind,contract_id,coverage_id)::text,'[]'),'UTF8')),'hex')
            FROM inventory i) AS digest,
          coalesce((SELECT jsonb_agg(to_jsonb(l) ORDER BY kind,contract_id,coverage_id)
            FROM limited l),'[]'::jsonb) AS entries
        """,
        {
            "household": scope.household_space_id,
            "member": event.family_member_id,
            "day": event.event_date,
            "limit": MAX_INDEX_COVERAGES,
        },
    ).fetchone()
    assert row is not None
    return list(row["entries"]), int(row["count"]), str(row["digest"])


def _inside(span: ClauseSourceSpan, bound: ClauseSourceSpan) -> bool:
    return (
        span.node_id == bound.node_id
        and span.page_number == bound.page_number
        and span.source_layer == bound.source_layer
        and span.bbox == bound.bbox
        and bound.start <= span.start < span.end <= bound.end
        and span.text == bound.text[span.start - bound.start : span.end - bound.start]
    )


def _primary_regions(plan: SemanticSourcePlan, source: VerifiedClauseSource) -> tuple[str, ...]:
    identity = plan.snapshot.source
    if any(
        str(getattr(identity, key)) != str(value)
        for key, value in (
            ("document_version_id", source.document_version_id),
            ("terms_edition_id", source.terms_edition_id),
            ("generation_id", source.generation_id),
            ("content_sha256", source.region.content_sha256),
        )
    ):
        raise SemanticWorkUnsupported
    bounds = (*source.region.body, *source.region.table_context)
    primary = tuple(
        region.region_id
        for region in plan.snapshot.layout.regions
        if region.kind == "article"
        and region.complete
        and region.body_spans
        and all(any(_inside(span, bound) for bound in bounds) for span in region.body_spans)
    )
    if not primary:
        raise SemanticWorkUnsupported
    return primary


def _links(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    entry: ReviewCoverageSource,
    tokens: tuple[str, ...],
) -> list[dict[str, Any]]:
    assert entry.native_ref is not None
    return list(
        connection.execute(
            """
        SELECT l.*,c.version AS clause_version,
          encode(sha256(convert_to(c.normalized_text,'UTF8')),'hex') AS clause_text_digest,
          encode(sha256(convert_to((jsonb_agg(jsonb_build_array(l.id,l.version,c.id,c.version,
            encode(sha256(convert_to(c.normalized_text,'UTF8')),'hex')))
            OVER (ORDER BY l.id ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING))::text,
            'UTF8')),'hex') AS all_links_digest,
          count(*) OVER() AS total_count,
          (SELECT count(*) FROM unnest(%s::text[]) token
            WHERE position(token in lower(concat_ws(' ',%s::text,c.label,c.normalized_title,
              c.normalized_text)))>0)::integer AS retrieval_score
        FROM rider_clause_links l JOIN clauses c ON c.id=l.clause_id
          AND c.terms_edition_id=l.terms_edition_id AND c.household_space_id=l.household_space_id
          AND c.deleted_at IS NULL
        WHERE l.household_space_id=%s AND l.rider_id=%s AND l.deleted_at IS NULL
          AND l.review_state IN ('AI_VERIFIED','USER_CONFIRMED')
        ORDER BY retrieval_score DESC,l.id LIMIT %s
        """,
            (
                list(tokens),
                entry.coverage_label,
                scope.household_space_id,
                entry.native_ref.coverage_id,
                MAX_LINKS_PER_COVERAGE,
            ),
        ).fetchall()
    )


def _native_packet(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    event: MedicalEvent,
    decisions: DecisionRepository,
    ref: CanonicalCoverageRef,
    row: dict[str, Any],
    tokens: tuple[str, ...],
    plans: dict[UUID, SemanticSourcePlan],
) -> ReviewNativePacket | None:
    selection = read_event_terms(
        connection,
        TermsSelectionScope(
            scope.household_space_id,
            ref.contract_id,
            event.family_member_id,
            ref.coverage_id,
            row["clause_id"],
        ),
        event.event_date,
    )
    edition = next((e for e in selection.editions if e.edition_id == row["terms_edition_id"]), None)
    if edition is not None and edition.status == "NO_MATCH":
        return None
    if edition is None or edition.status != "MATCH":
        raise _SourceUnavailable("REVIEW_EVENT_TERMS_UNRESOLVED")
    context = RiderClauseLinkRepository(decisions.database_url)._validation_context(
        connection, scope, row, include_change_gate=False, lock_source=False
    )
    validate_rider_clause_link(
        scope,
        replace(context, program_applicability_verified=True, program_applicability_blocked=False),
    )
    source = read_verified_clause_source(connection, scope.household_space_id, row["clause_id"])
    if source is None:
        raise _SourceUnavailable("REVIEW_NATIVE_SOURCE_UNAVAILABLE")
    if source.terms_edition_id not in plans:
        # Keep only one complete component projection alive while walking the
        # bounded retrieval shortlist; packets retain their own immutable JSON.
        plans.clear()
        plans[source.terms_edition_id] = _plan(connection, scope, source.terms_edition_id)
    plan = plans[source.terms_edition_id]
    envelope = build_work_envelope(plan, _primary_regions(plan, source))
    encoded = _json(envelope.model_dump(mode="json"))
    text = " ".join(c.text for r in envelope.regions for c in r.citations)
    score = len(set(tokens) & set(normalize_search_tokens(text, ())))
    selection_digest = _digest(asdict(selection))
    payload = {
        "ref": ref.model_dump(mode="json"),
        "link": str(row["id"]),
        "link_version": row["version"],
        "source": source.input_digest,
        "selection": selection_digest,
        "envelope": encoded,
    }
    return ReviewNativePacket(
        _digest(payload),
        ref,
        row["id"],
        row["version"],
        source.clause_id,
        source.assessment_id,
        source.input_digest,
        selection_digest,
        score + int(row["retrieval_score"]),
        encoded,
    )


def _regions(
    observed: list[ReviewNativePacket], packets: list[ReviewNativePacket]
) -> tuple[ReviewRegionManifest, ...]:
    grouped: dict[tuple[UUID, str], tuple[set[str], set[str]]] = {}
    retained_ids = {packet.packet_id for packet in packets}
    for packet in observed:
        envelope = json.loads(packet.envelope_json)
        key = UUID(envelope["source"]["terms_edition_id"]), envelope["input_digest"]
        expected, supplied = grouped.setdefault(key, (set(), set()))
        expected.update(envelope["expected_region_ids"])
        if packet.packet_id in retained_ids:
            supplied.update(region["region_id"] for region in envelope["regions"])
    result = []
    for (edition, digest), (expected, supplied) in sorted(grouped.items()):
        omitted = sorted(expected - supplied)
        listed = sorted(expected)[:MAX_MANIFEST_REGION_IDS]
        result.append(
            ReviewRegionManifest(
                edition,
                digest,
                tuple(listed),
                tuple(sorted(supplied)),
                tuple(omitted[:MAX_MANIFEST_REGION_IDS]),
                len(expected),
                len(omitted),
                max(0, len(expected) - len(listed)),
                _digest(sorted(expected)),
            )
        )
    return tuple(result)


def read_review_sources(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    event: MedicalEvent,
    decisions: DecisionRepository,
) -> ReviewSources:
    """Read using the caller's snapshot; never enqueue, publish or call a provider."""
    if event.household_space_id != scope.household_space_id:
        raise ReviewSourcesInvalid
    current = connection.execute(
        "SELECT e.id FROM medical_events e JOIN household_spaces h ON h.id=e.household_space_id "
        "AND h.deleted_at IS NULL JOIN family_members m ON m.id=e.family_member_id "
        "AND m.household_space_id=e.household_space_id AND m.deleted_at IS NULL "
        "WHERE e.id=%s AND e.household_space_id=%s AND e.family_member_id=%s "
        "AND e.version=%s AND e.event_date IS NOT DISTINCT FROM %s::date AND e.deleted_at IS NULL",
        (
            event.id,
            scope.household_space_id,
            event.family_member_id,
            event.version,
            event.event_date,
        ),
    ).fetchone()
    if current is None:
        raise ReviewSourcesInvalid
    rows, total, inventory_digest = _inventory(connection, scope, event)
    evidence_ids = tuple(
        dict.fromkeys(
            UUID(key)
            for row in rows
            for key in (*row["enrollment_evidence_ids"], *row["subject_evidence_ids"])
            if key
        )
    )
    evidence = {e.evidence_id: e for e in decisions._evidence_many(connection, scope, evidence_ids)}
    valid = set(evidence)
    failures: set[str] = set()
    if total > len(rows):
        failures.add("REVIEW_INDEX_LIMIT")
    try:
        with connection.transaction():
            aliases = {
                link.knowledge_coverage_id: link
                for link in CanonicalLinkRepository.read_in_transaction(connection, scope)
                if link.family_member_id == event.family_member_id
            }
    except psycopg.Error, CanonicalLinkError:
        aliases = {}
        failures.add("REVIEW_CANONICAL_SOURCE_UNAVAILABLE")
    index = []
    for row in rows:
        ref = CanonicalCoverageRef(
            kind=row["kind"], contract_id=row["contract_id"], coverage_id=row["coverage_id"]
        )
        native = ref if ref.kind == "OPERATIONAL_RIDER" else None
        if ref.coverage_id in aliases:
            native = aliases[ref.coverage_id].identity().ref
        enrolled = ref.kind == "PRIVATE_KNOWLEDGE_COVERAGE" or (
            all(key and UUID(key) in valid for key in row["enrollment_evidence_ids"])
            and any(UUID(key) in valid for key in row["subject_evidence_ids"] if key)
        )
        reasons = () if enrolled else ("REVIEW_ENROLLMENT_SOURCE_UNAVAILABLE",)
        if native is None:
            reasons = (*reasons, "REVIEW_NATIVE_SOURCE_UNAVAILABLE")
        index.append(
            ReviewCoverageSource(
                ref,
                row["contract_label"],
                row["coverage_label"],
                "MATCH" if enrolled else "UNKNOWN",
                row["authority"],
                _digest(row["revision"]),
                native,
                source_document_version_ids=tuple(
                    sorted(
                        {
                            evidence[UUID(key)].document_version_id
                            for key in (
                                *row["enrollment_evidence_ids"],
                                *row["subject_evidence_ids"],
                            )
                            if key and UUID(key) in evidence
                        }
                    )
                ),
                reason_codes=reasons,
            )
        )
    tokens = normalize_search_tokens(
        event.situation,
        tuple(
            str(fact.value)
            for fact in event.facts.values()
            if fact.confirmation == "user" and fact.value is not None
        ),
    )
    work: list[tuple[CanonicalCoverageRef, dict[str, Any]]] = []
    source_index = []
    by_native: dict[UUID, list[int]] = defaultdict(list)
    counts_by_native: dict[UUID, tuple[int, int]] = {}
    reasons_by_native: dict[UUID, set[str]] = defaultdict(set)
    for position, entry in enumerate(index):
        if entry.native_ref is None or entry.enrollment_decision != "MATCH":
            continue
        key = entry.native_ref.coverage_id
        by_native[key].append(position)
        if len(by_native[key]) > 1:
            continue
        links = _links(connection, scope, entry, tokens)
        count = int(links[0]["total_count"]) if links else 0
        counts_by_native[key] = count, max(0, count - len(links))
        index[position] = replace(
            entry, linked_clause_count=count, omitted_clause_count=max(0, count - len(links))
        )
        source_index.append({"coverage": str(key), "count": count, "links": links})
        if not links:
            reasons_by_native[key].add("REVIEW_NATIVE_SOURCE_UNAVAILABLE")
        if count > len(links):
            reasons_by_native[key].add("REVIEW_LINK_LIMIT")
        work.extend((entry.native_ref, row) for row in links)
    work.sort(
        key=lambda pair: (-pair[1]["retrieval_score"], str(pair[0].coverage_id), str(pair[1]["id"]))
    )
    available = []
    observed = []
    plans: dict[UUID, SemanticSourcePlan] = {}
    for ordinal, (ref, row) in enumerate(work):
        if ordinal >= MAX_SOURCE_ATTEMPTS:
            reasons_by_native[ref.coverage_id].add("REVIEW_SOURCE_ATTEMPT_LIMIT")
            continue
        try:
            with connection.transaction():
                packet = _native_packet(
                    connection, scope, event, decisions, ref, row, tokens, plans
                )
            if packet is None:
                continue
            observed.append(packet)
            if len(_json(packet.to_payload()).encode()) > MAX_PACKET_BYTES:
                reasons_by_native[ref.coverage_id].add("REVIEW_PACKET_BYTE_LIMIT")
            else:
                available.append(packet)
        except SemanticWorkUnsupported as error:
            reasons_by_native[ref.coverage_id].add(error.code)
        except _SourceUnavailable as error:
            reasons_by_native[ref.coverage_id].add(error.code)
        except (
            psycopg.Error,
            SemanticKnowledgeError,
            RiderClauseLinkInvalid,
            TermsEditionNotFound,
            ValueError,
        ):
            reasons_by_native[ref.coverage_id].add("REVIEW_NATIVE_SOURCE_UNAVAILABLE")
    available.sort(key=lambda packet: (-packet.retrieval_score, packet.packet_id))
    packets: list[ReviewNativePacket] = []
    for packet in available:
        if len(packets) >= MAX_SOURCE_PACKETS:
            reasons_by_native[packet.coverage_ref.coverage_id].add("REVIEW_PACKET_LIMIT")
            continue
        # Leave bounded space for the index and manifests, then check the complete
        # serialized result below. Whole packets are removed when necessary.
        packets.append(packet)

    def result_for(selected: list[ReviewNativePacket]) -> ReviewSources:
        entries = []
        all_reasons = set(failures)
        for entry in index:
            key = entry.native_ref.coverage_id if entry.native_ref else None
            retained = tuple(p.packet_id for p in selected if p.coverage_ref.coverage_id == key)
            entry_reasons = set(entry.reason_codes) | (
                reasons_by_native.get(key, set()) if key else set()
            )
            if not retained and not entry_reasons:
                entry_reasons.add("REVIEW_NATIVE_SOURCE_UNAVAILABLE")
            all_reasons.update(entry_reasons)
            count, omitted = counts_by_native.get(key, (0, 0)) if key else (0, 0)
            entries.append(
                replace(
                    entry,
                    packet_ids=retained,
                    reason_codes=tuple(sorted(entry_reasons)),
                    linked_clause_count=count,
                    omitted_clause_count=omitted,
                    source_state="PARTIAL"
                    if retained and entry_reasons
                    else "AVAILABLE"
                    if retained
                    else "UNAVAILABLE",
                )
            )
        regions = _regions(observed, selected)
        if any(region.unlisted_region_count for region in regions):
            all_reasons.add("REVIEW_REGION_MANIFEST_LIMIT")
        manifest = ReviewSourceManifest(
            total,
            len(entries),
            total - len(entries),
            inventory_digest,
            _digest(source_index),
            regions,
            tuple(sorted(all_reasons)),
            not all_reasons and not any(region.omitted_region_count for region in regions),
        )
        result = ReviewSources(
            scope.household_space_id,
            event.family_member_id,
            event.id,
            event.version,
            event.event_date,
            tuple(entries),
            tuple(selected),
            manifest,
            "",
        )
        payload = result.to_payload()
        payload.pop("digest_sha256")
        return replace(result, digest_sha256=_digest(payload))

    result = result_for(packets)
    while len(_json(result.to_payload()).encode()) > MAX_SOURCE_BYTES and packets:
        removed = packets.pop()
        reasons_by_native[removed.coverage_ref.coverage_id].add("REVIEW_TOTAL_BYTE_LIMIT")
        result = result_for(packets)
    if len(_json(result.to_payload()).encode()) > MAX_SOURCE_BYTES:
        # Index labels have storage bounds, but keep this final guard independent
        # of those contracts. Counts and the full inventory digest survive.
        failures.add("REVIEW_TOTAL_BYTE_LIMIT")
        while index and len(_json(result.to_payload()).encode()) > MAX_SOURCE_BYTES:
            index.pop()
            result = result_for(packets)
    return result
