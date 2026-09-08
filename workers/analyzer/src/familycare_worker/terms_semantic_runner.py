"""One optional terms proposal; local publication remains an API responsibility."""

from __future__ import annotations

import os
import re
from collections.abc import Callable

from familycare_worker.ai.evidence_loader import EvidenceLoadError
from familycare_worker.ai.provider import (
    DEFAULT_STRUCTURER_MODEL,
    AiProvider,
    ProviderConfigurationError,
    ProviderValidationError,
    RetryableProviderError,
)
from familycare_worker.ai.terms_structurer import structure_terms_region
from familycare_worker.terms_request_budget import BudgetedTermsProvider, TermsRequestBudget
from familycare_worker.terms_semantic_jobs import (
    TermsSemanticJobQueue,
    TermsSemanticJobRecord,
    TermsSemanticWorkConflict,
    TermsSemanticWorkUnavailable,
)


def _configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


class TermsSemanticRunner:
    def __init__(
        self,
        *,
        queue: TermsSemanticJobQueue,
        provider: AiProvider,
        request_budget: TermsRequestBudget,
        enabled: bool = False,
        model: str = DEFAULT_STRUCTURER_MODEL,
        configured: Callable[[], bool] = _configured,
        stop_requested: Callable[[], bool] = lambda: False,
    ) -> None:
        if (
            type(enabled) is not bool
            or not isinstance(model, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", model) is None
        ):
            raise ValueError("TERMS_RUNNER_CONFIGURATION_INVALID")
        self.queue, self.provider, self.request_budget = queue, provider, request_budget
        self.enabled, self.model = enabled, model
        self.configured, self.stop_requested = configured, stop_requested

    def run_once(self, worker_id: str) -> bool:
        if self.stop_requested():
            return False
        ready = self.enabled and self.configured()
        job = self.queue.claim(worker_id, include_paused_configuration=ready)
        if job is None:
            return False
        budgeted = None
        try:
            if not ready:
                self.queue.pause(
                    job,
                    worker_id,
                    "TERMS_PROVIDER_UNCONFIGURED" if self.enabled else "TERMS_STRUCTURING_DISABLED",
                )
                return True
            if not self.queue.heartbeat(job, worker_id):
                return True
            terms = self.queue.load_sensitive_terms(job, worker_id)
            budgeted = BudgetedTermsProvider(
                provider=self.provider, budget=self.request_budget, job=job, worker_id=worker_id
            )
            graph, _ = structure_terms_region(
                envelope=job.envelope, provider=budgeted, model=self.model, sensitive_terms=terms
            )
            # complete performs the lease/source/privacy compare-and-set in its transaction.
            self.queue.complete(job, worker_id, graph)
        except TermsSemanticWorkConflict, TermsSemanticWorkUnavailable:
            # An ambiguous commit is recovered by the queue; never overwrite its outcome.
            return True
        except Exception as error:
            try:
                if budgeted is not None and budgeted.waiting_for_request:
                    self.queue.pause(job, worker_id, "TERMS_PROVIDER_INFLIGHT")
                elif budgeted is not None and budgeted.exhausted_scope is not None:
                    scope = budgeted.exhausted_scope
                    self.queue.pause(
                        job,
                        worker_id,
                        f"TERMS_PROVIDER_{scope.upper()}_BUDGET",
                        daily=scope == "daily",
                    )
                elif isinstance(error, ProviderConfigurationError):
                    self.queue.pause(job, worker_id, "TERMS_PROVIDER_UNCONFIGURED")
                else:
                    self._fail(job, worker_id, error)
            except TermsSemanticWorkConflict, TermsSemanticWorkUnavailable:
                pass
        return True

    def _fail(self, job: TermsSemanticJobRecord, worker_id: str, error: Exception) -> None:
        retryable = isinstance(error, RetryableProviderError)
        code = (
            "TERMS_PRIVACY_UNAVAILABLE"
            if isinstance(error, EvidenceLoadError)
            else "TERMS_PROVIDER_RETRYABLE"
            if retryable
            else "TERMS_STRUCTURING_INVALID"
            if isinstance(error, ProviderValidationError)
            else "TERMS_PROVIDER_FAILED"
        )
        self.queue.fail(job, worker_id, code, retryable=retryable)
