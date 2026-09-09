"""One explicit bounded review proposes data; local API verification owns the answer."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event, Thread
from time import monotonic
from typing import Any
from uuid import UUID

from familycare_worker.ai.guidance_reviewer import (
    OUTPUT_TOKEN_LIMIT,
    PROMPT_REVISION,
    REQUEST_TIMEOUT_SECONDS,
    GuidanceReviewResult,
    build_review_request,
)
from familycare_worker.ai.provider import (
    AiProvider,
    ProviderCallMetadata,
    ProviderConfigurationError,
    ProviderIncompleteError,
    ProviderRateLimitError,
    ProviderRefusalError,
    ProviderTimeoutError,
    ProviderValidationError,
)
from familycare_worker.guidance_review_budget import GuidanceReviewBudget, ReviewBudgetRejected
from familycare_worker.guidance_review_jobs import (
    GuidanceReviewQueue,
    ReviewLease,
    ReviewQueueUnavailable,
    ReviewWorkInput,
)


def _configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


@dataclass(repr=False)
class _Outcome:
    result: GuidanceReviewResult | None = None
    error: Exception | None = None


def _local_input(work: ReviewWorkInput) -> dict[str, Any]:
    """Attach citation-derived accounting identities locally; never send these IDs."""
    result: dict[str, Any] = json.loads(json.dumps(work.local_guidance))
    for candidate in result["candidates"]:
        versions: set[UUID] = set()
        unresolved = False
        pending: list[object] = [candidate]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                if value.get("kind") == "SEMANTIC_CITATION":
                    versions.add(UUID(value["document_version_id"]))
                elif value.get("kind") == "OPERATIONAL_EVIDENCE":
                    version = work.evidence_document_versions.get(UUID(value["evidence_id"]))
                    if version is None:
                        unresolved = True
                    else:
                        versions.add(version)
                elif value.get("kind") == "TERMS_SECTION":
                    # Legacy private section IDs have no proved native document identity.
                    unresolved = True
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        candidate["source_document_version_ids"] = (
            [] if unresolved else [str(value) for value in sorted(versions)]
        )
    return result


class GuidanceReviewRunner:
    def __init__(
        self,
        *,
        queue: GuidanceReviewQueue,
        provider: AiProvider,
        request_budget: GuidanceReviewBudget,
        configured: Callable[[], bool] = _configured,
        stop_requested: Callable[[], bool] = lambda: False,
        enabled: bool = True,
        call_timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        if (
            type(enabled) is not bool
            or type(call_timeout_seconds) not in (int, float)
            or not 0 < call_timeout_seconds <= REQUEST_TIMEOUT_SECONDS
        ):
            raise ValueError("REVIEW_RUNNER_CONFIGURATION_INVALID")
        self.queue, self.provider, self.request_budget = queue, provider, request_budget
        self.configured, self.stop_requested, self.enabled = configured, stop_requested, enabled
        self.call_timeout_seconds = float(call_timeout_seconds)
        self._active_call: Thread | None = None

    def run_once(self, worker_id: str) -> bool:
        del worker_id
        if self.stop_requested() or (self._active_call and self._active_call.is_alive()):
            # At most one detached provider call may exist in this process.
            return False
        job = self.queue.claim()
        if job is None:
            return False
        try:
            if not self.enabled or not self.configured() or job.prompt_revision != PROMPT_REVISION:
                self.queue.fail(job, "REVIEW_PROVIDER_CONFIGURATION")
                return True
            work = self.queue.load_inputs(job)
            if not work.sources.get("packets"):
                self.queue.fail(job, "REVIEW_SOURCE_UNAVAILABLE")
                return True
            request = build_review_request(
                sources=work.sources,
                event=work.event,
                local_guidance=_local_input(work),
                model=job.model,
                sensitive_terms=work.sensitive_terms,
            )
            if not request.payload["source_packets"]:
                self.queue.fail(job, "REVIEW_SOURCE_UNAVAILABLE")
                return True
            if not set(request.document_version_ids) <= set(work.document_versions):
                raise ReviewQueueUnavailable
            documents = tuple(
                sorted({work.document_versions[v] for v in request.document_version_ids})
            )
            remaining = (job.deadline_at - datetime.now(UTC)).total_seconds()
            if remaining <= 0 or self.stop_requested():
                self.queue.fail(job, "REVIEW_DEADLINE_EXCEEDED")
                return True
            reservation = self.request_budget.reserve(
                job,
                phase="discover",
                document_ids=documents,
                fingerprint=request.fingerprint,
                input_tokens=request.conservative_token_bound,
                output_tokens=OUTPUT_TOKEN_LIMIT,
            )
            # Reserving may wait for the shared quota lock. Recheck after that wait,
            # immediately before starting the wire call, and retain the reservation.
            self.queue.load_inputs(job)
            remaining = (job.deadline_at - datetime.now(UTC)).total_seconds()
            if remaining <= 0 or self.stop_requested():
                self.queue.fail(job, "REVIEW_DEADLINE_EXCEEDED")
                return True
            finished, outcome = Event(), _Outcome()

            def call() -> None:
                metadata = None
                try:
                    outcome.result = request.call(self.provider)
                    metadata = outcome.result.metadata
                    usage = metadata.usage if metadata else None
                    if usage is not None and (
                        usage.input_tokens > request.conservative_token_bound
                        or usage.output_tokens > OUTPUT_TOKEN_LIMIT
                    ):
                        outcome.error = ReviewBudgetRejected()
                except Exception as error:
                    outcome.error = error
                    candidate = getattr(error, "metadata", None)
                    metadata = candidate if isinstance(candidate, ProviderCallMetadata) else None
                try:
                    self.request_budget.finish(
                        reservation, succeeded=outcome.error is None, metadata=metadata
                    )
                except Exception:
                    # Retain an ambiguous reservation. It must never trigger retransmission.
                    outcome.error = ReviewBudgetRejected()
                finally:
                    finished.set()

            self._active_call = Thread(target=call, daemon=True, name="guidance-review-call")
            deadline = monotonic() + min(self.call_timeout_seconds, remaining)
            self._active_call.start()
            while not finished.wait(timeout=min(0.1, max(0, deadline - monotonic()))):
                if monotonic() >= deadline or self.stop_requested():
                    # A network request cannot be uncharged by local cancellation. The single
                    # bounded background call may settle usage, but cannot publish its result.
                    self.queue.fail(job, "REVIEW_PROVIDER_TIMEOUT")
                    return True
            if outcome.error is not None:
                raise outcome.error
            assert outcome.result is not None
            self.queue.load_inputs(job)
            self.queue.record_proposal(job, outcome.result.to_payload())
        except Exception as error:
            self._fail(job, error)
        return True

    def _fail(self, job: ReviewLease, error: Exception) -> None:
        code = (
            "REVIEW_PROVIDER_CONFIGURATION"
            if isinstance(error, ProviderConfigurationError)
            else "REVIEW_REFUSED"
            if isinstance(error, ProviderRefusalError)
            else "REVIEW_INCOMPLETE"
            if isinstance(error, ProviderIncompleteError)
            else "REVIEW_RATE_LIMITED"
            if isinstance(error, ProviderRateLimitError)
            else "REVIEW_PROVIDER_TIMEOUT"
            if isinstance(error, ProviderTimeoutError)
            else "REVIEW_INVALID_RESPONSE"
            if isinstance(error, ProviderValidationError)
            else "REVIEW_BUDGET_EXCEEDED"
            if isinstance(error, ReviewBudgetRejected)
            else "REVIEW_SOURCE_UNAVAILABLE"
            if isinstance(error, ReviewQueueUnavailable)
            else "REVIEW_PROVIDER_UNAVAILABLE"
        )
        with suppress(ReviewQueueUnavailable):
            self.queue.fail(job, code)
