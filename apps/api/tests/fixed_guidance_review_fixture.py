"""Retain frozen synthetic scenarios at the production local/review database boundary.

This adapter owns an additive, case-scoped fixture, never a database reset. It
does not read expected labels, call a provider, or edit an existing review job.
The companion source prose retains unsupported formulas without declaring them
executable. Generic ledger amounts deliberately do not impersonate independently
published certificate amount evidence. Daily and ratio sources use the normal range
grounding and enrollment publication pipeline to retain that independent proof.
An explicit eligible-cost input is registered through receipt CRUD before the
first local answer; a legacy event-fact number never substitutes for that source.

Companion conventions, not additions to the frozen benchmark contract: event.kind
is encoded without changing its value in the wholly synthetic taxonomy
synthetic-event-kind/v1. Synthetic-credit inputs are represented one-for-one as
wholly invented whole KRW amounts, with no exchange-rate conversion. This is a
new companion source convention, not the earlier TST evaluation input. Whole
KRW amounts use half-up rounding. The exact fixed
inputs have integral supported fixed/daily results, so that rounding changes no
value; the tests prove this without consulting embedded expected_amount labels.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid5

import psycopg
from familycare_api.clauses.component_editions import ComponentTermsProjector
from familycare_api.clauses.repository import RiderClauseLinkRepository
from familycare_api.clauses.source_repository import ClauseSourceProjector
from familycare_api.clauses.terms_applicability_repository import TermsApplicabilityProjector
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.calculation_repository import CalculationRepository
from familycare_api.decisions.calculation_schemas import ReceiptLineCreateRequest
from familycare_api.decisions.calculation_service import CalculationService
from familycare_api.decisions.repository import DecisionRepository
from familycare_api.decisions.schemas import MedicalEventUpdateRequest, StructuredFactInput
from familycare_api.decisions.service import DecisionService
from familycare_api.guidance_review.sources import read_review_sources
from familycare_api.insurance_documents.metadata_publication import DocumentMetadataProjector
from familycare_api.insurance_documents.repository import InsuranceDocumentRepository
from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
from familycare_api.terms_knowledge.projector import TermsSemanticProjector
from familycare_api.terms_knowledge.source_meaning import observe_statement
from familycare_worker.ai.range_structurer import PolicyRangeBatch
from familycare_worker.ai.schemas import (
    CandidateField,
    CandidatePipelineResult,
    PolicyCandidate,
    StructurerCandidate,
)
from familycare_worker.document_metadata_repository import DocumentMetadataRunner
from familycare_worker.policy_jobs import PolicyStructuringJobQueue
from familycare_worker.policy_range_repository import PolicyRangeRepository
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests.test_clause_change_integration import _native_link
from apps.api.tests.test_clause_source_publication import _clause
from apps.api.tests.test_decision_integration import _insert_candidate, _insert_evidence
from apps.api.tests.test_terms_change_integration import _add_document, _psycopg_url
from scripts.claim_guidance_benchmark import BenchmarkCase
from scripts.fixed_review_fixture import FixedReviewFixture
from scripts.integration_test_database import configure_integration_test_database
from scripts.run_claim_guidance_benchmark import _FIELD_PATHS, _value
from workers.analyzer.tests.test_document_text_lines import _words
from workers.analyzer.tests.test_policy_range_repository import _no_facts

REVISION = "fixed-review-database-fixture-v5-krw-source-receipt"
EVENT_KIND_SYSTEM = "synthetic-event-kind"
EVENT_KIND_VERSION = "v1"
_NAMESPACE = UUID("00000000-0000-4000-8000-000000009094")


def _id(value: str) -> UUID:
    return uuid5(_NAMESPACE, value)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _source_parameters(case: BenchmarkCase) -> dict[str, Any]:
    """Remove only the embedded amount oracle; keep every actual scenario input."""
    parameters = json.loads(_json(case.scenario_parameters))
    for coverage in parameters["coverages"]:
        coverage.get("calculation", {}).pop("expected_amount", None)
    return parameters


def _contract_key(case: BenchmarkCase, raw: Mapping[str, Any]) -> str:
    # contract_group is the train/holdout leakage group. Explicit identities
    # remain authoritative; implicit contracts separate incompatible subjects
    # because native insured parties are a contract-level relationship.
    return str(raw.get("contract_identity") or f"{case.contract_group}:{raw['subject']}")


def _situation(facts: Mapping[str, Any]) -> str:
    sentences = ["Synthetic structured benchmark event."]
    if "event.performed" in facts:
        sentences.append("수술받았습니다." if facts["event.performed"] else "수술받지 않았습니다.")
    if facts.get("event.planned") is True:
        sentences.append("수술 예정입니다.")
    if "event.admitted" in facts:
        sentences.append("입원했습니다." if facts["event.admitted"] else "입원하지 않았습니다.")
    if facts.get("event.confirmed") is True:
        sentences.append("진단받았습니다.")
    return " ".join(sentences)


def _source_body(raw: Mapping[str, Any]) -> str:
    if raw["event_field"] != "event.kind":
        raise ValueError("FIXED_REVIEW_EVENT_KIND_SOURCE_REQUIRED")
    lines = [
        f"Medical event classification uses {EVENT_KIND_SYSTEM} "
        f"version {EVENT_KIND_VERSION}: {raw['event_value']}.",
    ]
    if raw["event_value"] in {"surgery", "admission", "outpatient"}:
        lines.append(f"The treatment kind must be {raw['event_value']}.")
    if "required_field" in raw:
        field, value = raw["required_field"], raw["required_value"]
        if field == "event.first_claim" and value is True:
            lines.append("Payment requires fewer than 1 prior occurrences.")
        elif field == "event.performed" and value is True:
            lines.append("Surgery must have been performed.")
        elif field == "event.confirmed" and value is True:
            lines.append("Diagnosis must be confirmed.")
        elif field == "event.admitted" and value is True:
            lines.append("Admission is required.")
        else:
            # Keep unsupported mandatory predicates inside the original root.
            # Their omission must prevent source-verified execution of that root.
            lines.append(f"Eligibility also requires {field} equals {_json(value)}.")
    spec = raw.get("calculation", {})
    kind = spec.get("kind")
    if kind == "fixed":
        lines.append(
            f"The benefit is KRW {spec['amount']}, rounded half up to whole currency units."
        )
    elif kind == "daily":
        lines.extend(
            (
                "For each payable admission day, pay the insured amount in KRW; "
                "multiply first, then round the total half up to whole currency units.",
                f"Exclude the first {spec['deduct_days']} admission days.",
                f"The maximum is {spec['cap_days']} payable days.",
            )
        )
    elif kind == "ratio":
        lines.append(
            f"Pay {spec['rate']} of the insured amount in KRW, "
            "rounded half up to whole currency units."
        )
        if "reduction" in spec:
            # Preserve the event-dependent branch instead of pre-applying its
            # factor to the insured amount or the unconditional payment rate.
            lines.append(
                "When the event reduction condition is true, multiply the gross benefit by "
                f"{spec['reduction']}; otherwise keep the gross benefit, "
                "before deduction, amount cap and rounding."
            )
        if "cap" in spec:
            lines.append(f"The maximum benefit is KRW {spec['cap']}.")
    elif kind == "indemnity" and spec.get("cost_field") == "event.eligible_cost":
        lines.extend(
            (
                "Reimburse the covered receipt amount in KRW, rounded half up "
                "to whole currency units.",
                f"Deduct KRW {spec['deductible']} from the gross benefit before applying "
                "the amount cap and rounding; floor at zero.",
            )
        )
    elif spec:
        lines.append(f"Synthetic calculation specification: {_json(spec)}.")
    return "\n".join(lines)


def _insured_amount(raw: Mapping[str, Any]) -> str:
    spec = raw.get("calculation", {})
    key = {"fixed": "amount", "daily": "rate", "ratio": "base"}.get(spec.get("kind"))
    return str(spec[key]) if key is not None else "1"


def exact_companion_amount(parameters: Mapping[str, Any], raw: Mapping[str, Any]) -> Decimal | None:
    """Arithmetic witness for the fixed input only; never an insurance outcome oracle."""
    spec = raw.get("calculation", {})
    kind = spec.get("kind")
    if kind == "fixed":
        return Decimal(str(spec["amount"]))
    if kind == "daily":
        days = parameters["event_facts"].get(spec["days_field"])
        if days is None:
            return None
        payable = min(max(days - spec["deduct_days"], 0), spec["cap_days"])
        return Decimal(str(spec["rate"])) * payable
    if kind == "ratio":
        value = Decimal(str(spec["base"])) * Decimal(str(spec["rate"]))
        if parameters["event_facts"].get("event.reduction_applies") is True:
            value *= Decimal(str(spec["reduction"]))
        return min(value, Decimal(str(spec["cap"]))) if "cap" in spec else value
    return None


def reference_review_graph(packet: Mapping[str, Any]) -> dict[str, Any] | None:
    """Build a good-faith graph solely from the actual supplied original statements.

    It works with native packets and minimized model packets. Unsupported primary
    bodies are explicitly left unreviewed; no expected case labels enter here.
    """
    envelope = packet["envelope"]
    expected = envelope.get(
        "expected_region_ids", [region["region_id"] for region in envelope["regions"]]
    )
    primary = set(envelope["primary_region_ids"])
    regions = [region for region in envelope["regions"] if region["region_id"] in primary]
    nodes, citations, edges, roots = [], [], [], []
    consumed = []
    for region in regions:
        if not region["complete"]:
            return None
        body = [item for item in region["citations"] if item["text"].strip() != region["label"]]
        meanings = [observe_statement(item["text"]) for item in body]
        if not body or any(meaning is None for meaning in meanings):
            return None
        planned = []
        for citation, meaning in zip(body, meanings, strict=True):
            planned.append(
                {
                    "node_id": f"synthetic-reference-node-{len(nodes) + len(planned)}",
                    "source_id": envelope["source"]["source_id"],
                    "statement": citation["text"],
                    "region_ids": [region["region_id"]],
                    "citation_ids": [citation["citation_id"]],
                    "payload": meaning,
                }
            )
        planned.sort(key=lambda node: node["payload"]["kind"] != "calculation")
        root = planned[0]["node_id"]
        roots.append(root)
        edges.extend(
            {"from_node_id": root, "to_node_id": node["node_id"], "relation": "DEPENDS_ON"}
            for node in planned[1:]
        )
        nodes.extend(planned)
        citations.extend(dict(item) for item in body)
        consumed.append(region["region_id"])
    if not roots:
        return None
    return {
        "schema_version": "1",
        "schema_revision": "terms-semantic-v1",
        "prompt_revision": "synthetic-reference-v1",
        "model_revision": "synthetic-reference-v1",
        "sources": [dict(envelope["source"])],
        "nodes": nodes,
        "citations": citations,
        "edges": edges,
        "roots": roots,
        "processing": {
            "expected_region_ids": list(expected),
            "consumed_region_ids": consumed,
            "unresolved_region_ids": [key for key in expected if key not in consumed],
        },
    }


def reference_review_proposals(model_input: Mapping[str, Any]) -> dict[str, Any]:
    """One bounded good-faith model response; other packets remain explicitly unreviewed."""
    packets = model_input["source_packets"]
    selected = next(
        (
            (packet, graph)
            for packet in packets
            if (graph := reference_review_graph(packet)) is not None
        ),
        None,
    )
    if selected is None:
        raise ValueError("FIXED_REVIEW_NO_REPRESENTABLE_SOURCE_PACKET")
    packet, graph = selected
    return {
        "schema_revision": "guidance-review-proposals-v1",
        "reviewed_packet_aliases": [packet["packet_alias"]],
        "unreviewed_packet_aliases": [
            item["packet_alias"]
            for item in packets
            if item["packet_alias"] != packet["packet_alias"]
        ],
        "suggestions": [
            {
                "packet_alias": packet["packet_alias"],
                "kind": "AGREEMENT",
                "graph": graph,
                "affected_fact_paths": ["MedicalEvent.classification"],
                "proposed_amount": None,
            }
        ],
    }


def _bootstrap(url: str, case: BenchmarkCase, fingerprint: str) -> Any:
    household = _id(f"{case.case_id}:household")
    actor = _id(f"{case.case_id}:actor")
    member = _id(f"{case.case_id}:member")
    other = _id(f"{case.case_id}:other-member")
    batch = _id(f"{case.case_id}:batch")
    anchor = _id(f"{case.case_id}:batch-anchor")
    other_batch = _id(f"{case.case_id}:other-batch")
    other_anchor = _id(f"{case.case_id}:other-batch-anchor")
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        connection.execute(
            "INSERT INTO household_spaces(id,space_key,display_name) VALUES(%s,%s,%s)",
            (household, case.case_id, f"{REVISION}:PREPARING:{fingerprint}"),
        )
        connection.execute(
            "INSERT INTO app_users(id,household_space_id,username,display_name,password_hash) "
            "VALUES(%s,%s,%s,'Admin A','$argon2id$synthetic')",
            (actor, household, f"{case.case_id}-admin"),
        )
        for identity, label, alias in (
            (member, "Family Member A", "member-a"),
            (other, "Family Member B", "member-b"),
        ):
            connection.execute(
                "INSERT INTO family_members(id,household_space_id,display_name,internal_alias) "
                "VALUES(%s,%s,%s,%s)",
                (identity, household, label, alias),
            )
        connection.execute(
            "INSERT INTO document_batches(id,household_space_id,family_member_id,created_by,state) "
            "VALUES(%s,%s,%s,%s,'created')",
            (batch, household, member, actor),
        )
        # A synthetic anchor gives the existing retained-source helper its batch.
        connection.execute(
            "INSERT INTO document_batch_items(id,batch_id,source_id,source_key,display_label,"
            "document_kind,state) VALUES(%s,%s,%s,%s,'Synthetic Fixture Anchor','policy','queued')",
            (anchor, batch, _digest([case.case_id, "anchor"]), f"synthetic/{anchor}.pdf"),
        )
        connection.execute(
            "INSERT INTO document_batches(id,household_space_id,family_member_id,created_by,state) "
            "VALUES(%s,%s,%s,%s,'created')",
            (other_batch, household, other, actor),
        )
        connection.execute(
            "INSERT INTO document_batch_items(id,batch_id,source_id,source_key,display_label,"
            "document_kind,state) VALUES(%s,%s,%s,%s,'Synthetic Fixture Anchor','policy','queued')",
            (
                other_anchor,
                other_batch,
                _digest([case.case_id, "other-anchor"]),
                f"synthetic/{other_anchor}.pdf",
            ),
        )
    return SimpleNamespace(
        household_space_id=household,
        family_member_id=member,
        other_member_id=other,
        actor_id=actor,
        batch_item_id=anchor,
        other_batch_item_id=other_anchor,
    )


def _published_amount_policy(
    url: str, job: Any, source: Mapping[str, Any], rows: list[dict[str, Any]], lines: list[str]
) -> UUID:
    """Replay synthetic field proposals; only production grounding grants amount authority."""
    if any(raw["status"] != "unknown" for raw in rows):
        raise ValueError("FIXED_REVIEW_AMOUNT_STATUS_SOURCE_UNSUPPORTED")
    worker = "synthetic-fixed-amount-worker"
    with psycopg.connect(_psycopg_url(url)) as connection:
        # The original fixture retains IR only. Preserve the same invented input
        # in its extraction layer so the normal range loader can reconstruct it.
        for block in _words(lines):
            connection.execute(
                "INSERT INTO extraction_blocks(page_id,text,bbox,reading_order) "
                "SELECT id,%s,%s,%s FROM extraction_pages WHERE extraction_id=%s",
                (block["text"], Jsonb(block["bbox"]), block["reading_order"], source["extraction"]),
            )
        queued = connection.execute(
            "INSERT INTO policy_structuring_jobs(household_space_id,family_member_id,"
            "batch_item_id,document_version_id,extraction_id,pipeline_version) "
            "VALUES(%s,%s,%s,%s,%s,'synthetic-fixed-amount-v1') RETURNING id",
            (
                job.household_space_id,
                job.family_member_id,
                source["item"],
                source["version"],
                source["extraction"],
            ),
        ).fetchone()[0]
    claim = PolicyStructuringJobQueue(url).claim_next_job(worker)
    if claim is None or claim.id != queued:
        raise ValueError("FIXED_REVIEW_POLICY_JOB_SCOPE_MISMATCH")
    repository = PolicyRangeRepository(url)
    work = repository.next(claim, worker, sensitive_terms=())
    if work is None:
        raise ValueError("FIXED_REVIEW_POLICY_SOURCE_RANGE_UNSUPPORTED")
    evidence_by_text = {item.text: item for item in work.envelope.evidence if item.primary}

    def proposal(key: str, kind: str, values: Mapping[str, tuple[Any, str]]) -> StructurerCandidate:
        return StructurerCandidate.model_validate(
            {
                "schema_version": "1",
                "candidate_id": _id(f"{queued}:{key}"),
                "candidate_kind": kind,
                "fields": tuple(
                    CandidateField(
                        field_id=field,
                        value=value,
                        evidence_ids=(evidence_by_text[line].evidence_id,),
                    )
                    for field, (value, line) in values.items()
                ),
            }
        )

    candidates = [
        proposal(
            "policy",
            "policy_contract",
            {
                "insurer": ("Sample Insurer", "보험사: Sample Insurer"),
                "product_name": ("Sample Plan", "상품명: Sample Plan"),
                "contract_start": ("2026-01-01", "contract start: 2026-01-01"),
                "contract_end": ("2026-12-31", "contract end: 2026-12-31"),
            },
        )
    ]
    for raw in rows:
        if raw["enrollment"] != "confirmed":
            continue
        line = next(
            line for line in lines if line.startswith(raw["_fixture_label"] + " enrollment:")
        )
        values = {
            "rider_name": (raw["_fixture_label"], line),
            "rider_key": (raw["_fixture_label"].casefold().replace(" ", "-"), line),
            "benefit_type": (raw["benefit_type"].lower(), line),
        }
        if raw.get("calculation", {}).get("kind") in {"fixed", "daily", "ratio"}:
            values.update(sum_assured=(int(_insured_amount(raw)), line), currency=("KRW", line))
        candidates.append(proposal(raw["coverage_key"], "rider", values))
    assigned = defaultdict(set)
    for candidate in candidates:
        for field in candidate.fields:
            for evidence in field.evidence_ids:
                assigned[evidence].add(candidate.candidate_id)
    primary = dict(
        zip(work.envelope.primary_chunk_ids, work.envelope.primary_evidence_ids, strict=True)
    )
    batch = PolicyRangeBatch(
        schema_version="3",
        candidates=tuple(candidates),
        ranges=tuple(
            disposition.model_copy(
                update={
                    "outcome": "CANDIDATES",
                    "candidate_ids": tuple(
                        sorted(assigned[primary[disposition.chunk_id]], key=str)
                    ),
                }
            )
            if primary[disposition.chunk_id] in assigned
            else disposition
            for disposition in _no_facts(work).ranges
        ),
    )
    result = CandidatePipelineResult(
        classification="SUCCESS",
        candidates=tuple(
            PolicyCandidate(
                candidate_id=candidate.candidate_id,
                candidate_kind=candidate.candidate_kind,
                fields=candidate.fields,
                status="AI_VERIFIED",
                issue_codes=(),
                provider_request_ids=("synthetic-fixture-structure", "synthetic-fixture-verify"),
            )
            for candidate in candidates
        ),
    )
    repository.save(claim, worker, work, batch, result)
    if RangeEnrollmentProjector(url).project_pending() != len(candidates):
        raise ValueError("FIXED_REVIEW_POLICY_FIELDS_NOT_PUBLISHED")
    # Range preparation creates a constructor-consistent generation from the
    # retained extraction; publish its metadata through the same normal path.
    if not DocumentMetadataRunner(url).run_once(worker):
        raise ValueError("FIXED_REVIEW_POLICY_METADATA_UNAVAILABLE")
    DocumentMetadataProjector(url).project_pending()
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        publications = connection.execute(
            "SELECT p.policy_contract_id,p.rider_id,p.field_values "
            "FROM range_enrollment_publications p JOIN analysis_candidate_versions c "
            "ON c.id=p.source_candidate_version_id WHERE c.structuring_job_id=%s",
            (queued,),
        ).fetchall()
    policies = {row["policy_contract_id"] for row in publications}
    if len(policies) != 1:
        raise ValueError("FIXED_REVIEW_POLICY_IDENTITY_AMBIGUOUS")
    for raw in rows:
        if raw["enrollment"] == "confirmed":
            matches = [
                row["rider_id"]
                for row in publications
                if row["field_values"].get("rider_name") == raw["_fixture_label"]
            ]
            if len(matches) != 1 or matches[0] is None:
                raise ValueError("FIXED_REVIEW_RIDER_IDENTITY_AMBIGUOUS")
            raw["_fixture_rider_id"] = matches[0]
    return policies.pop()


def _policy(url: str, case: BenchmarkCase, job: Any, key: str, rows: list[dict[str, Any]]) -> UUID:
    policy = _id(f"{case.case_id}:contract:{key}")
    subject = rows[0]["subject"]
    name = "Family Member A" if subject == "same_member" else "Family Member B"
    lines = [
        "보험증권",
        "보험사: Sample Insurer",
        "상품명: Sample Plan",
        "상품코드: SAMPLE-P",
        f"적용약관코드: TERMS-{policy.hex}",
        f"적용판본코드: EDITION-{policy.hex}",
        f"증권번호: synthetic-policy-{policy.hex[:16]}",
        f"피보험자: {name}",
        "보험기간: 2026-01-01 ~ 2026-12-31",
    ]
    amount_proof = any(raw.get("calculation", {}).get("kind") in {"daily", "ratio"} for raw in rows)
    if amount_proof:
        lines.extend(("contract start: 2026-01-01", "contract end: 2026-12-31", "가입금액"))
    for raw in rows:
        amount = _insured_amount(raw)
        lines.append(
            f"{raw['_fixture_label']} enrollment: {raw['enrollment']}; "
            f"sum assured: {amount} KRW; source status: {raw['status']}."
            + (f" benefit type: {raw['benefit_type'].lower()}." if amount_proof else "")
        )
    digest = _digest(lines)
    _add_document(url, job, "\n".join(lines), kind="policy", digest=digest)
    for raw in rows:
        for copy_number in range(1, raw["source_count"]):
            repeated = [*lines[:9], next(line for line in lines if raw["_fixture_label"] in line)]
            duplicate_text = "\n".join([*repeated, f"Synthetic source copy {copy_number + 1}."])
            _add_document(url, job, duplicate_text, kind="policy", digest=_digest(duplicate_text))
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        source = connection.execute(
            "SELECT v.id AS version,x.id AS extraction,i.id AS item FROM document_versions v "
            "JOIN extractions x ON x.document_version_id=v.id AND x.status='succeeded' "
            "JOIN document_batch_items i ON i.processed_document_version_id=v.id "
            "AND i.document_id=v.document_id AND i.state='succeeded' "
            "JOIN document_batches b ON b.id=i.batch_id "
            "WHERE v.content_sha256=%s AND b.household_space_id=%s AND b.family_member_id=%s "
            "AND b.id=(SELECT batch_id FROM document_batch_items WHERE id=%s)",
            (digest, job.household_space_id, job.family_member_id, job.batch_item_id),
        ).fetchone()
        if source is None:
            raise ValueError("FIXED_REVIEW_POLICY_SOURCE_NOT_RETAINED")
        evidence = _id(f"{case.case_id}:contract-evidence:{key}")
        _insert_evidence(
            connection,
            evidence_id=evidence,
            household_id=job.household_space_id,
            document_version_id=source["version"],
            extraction_id=source["extraction"],
            content_sha256=digest,
            page=1,
        )
        connection.execute("UPDATE evidence SET x0=10,y0=10,x1=500,y1=700 WHERE id=%s", (evidence,))
    if amount_proof:
        for raw in rows:
            raw["_fixture_terms_code"] = policy.hex
        return _published_amount_policy(url, job, source, rows, lines)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        connection.execute(
            "INSERT INTO policy_contracts(id,household_space_id,source_document_version_id,"
            "source_evidence_id,insurer_display,insurer_key,product_display,product_key,"
            "contract_date,coverage_start_date,coverage_end_date,status) "
            "VALUES(%s,%s,%s,%s,'Sample Insurer','sample-insurer','Sample Plan',%s,"
            "'2026-01-01','2026-01-01','2026-12-31','unknown')",
            (policy, job.household_space_id, source["version"], evidence, str(policy)),
        )
        connection.execute(
            "INSERT INTO policy_parties(id,household_space_id,policy_contract_id,family_member_id,"
            "role,effective_from,effective_to,evidence_id) "
            "VALUES(%s,%s,%s,%s,'primary_insured','2026-01-01','2026-12-31',%s)",
            (
                _id(f"{case.case_id}:party:{key}"),
                job.household_space_id,
                policy,
                job.family_member_id if subject == "same_member" else job.other_member_id,
                evidence,
            ),
        )
        if any(raw["status"] in {"active", "active_at_event"} for raw in rows):
            connection.execute(
                "INSERT INTO policy_status_snapshots(id,household_space_id,policy_contract_id,"
                "status,effective_at,evidence_id) "
                "VALUES(%s,%s,%s,'active','2026-01-01T00:00:00Z',%s)",
                (_id(f"{policy}:active"), job.household_space_id, policy, evidence),
            )
        for raw in rows:
            if raw["enrollment"] != "confirmed":
                continue
            rider = _id(raw["coverage_key"])
            current = (
                "inactive" if raw["status"] in {"active_at_event", "terminated"} else "unknown"
            )
            connection.execute(
                "INSERT INTO riders(id,household_space_id,policy_contract_id,source_evidence_id,"
                "display_name,normalized_key,benefit_type,insured_amount,currency,"
                "coverage_start_date,coverage_end_date,status) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'KRW','2026-01-01','2026-12-31',%s)",
                (
                    rider,
                    job.household_space_id,
                    policy,
                    evidence,
                    raw["_fixture_label"],
                    raw["_fixture_label"].casefold().replace(" ", "-"),
                    raw["benefit_type"].lower(),
                    _insured_amount(raw),
                    current,
                ),
            )
            if raw["status"] != "unknown":
                statuses = (
                    ("active", "inactive")
                    if raw["status"] == "conflicting"
                    else ("inactive",)
                    if raw["status"] == "terminated"
                    else ("active",)
                )
                for status in statuses:
                    connection.execute(
                        "INSERT INTO policy_status_snapshots(id,household_space_id,"
                        "rider_id,status,effective_at,evidence_id) "
                        "VALUES(%s,%s,%s,%s,'2026-01-01T00:00:00Z',%s)",
                        (_id(f"{rider}:{status}"), job.household_space_id, rider, status, evidence),
                    )
    return policy


def _rules(url: str, job: Any, raw: dict[str, Any], link: UUID, clause: UUID) -> None:
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        evidence = connection.execute(
            "SELECT e.* FROM evidence e JOIN clause_evidence ce ON ce.evidence_id=e.id "
            "WHERE ce.clause_id=%s ORDER BY e.id LIMIT 1",
            (clause,),
        ).fetchone()
        predicates = [(raw["event_field"], raw["event_value"])]
        if raw["event_field"] == "event.kind" and raw["event_value"] in {
            "surgery",
            "admission",
            "outpatient",
        }:
            predicates.append(("MedicalEvent.treatment_kind", raw["event_value"]))
        if "required_field" in raw:
            predicates.append((raw["required_field"], raw["required_value"]))
        for index, (field, value) in enumerate(predicates):
            rule = _id(f"{link}:rule:{index}")
            version = _id(f"{rule}:version")
            candidate = _id(f"{rule}:candidate")
            _insert_candidate(
                connection,
                candidate_id=candidate,
                review_item_id=_id(f"{rule}:review"),
                household_id=job.household_space_id,
                candidate_kind="coverage_rule",
                aggregate_id=rule,
                schema_version="coverage-rule-v1",
                field_id="rule_kind",
                evidence=((evidence["document_version_id"], evidence["id"], 1),),
            )
            path = _FIELD_PATHS.get(field, field)
            document = {
                "schema_version": "coverage-rule-v1",
                "rule_kind": "eligibility",
                "required": True,
                "input_field_paths": [path],
                "expression": {"op": "equals", "field": path, "value": _value(field, value)},
                "result_reason_code": "SYNTHETIC_CONDITION_MATCH",
                "evidence_ids": [str(evidence["id"])],
            }
            connection.execute(
                "INSERT INTO coverage_rules(id,household_space_id,rider_clause_link_id,rule_key,"
                "current_status,version) VALUES(%s,%s,%s,%s,'published',1)",
                (rule, job.household_space_id, link, f"synthetic-{rule}"),
            )
            connection.execute(
                "INSERT INTO coverage_rule_versions(id,coverage_rule_id,candidate_version_id,"
                "version_number,schema_version,rule_kind,required,input_field_paths,expression_json,"
                "result_reason_code,review_state,executable,generator_version,verifier_version,"
                "published_at) VALUES(%s,%s,%s,1,'coverage-rule-v1','eligibility',true,%s,%s,"
                "'SYNTHETIC_CONDITION_MATCH','AI_VERIFIED',true,'synthetic-fixed-fixture-v1',"
                "'synthetic-fixed-fixture-v1',clock_timestamp())",
                (version, rule, candidate, Jsonb([path]), Jsonb(document)),
            )
            connection.execute(
                "INSERT INTO coverage_rule_evidence VALUES(%s,%s)",
                (version, evidence["id"]),
            )


def _refresh_terms(url: str, job: Any, policy: UUID) -> None:
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        TermsApplicabilityProjector(url)._refresh(
            connection,
            {
                "id": policy,
                "household_space_id": job.household_space_id,
                "family_member_id": job.family_member_id,
            },
        )


def _terms(url: str, job: Any, policy: UUID, raw: dict[str, Any]) -> None:
    body = _source_body(raw)
    code = raw.get("_fixture_terms_code", policy.hex)
    text = (
        "보험약관\n보험사: Sample Insurer\n상품명: Sample Plan\n상품코드: SAMPLE-P\n"
        f"약관코드: TERMS-{code}\n판본코드: EDITION-{code}\n"
        f"담보명: {raw['_fixture_label']}\nArticle 1\n{body}\n"
        "Article 2\nSynthetic companion input record: "
        + _json(
            {
                key: value
                for key, value in raw.items()
                if key
                in {
                    "event_field",
                    "event_value",
                    "required_field",
                    "required_value",
                    "benefit_type",
                    "calculation",
                }
            }
        )
        + "."
    )
    digest = _digest(text)
    _add_document(url, job, text, kind="terms", digest=digest)
    ComponentTermsProjector(url).project_pending()
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        edition = connection.execute(
            "SELECT id,source_component_id FROM terms_editions "
            "WHERE household_space_id=%s AND content_sha256=%s",
            (job.household_space_id, digest),
        ).fetchone()
    if edition is None:
        raise ValueError("FIXED_REVIEW_TERMS_SOURCE_NOT_RETAINED")
    scope = HouseholdScope(job.household_space_id)
    repository = InsuranceDocumentRepository(url)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        existing_set = connection.execute(
            "SELECT id,version FROM insurance_document_sets WHERE household_space_id=%s "
            "AND policy_contract_id=%s AND deleted_at IS NULL",
            (job.household_space_id, policy),
        ).fetchone()
    selected = (
        SimpleNamespace(**existing_set)
        if existing_set
        else repository.create_document_set(
            scope,
            actor_id=job.actor_id,
            member_id=job.family_member_id,
            policy_contract_id=policy,
            insurer_display=None,
            product_display=None,
            display_label="Sample Synthetic Terms Selection",
        )
    )
    repository.attach_set_item(
        scope,
        actor_id=job.actor_id,
        document_set_id=selected.id,
        insurance_document_component_id=edition["source_component_id"],
        match_state="USER_CONFIRMED",
        evidence_id=None,
        expected_set_version=selected.version,
    )
    _refresh_terms(url, job, policy)
    clause = _clause(url, job, edition["id"], body=body, label="Article 1")
    ClauseSourceProjector(url).refresh_pending()
    # Normal deterministic processing runs before the saved local answer. The
    # reference model must not get credit for a skipped local recognition stage.
    if not TermsSemanticProjector(url)._project(scope, edition["id"], lambda: False):
        raise ValueError("FIXED_REVIEW_LOCAL_SEMANTICS_NOT_PROJECTED")
    if raw["enrollment"] != "confirmed":
        return
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        link = _native_link(
            connection,
            job.household_space_id,
            raw.get("_fixture_rider_id", _id(raw["coverage_key"])),
            clause,
            edition["id"],
        )
    RiderClauseLinkRepository(url).confirm(scope, link, expected_version=1)
    _rules(url, job, raw, link, clause)


def seed_fixed_review_case(database_url: str, case: BenchmarkCase) -> FixedReviewFixture:
    """Create once or reopen the exact immutable local answer; preserve paid ledgers."""
    configure_integration_test_database(
        {
            "FAMILYCARE_TEST_DATABASE_URL": database_url,
            "FAMILYCARE_ALLOW_DESTRUCTIVE_TEST_DB": os.environ.get(
                "FAMILYCARE_ALLOW_DESTRUCTIVE_TEST_DB", ""
            ),
        }
    )
    if not case.case_id.startswith("synthetic-"):
        raise ValueError("FIXED_REVIEW_SYNTHETIC_CASE_REQUIRED")
    parameters = _source_parameters(case)
    fingerprint = _digest([REVISION, case.case_id, case.contract_group, parameters])
    scope = HouseholdScope(_id(f"{case.case_id}:household"))
    service = DecisionService(scope=scope, repository=DecisionRepository(database_url))
    keys = {_id(raw["coverage_key"]): raw["coverage_key"] for raw in parameters["coverages"]}
    # A session lock spans the production APIs' separate transactions. A partial
    # seed fails closed; it is never removed or confused with a complete run.
    with psycopg.connect(_psycopg_url(database_url), autocommit=True) as lock:
        lock.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", (case.case_id,))
        try:
            with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
                existing = connection.execute(
                    "SELECT display_name FROM household_spaces WHERE id=%s",
                    (scope.household_space_id,),
                ).fetchone()
            if existing:
                if existing["display_name"] != f"{REVISION}:COMPLETE:{fingerprint}":
                    raise ValueError("FIXED_REVIEW_FIXTURE_INCOMPLETE_OR_CHANGED")
            else:
                job = _bootstrap(database_url, case, fingerprint)
                grouped: dict[str, list[dict[str, Any]]] = {}
                for ordinal, raw in enumerate(parameters["coverages"], 1):
                    raw["_fixture_label"] = f"Sample Benefit {ordinal}"
                    grouped.setdefault(_contract_key(case, raw), []).append(raw)
                policies = []
                for key, rows in grouped.items():
                    source_job = job
                    if rows[0]["subject"] == "other_member":
                        source_job = SimpleNamespace(**vars(job))
                        source_job.family_member_id = job.other_member_id
                        source_job.batch_item_id = job.other_batch_item_id
                    policy = _policy(database_url, case, source_job, key, rows)
                    policies.append((source_job, policy))
                    for raw in rows:
                        _terms(database_url, source_job, policy, raw)
                for source_job, policy in policies:
                    _refresh_terms(database_url, source_job, policy)
                supported = {
                    "event.kind",
                    "event.days",
                    "event.eligible_cost",
                    "event.first_claim",
                }
                raw_facts = parameters["event_facts"]
                facts = {
                    _FIELD_PATHS[key]: _value(key, value)
                    for key, value in raw_facts.items()
                    if key in supported
                }
                if "event.reduction_applies" in raw_facts:
                    reduction = raw_facts["event.reduction_applies"]
                    if type(reduction) is not bool:
                        raise ValueError("FIXED_REVIEW_REDUCTION_FACT_MUST_BE_BOOLEAN")
                    facts["MedicalEvent.reduction_applies"] = reduction
                event = service.create_medical_event(
                    family_member_id=job.family_member_id,
                    mode="post_treatment",
                    situation=_situation(raw_facts),
                    event_date=date(2026, 6, 1),
                    visit_date=date(2026, 6, 1),
                    facts=facts,
                    confirmation={key: "user" for key in facts},
                )
                overrides = [
                    StructuredFactInput(
                        field_id="condition_class",
                        value=raw_facts["event.kind"],
                        code_system=EVENT_KIND_SYSTEM,
                        code_version=EVENT_KIND_VERSION,
                    )
                ]
                if raw_facts.get("event.kind") in {"surgery", "admission", "outpatient"}:
                    overrides.append(
                        StructuredFactInput(
                            field_id="treatment_kind", value=raw_facts["event.kind"]
                        )
                    )
                if "event.admitted" in raw_facts:
                    overrides.append(
                        StructuredFactInput(field_id="admission", value=raw_facts["event.admitted"])
                    )
                if overrides:
                    event = service.update_medical_event(
                        event.id,
                        MedicalEventUpdateRequest(
                            expected_version=event.version,
                            structured_facts=overrides,
                        ),
                    )
                if "event.eligible_cost" in raw_facts:
                    category = {"admission": "inpatient", "outpatient": "outpatient"}.get(
                        raw_facts.get("event.kind")
                    )
                    if category is None:
                        raise ValueError("FIXED_REVIEW_RECEIPT_CATEGORY_SOURCE_UNSUPPORTED")
                    CalculationService(
                        scope, CalculationRepository(database_url)
                    ).create_receipt_line(
                        event.id,
                        ReceiptLineCreateRequest(
                            category=category,
                            coverage_category="covered",
                            amount=raw_facts["event.eligible_cost"],
                            currency="KRW",
                            confirmation_level="user",
                        ),
                    )
                service.analyze_medical_event(event.id)
                with psycopg.connect(_psycopg_url(database_url)) as connection:
                    connection.execute(
                        "UPDATE household_spaces SET display_name=%s WHERE id=%s",
                        (f"{REVISION}:COMPLETE:{fingerprint}", scope.household_space_id),
                    )
            with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
                row = connection.execute(
                    "SELECT r.* FROM medical_events e JOIN decision_runs r "
                    "ON r.medical_event_id=e.id WHERE e.household_space_id=%s "
                    "ORDER BY r.created_at,r.id LIMIT 1",
                    (scope.household_space_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("FIXED_REVIEW_ORIGINAL_MISSING")
                original = service.repository._load_result(connection, scope, row)
            event = service.get_medical_event(row["medical_event_id"])
            if event.version != row["event_version"]:
                raise ValueError("FIXED_REVIEW_EVENT_CHANGED")
            with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
                sources = read_review_sources(connection, scope, event, service.repository)
                retained = connection.execute(
                    "SELECT id,display_name FROM riders WHERE household_space_id=%s "
                    "AND deleted_at IS NULL",
                    (scope.household_space_id,),
                ).fetchall()
                names = {
                    f"Sample Benefit {ordinal}": raw["coverage_key"]
                    for ordinal, raw in enumerate(parameters["coverages"], 1)
                }
                for rider in retained:
                    key = names[rider["display_name"]]
                    keys.pop(_id(key), None)
                    keys[rider["id"]] = key
            return FixedReviewFixture(database_url, scope, event, original, keys, sources, service)
        finally:
            lock.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (case.case_id,))
