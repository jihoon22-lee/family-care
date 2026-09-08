"""Explicit bounded work requests; local reads never schedule an external call."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from familycare_api.clauses.errors import TermsEditionNotFound
from familycare_api.common.scope import HouseholdScope
from familycare_api.terms_knowledge.generated_contracts import SemanticWorkEnvelope
from familycare_api.terms_knowledge.local_candidates import _citation
from familycare_api.terms_knowledge.projector import _revision
from familycare_api.terms_knowledge.repository import (
    SemanticSourcePlan,
    TermsSemanticRepository,
    _plan,
)
from familycare_api.terms_knowledge.source_verification import _OVERRIDE, _reference
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

WORK_REVISION = "terms-semantic-work-v1"


class SemanticWorkUnsupported(ValueError):
    def __init__(self, code: str = "SEMANTIC_WORK_REGION_UNSUPPORTED") -> None:
        if code not in {
            "SEMANTIC_WORK_REGION_UNSUPPORTED",
            "SEMANTIC_WORK_REFERENCE_UNRESOLVED",
            "SEMANTIC_WORK_LIMIT_EXCEEDED",
        }:
            code = "SEMANTIC_WORK_REGION_UNSUPPORTED"
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class SemanticWorkRequest:
    job_id: UUID
    state: str


def _lock_work_source(
    connection: psycopg.Connection[dict[str, Any]], scope: HouseholdScope, edition: UUID
) -> None:
    row = connection.execute(
        "SELECT lock_terms_semantic_work_source(%s,%s) AS valid",
        (edition, scope.household_space_id),
    ).fetchone()
    if row is None or not row["valid"]:
        raise TermsEditionNotFound


def _privacy_digest(connection: psycopg.Connection[dict[str, Any]], scope: HouseholdScope) -> str:
    row = connection.execute(
        "SELECT terms_semantic_privacy_digest(%s) AS digest", (scope.household_space_id,)
    ).fetchone()
    assert row is not None
    return str(row["digest"])


def build_work_envelope(
    plan: SemanticSourcePlan, primary_ids: tuple[str, ...]
) -> SemanticWorkEnvelope:
    regions = {r.region_id: r for r in plan.snapshot.layout.regions}
    if (
        not isinstance(primary_ids, tuple)
        or not 1 <= len(primary_ids) <= 8
        or any(not isinstance(key, str) for key in primary_ids)
        or len(set(primary_ids)) != len(primary_ids)
        or any(
            key not in regions or regions[key].kind != "article" or not regions[key].complete
            for key in primary_ids
        )
    ):
        raise SemanticWorkUnsupported
    selected: set[str] = set()
    pending = list(primary_ids)
    while pending:
        key = pending.pop()
        if key in selected:
            continue
        selected.add(key)
        if len(selected) > 16:
            raise SemanticWorkUnsupported("SEMANTIC_WORK_LIMIT_EXCEEDED")
        for span in regions[key].body_spans:
            target = _reference(span.text)
            override = _OVERRIDE.fullmatch(span.text.strip())
            if override and override["source"] == regions[key].label:
                target = override["target"]
            if target:
                matches = [
                    region.region_id for region in regions.values() if region.label == target
                ]
                if len(matches) != 1:
                    raise SemanticWorkUnsupported("SEMANTIC_WORK_REFERENCE_UNRESOLVED")
                pending.append(matches[0])
    supplied = [r for r in plan.snapshot.layout.regions if r.region_id in selected]
    if sum(len(span.text) for region in supplied for span in region.spans) > 16384:
        raise SemanticWorkUnsupported("SEMANTIC_WORK_LIMIT_EXCEEDED")
    try:
        return SemanticWorkEnvelope.model_validate(
            {
                "schema_revision": WORK_REVISION,
                "source": plan.snapshot.source.model_dump(mode="json"),
                "input_digest": plan.input_digest,
                "expected_region_ids": list(plan.snapshot.layout.expected_region_ids),
                "primary_region_ids": [r.region_id for r in supplied if r.region_id in primary_ids],
                "regions": [
                    {
                        "region_id": region.region_id,
                        "kind": region.kind,
                        "label": region.label,
                        "complete": region.complete,
                        "citations": [_citation(plan.snapshot, span) for span in region.spans],
                    }
                    for region in supplied
                ],
            }
        )
    except ValidationError:
        raise SemanticWorkUnsupported("SEMANTIC_WORK_LIMIT_EXCEEDED") from None


class TermsSemanticWorkRepository:
    def __init__(self, database_url: str) -> None:
        self.repository = TermsSemanticRepository(database_url)
        self.database_url = self.repository.database_url

    def enqueue(
        self, scope: HouseholdScope, edition: UUID, primary_ids: tuple[str, ...]
    ) -> SemanticWorkRequest:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            _lock_work_source(connection, scope, edition)
            plan = _plan(connection, scope, edition)
            return self._enqueue(connection, scope, edition, plan, primary_ids)

    @staticmethod
    def _enqueue(
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        edition: UUID,
        plan: SemanticSourcePlan,
        primary_ids: tuple[str, ...],
    ) -> SemanticWorkRequest:
        envelope = build_work_envelope(plan, primary_ids)
        payload = envelope.model_dump(mode="json")
        work_key = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        privacy = _privacy_digest(connection, scope)
        row = connection.execute(
            "INSERT INTO "
            "terms_semantic_jobs(household_space_id,terms_edition_id,document_version_id,"
            "input_context,input_digest,privacy_digest,work_key,pipeline_revision,envelope_json) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id,state",
            (
                scope.household_space_id,
                edition,
                UUID(plan.snapshot.source.document_version_id),
                Jsonb(plan.input_context),
                plan.input_digest,
                privacy,
                work_key,
                WORK_REVISION,
                Jsonb(payload),
            ),
        ).fetchone()
        if row is None:
            row = connection.execute(
                "SELECT id,state FROM terms_semantic_jobs WHERE household_space_id=%s "
                "AND terms_edition_id=%s AND input_digest=%s AND privacy_digest=%s "
                "AND work_key=%s AND pipeline_revision=%s",
                (
                    scope.household_space_id,
                    edition,
                    plan.input_digest,
                    privacy,
                    work_key,
                    WORK_REVISION,
                ),
            ).fetchone()
        assert row is not None
        return SemanticWorkRequest(row["id"], row["state"])

    def prepare_pending(
        self, *, limit: int = 5, stop_requested: Callable[[], bool] = lambda: False
    ) -> int:
        """Prepare only unresolved source regions when an ingestion caller enables this path."""
        if type(limit) is not int or not 1 <= limit <= 25:
            raise ValueError("SEMANTIC_WORK_LIMIT_INVALID")
        completed = 0
        for _ in range(limit):
            if stop_requested():
                break
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    "SELECT r.*,region.region_id FROM terms_semantic_processing_runs r "
                    "JOIN terms_semantic_processing_completions done ON done.run_id=r.id "
                    "CROSS JOIN LATERAL "
                    "jsonb_array_elements_text(r.manifest_json->'unresolved_regions') "
                    "AS region(region_id) WHERE r.planner_revision=%s "
                    "AND r.input_context=terms_semantic_input_context("
                    "r.terms_edition_id,r.household_space_id) "
                    "AND NOT EXISTS(SELECT 1 FROM terms_semantic_work_attempts a "
                    "WHERE a.run_id=r.id "
                    "AND a.region_id=region.region_id AND a.work_revision=%s "
                    "AND a.privacy_digest=terms_semantic_privacy_digest(r.household_space_id)) "
                    "ORDER BY r.created_at,r.id,region.region_id LIMIT 1 FOR "
                    "UPDATE OF r SKIP LOCKED",
                    (_revision(), WORK_REVISION),
                ).fetchone()
                if row is None:
                    break
                scope = HouseholdScope(row["household_space_id"])
                _lock_work_source(connection, scope, row["terms_edition_id"])
                plan = _plan(connection, scope, row["terms_edition_id"])
                if plan.input_digest != row["input_digest"]:
                    continue
                job_id = None
                reason = "SEMANTIC_WORK_QUEUED"
                try:
                    job = self._enqueue(
                        connection, scope, row["terms_edition_id"], plan, (row["region_id"],)
                    )
                    job_id = job.job_id
                except SemanticWorkUnsupported as error:
                    reason = error.code
                connection.execute(
                    "INSERT INTO terms_semantic_work_attempts(run_id,region_id,work_revision,"
                    "privacy_digest,outcome,reason_code,job_id) VALUES(%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT DO NOTHING",
                    (
                        row["id"],
                        row["region_id"],
                        WORK_REVISION,
                        _privacy_digest(connection, scope),
                        "QUEUED" if job_id else "UNSUPPORTED",
                        reason,
                        job_id,
                    ),
                )
                completed += 1
        return completed
