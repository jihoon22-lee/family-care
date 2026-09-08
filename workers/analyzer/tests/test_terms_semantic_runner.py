"""Terms execution preserves local results and does not spend on disabled work."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from familycare_worker.ai.evidence_loader import EvidenceLoadError
from familycare_worker.ai.provider import (
    ProviderConfigurationError,
    ProviderTimeoutError,
    ProviderValidationError,
)
from familycare_worker.terms_semantic_runner import TermsSemanticRunner


@pytest.fixture()
def context(monkeypatch):
    from familycare_worker import terms_semantic_runner as module

    job = SimpleNamespace(envelope=object())
    queue = Mock()
    queue.claim.return_value = job
    queue.heartbeat.return_value = True
    queue.load_sensitive_terms.return_value = ("Synthetic Member",)
    provider = Mock(exhausted_scope=None, waiting_for_request=False)
    wrapped = Mock(return_value=provider)
    monkeypatch.setattr(module, "BudgetedTermsProvider", wrapped)
    structure = Mock(return_value=(object(), "synthetic-request"))
    monkeypatch.setattr(module, "structure_terms_region", structure)
    runner = TermsSemanticRunner(
        queue=queue,
        provider=provider,
        request_budget=object(),
        enabled=True,
        configured=lambda: True,
    )
    return runner, queue, provider, wrapped, structure, job


@pytest.mark.parametrize("disabled", [True, False])
def test_disabled_or_unconfigured_work_pauses_before_budget_or_provider(context, disabled):
    runner, queue, _, wrapped, structure, job = context
    runner.enabled = not disabled
    runner.configured = lambda: disabled
    assert runner.run_once("synthetic-worker")
    queue.claim.assert_called_once_with("synthetic-worker", include_paused_configuration=False)
    queue.pause.assert_called_once_with(
        job,
        "synthetic-worker",
        "TERMS_STRUCTURING_DISABLED" if disabled else "TERMS_PROVIDER_UNCONFIGURED",
    )
    wrapped.assert_not_called()
    structure.assert_not_called()
    queue.complete.assert_not_called()


def test_ready_work_resumes_with_household_minimization_and_retains_only_candidate(context):
    runner, queue, provider, wrapped, structure, job = context
    assert runner.run_once("synthetic-worker")
    queue.claim.assert_called_once_with("synthetic-worker", include_paused_configuration=True)
    assert wrapped.call_args.kwargs["job"] is job
    assert structure.call_args.kwargs["sensitive_terms"] == ("Synthetic Member",)
    assert structure.call_args.kwargs["envelope"] is job.envelope
    assert structure.call_args.kwargs["provider"] is provider
    queue.complete.assert_called_once_with(job, "synthetic-worker", structure.return_value[0])
    queue.fail.assert_not_called()


@pytest.mark.parametrize("scope", ["document", "daily"])
def test_budget_wait_scope_survives_provider_error_sanitization(context, scope):
    runner, queue, provider, _, structure, job = context
    provider.exhausted_scope = scope
    structure.side_effect = ProviderTimeoutError
    assert runner.run_once("synthetic-worker")
    queue.pause.assert_called_once_with(
        job, "synthetic-worker", f"TERMS_PROVIDER_{scope.upper()}_BUDGET", daily=scope == "daily"
    )
    queue.fail.assert_not_called()
    queue.complete.assert_not_called()


@pytest.mark.parametrize(
    "error,code,retryable",
    [
        (ProviderTimeoutError, "TERMS_PROVIDER_RETRYABLE", True),
        (ProviderValidationError, "TERMS_STRUCTURING_INVALID", False),
        (RuntimeError, "TERMS_PROVIDER_FAILED", False),
    ],
)
def test_failure_is_fixed_and_cannot_publish_a_candidate(context, error, code, retryable):
    runner, queue, _, _, structure, job = context
    structure.side_effect = error
    assert runner.run_once("synthetic-worker")
    queue.fail.assert_called_once_with(job, "synthetic-worker", code, retryable=retryable)
    queue.complete.assert_not_called()


def test_key_removed_after_claim_pauses_without_retrying(context):
    runner, queue, _, _, structure, job = context
    structure.side_effect = ProviderConfigurationError
    assert runner.run_once("synthetic-worker")
    queue.pause.assert_called_once_with(job, "synthetic-worker", "TERMS_PROVIDER_UNCONFIGURED")
    queue.fail.assert_not_called()


def test_unavailable_privacy_set_cannot_reach_provider(context):
    runner, queue, _, wrapped, structure, job = context
    queue.load_sensitive_terms.side_effect = EvidenceLoadError
    assert runner.run_once("synthetic-worker")
    wrapped.assert_not_called()
    structure.assert_not_called()
    queue.fail.assert_called_once_with(
        job, "synthetic-worker", "TERMS_PRIVACY_UNAVAILABLE", retryable=False
    )


def test_lost_lease_and_shutdown_cannot_publish(context):
    runner, queue, _, wrapped, structure, _ = context
    queue.heartbeat.return_value = False
    assert runner.run_once("synthetic-worker")
    structure.assert_not_called()
    wrapped.assert_not_called()
    queue.complete.assert_not_called()
    runner.stop_requested = lambda: True
    queue.reset_mock()
    assert not runner.run_once("synthetic-worker")
    queue.claim.assert_not_called()


def test_inflight_wait_does_not_consume_execution_retry(context):
    runner, queue, provider, _, structure, job = context
    provider.waiting_for_request = True
    structure.side_effect = ProviderTimeoutError
    assert runner.run_once("synthetic-worker")
    queue.pause.assert_called_once_with(job, "synthetic-worker", "TERMS_PROVIDER_INFLIGHT")
    queue.fail.assert_not_called()
    queue.complete.assert_not_called()
