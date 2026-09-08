"""Terms reservations share the policy quota and retain only minimized responses."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from familycare_worker.ai.minimizer import MINIMIZATION_REVISION
from familycare_worker.ai.provider import (
    AiProvider,
    ProviderBoundaryError,
    ProviderConfigurationError,
    ProviderRateLimitError,
    ProviderResponse,
    ProviderUnavailableError,
    ProviderValidationError,
    provider_payload,
)
from familycare_worker.ai.terms_structurer import (
    TERMS_STRUCTURER_SCHEMA_NAME,
    _hydrate,
    _minimize,
    terms_structurer_schema,
)
from familycare_worker.policy_request_budget import PolicyRequestBudget
from familycare_worker.terms_semantic_jobs import (
    TermsSemanticJobQueue,
    TermsSemanticJobRecord,
    TermsSemanticWorkConflict,
    TermsSemanticWorkUnavailable,
    _owned,
)

_HEX = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


class TermsBudgetExhausted(ProviderRateLimitError):
    def __init__(self, scope: Literal["document", "daily"]) -> None:
        if scope not in ("document", "daily"):
            raise ProviderConfigurationError
        self.scope = scope
        ProviderBoundaryError.__init__(self, "TERMS_PROVIDER_BUDGET_EXHAUSTED")


def _json(value: object, *, bounded: bool = True) -> str:
    try:
        text = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        if bounded and len(text.encode()) > 131072:
            raise ProviderValidationError
        return text
    except TypeError, ValueError, RecursionError:
        raise ProviderValidationError from None


def _response(value: object) -> ProviderResponse:
    payload, request_id = provider_payload(value)
    if _IDENTIFIER.fullmatch(request_id) is None:
        raise ProviderValidationError
    return ProviderResponse(json.loads(_json(dict(payload))), request_id)


class TermsRequestBudget:
    """Reserve under the global policy lock, then release all locks before a call."""

    def __init__(self, database_url: str, *, per_document: int = 4, daily: int = 8) -> None:
        self._shared = PolicyRequestBudget(database_url, per_document=per_document, daily=daily)
        self.database_url = self._shared.database_url
        self.per_document = per_document
        self.daily = daily

    def reserve(
        self, job: TermsSemanticJobRecord, worker_id: str, fingerprint: str
    ) -> UUID | ProviderResponse:
        if not isinstance(fingerprint, str) or _HEX.fullmatch(fingerprint) is None:
            raise ProviderValidationError
        error: ProviderBoundaryError | None = None
        result: UUID | ProviderResponse | None = None
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET LOCAL lock_timeout='5s'")
                connection.execute("SELECT pg_advisory_xact_lock(523019,28)")
                _owned(connection, job, worker_id)
                document = connection.execute(
                    "SELECT document_id FROM document_versions WHERE id=%s",
                    (job.document_version_id,),
                ).fetchone()
                assert document is not None
                document_id = document["document_id"]
                cached = connection.execute(
                    "SELECT p.response_json,p.request_id FROM policy_provider_requests p "
                    "JOIN terms_semantic_jobs prior ON prior.id=p.terms_job_id "
                    "WHERE p.document_id=%s AND prior.household_space_id=%s AND p.fingerprint=%s "
                    "AND p.state='SUCCEEDED' ORDER BY p.reserved_at DESC,p.id DESC LIMIT 1",
                    (document_id, job.household_space_id, fingerprint),
                ).fetchone()
                if cached is not None:
                    return _response(
                        ProviderResponse(cached["response_json"], cached["request_id"])
                    )
                connection.execute(
                    "UPDATE policy_provider_requests SET state='FAILED' WHERE document_id=%s "
                    "AND fingerprint=%s AND state='RESERVED' AND expires_at<=clock_timestamp()",
                    (document_id, fingerprint),
                )
                busy = connection.execute(
                    "SELECT 1 FROM policy_provider_requests WHERE document_id=%s AND "
                    "fingerprint=%s AND state='RESERVED'",
                    (document_id, fingerprint),
                ).fetchone()
                counts = connection.execute(
                    "SELECT count(*) FILTER(WHERE document_id=%s) AS document_requests, "
                    "count(*) FILTER(WHERE reserved_at>=date_trunc('day',clock_timestamp() AT TIME "
                    "ZONE 'UTC') AT TIME ZONE 'UTC') AS daily_requests "
                    "FROM policy_provider_requests",
                    (document_id,),
                ).fetchone()
                assert counts is not None
                if busy:
                    error = ProviderUnavailableError()
                elif counts["document_requests"] >= self.per_document:
                    error = TermsBudgetExhausted("document")
                elif counts["daily_requests"] >= self.daily:
                    error = TermsBudgetExhausted("daily")
                else:
                    reservation = connection.execute(
                        "INSERT INTO policy_provider_requests(terms_job_id,document_id,fingerprint,"
                        "state) VALUES(%s,%s,%s,'RESERVED') RETURNING id",
                        (job.id, document_id, fingerprint),
                    ).fetchone()
                    assert reservation is not None
                    result = reservation["id"]
            # Preserve expired reservations as FAILED even when the cap rejects retry.
            if error is not None:
                raise error
            assert result is not None
            return result
        except psycopg.Error, TermsSemanticWorkConflict, TermsSemanticWorkUnavailable:
            raise ProviderUnavailableError from None

    def finish(self, reservation: UUID, response: ProviderResponse | None) -> None:
        self._shared.finish(reservation, _response(response) if response is not None else None)


class BudgetedTermsProvider:
    def __init__(
        self,
        *,
        provider: AiProvider,
        budget: TermsRequestBudget,
        job: TermsSemanticJobRecord,
        worker_id: str,
    ) -> None:
        self.provider, self.budget, self.job, self.worker_id = provider, budget, job, worker_id
        self.exhausted_scope: Literal["document", "daily"] | None = None

    def complete(
        self,
        *,
        model: str,
        schema_name: str,
        system_instruction: str,
        input_payload: Mapping[str, object],
    ) -> ProviderResponse:
        self.exhausted_scope = None
        if (
            not isinstance(model, str)
            or _IDENTIFIER.fullmatch(model) is None
            or schema_name != TERMS_STRUCTURER_SCHEMA_NAME
            or not isinstance(system_instruction, str)
            or not 1 <= len(system_instruction) <= 16384
        ):
            raise ProviderValidationError
        try:
            terms = TermsSemanticJobQueue(self.budget.database_url).load_sensitive_terms(
                self.job, self.worker_id
            )
            minimized = _minimize(self.job.envelope, terms)
        except TermsSemanticWorkConflict, TermsSemanticWorkUnavailable:
            raise ProviderUnavailableError from None
        supplied: dict[str, Any] = json.loads(_json(dict(input_payload)))
        if supplied != minimized.payload:
            raise ProviderValidationError
        schema_digest = hashlib.sha256(
            _json(terms_structurer_schema(), bounded=False).encode()
        ).hexdigest()
        fingerprint = hashlib.sha256(
            _json(
                {
                    "model": model,
                    "schema_name": schema_name,
                    "instruction": system_instruction,
                    "schema_sha256": schema_digest,
                    "minimization_revision": MINIMIZATION_REVISION,
                    "payload": supplied,
                },
                bounded=False,
            ).encode()
        ).hexdigest()
        try:
            reserved = self.budget.reserve(self.job, self.worker_id, fingerprint)
        except TermsBudgetExhausted as error:
            self.exhausted_scope = error.scope
            raise
        if isinstance(reserved, ProviderResponse):
            _hydrate(reserved.payload, minimized, model)
            return reserved
        try:
            response = _response(
                self.provider.complete(
                    model=model,
                    schema_name=schema_name,
                    system_instruction=system_instruction,
                    input_payload=supplied,
                )
            )
            # Validate the minimized response before caching; discard the hydration.
            # The outer boundary repeats this check and restores current original proof.
            _hydrate(response.payload, minimized, model)
        except Exception:
            self.budget.finish(reserved, None)
            raise
        self.budget.finish(reserved, response)
        return response
