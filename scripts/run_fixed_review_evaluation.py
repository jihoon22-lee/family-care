#!/usr/bin/env python3
"""Explicit, durable evaluation of the frozen synthetic cases through the real Worker.

Prepare and report never call OpenAI. Run uses one request per case, zero SDK
retries, a persisted USD ceiling, and the existing server review budget. The
dedicated database and journal must be retained together after any attempt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import asdict
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import httpx2
import openai
import psycopg
from familycare_api.guidance.models import LocalGuidanceResponse
from familycare_api.guidance_review.models import GuidanceReviewJob
from familycare_api.guidance_review.projector import GuidanceReviewProjector
from familycare_api.guidance_review.repository import GuidanceReviewRepository
from familycare_worker.ai.guidance_reviewer import SCHEMA_NAME, guidance_review_schema
from familycare_worker.ai.provider import OpenAiResponsesAdapter, _OpenAiClient
from familycare_worker.guidance_review_budget import GuidanceReviewBudget
from familycare_worker.guidance_review_jobs import (
    GuidanceReviewQueue,
    ReviewLease,
    ReviewQueueUnavailable,
    ReviewWorkInput,
)
from familycare_worker.guidance_review_runner import GuidanceReviewRunner

from scripts.claim_guidance_benchmark import (
    DEFAULT_CASES,
    BenchmarkCase,
    Prediction,
    load_cases,
    score_predictions,
)
from scripts.fixed_review_evaluation import (
    MODEL,
    BudgetedReviewTransport,
    EvaluationBudgetError,
    EvaluationJournal,
)
from scripts.fixed_review_fixture import FixedReviewFixture
from scripts.integration_test_database import configure_integration_test_database


class ExpectedReviewQueue(GuidanceReviewQueue):
    """Assert the journal's exact job before loading any provider input."""

    expected_job_id: UUID | None = None

    def load_inputs(self, job: ReviewLease) -> ReviewWorkInput:
        if self.expected_job_id is None or job.id != self.expected_job_id:
            raise ReviewQueueUnavailable
        return super().load_inputs(job)


def _require_empty_queue(database_url: str) -> None:
    with psycopg.connect(
        database_url.replace("postgresql+psycopg://", "postgresql://")
    ) as connection:
        row = connection.execute(
            "SELECT EXISTS(SELECT 1 FROM guidance_review_jobs WHERE state IN ('queued','running'))"
        ).fetchone()
        if row is None or row[0]:
            raise EvaluationBudgetError


def _prediction(
    case_id: str, sample: FixedReviewFixture, guidance: LocalGuidanceResponse
) -> Prediction:
    return Prediction(
        case_id,
        tuple(
            sorted(
                sample.coverage_keys[c.ref.coverage_id]
                for c in guidance.candidates
                if c.group == "PRIMARY"
            )
        ),
        tuple(
            sorted(
                sample.coverage_keys[c.ref.coverage_id]
                for c in guidance.candidates
                if c.group == "CONDITIONAL"
            )
        ),
        not guidance.candidates and guidance.outcome in {"KNOWLEDGE_PENDING", "INPUT_UNRESOLVED"},
    )


def _details(sample: FixedReviewFixture, guidance: LocalGuidanceResponse) -> dict[str, Any]:
    return {
        sample.coverage_keys[candidate.ref.coverage_id]: {
            "group": candidate.group,
            "condition_result": candidate.condition_result,
            "estimate": candidate.estimate.model_dump(mode="json"),
            "cases": [case.model_dump(mode="json") for case in candidate.cases],
        }
        for candidate in guidance.candidates
    }


def _prepare(
    journal: EvaluationJournal,
    cases: Sequence[BenchmarkCase],
    database_url: str,
) -> dict[str, FixedReviewFixture]:
    # Load test-only seeding dependencies only for this explicit synthetic command.
    seed_fixed_review_case = cast(
        Callable[[str, BenchmarkCase], FixedReviewFixture],
        import_module("apps.api.tests.fixed_guidance_review_fixture").seed_fixed_review_case,
    )
    samples = {}
    for case in cases:
        sample = seed_fixed_review_case(database_url, case)
        samples[case.case_id] = sample
        local = _prediction(case.case_id, sample, sample.original.local_guidance)
        record = journal.record["cases"].setdefault(
            case.case_id,
            {
                "local": asdict(local),
                "state": "prepared",
                "review_job_id": None,
            },
        )
        # The fixture reopens the immutable original run and rejects source/input drift.
        if Prediction.from_record(record["local"]) != local:
            raise EvaluationBudgetError
        record["local_details"] = _details(sample, sample.original.local_guidance)
        record["available_source_packets"] = len(sample.sources.packets)
        journal.save()
    return samples


def _collect(
    journal: EvaluationJournal,
    case_id: str,
    sample: FixedReviewFixture,
    reviewed: GuidanceReviewJob,
) -> None:
    if (
        reviewed.medical_event_id != sample.event.id
        or reviewed.decision_run_id != sample.original.run_id
        or reviewed.event_version != sample.event.version
        or str(reviewed.id) != journal.record["cases"][case_id]["review_job_id"]
        or (reviewed.usage is not None and reviewed.usage.requests_reserved > 1)
    ):
        raise EvaluationBudgetError
    record = journal.record["cases"][case_id]
    record["state"] = reviewed.state
    record["error_code"] = reviewed.error_code
    record["usage"] = reviewed.usage.model_dump(mode="json") if reviewed.usage else None
    record["job_duration_seconds"] = (
        max(0.0, (reviewed.completed_at - reviewed.created_at).total_seconds())
        if reviewed.completed_at is not None
        else None
    )
    if reviewed.result is not None:
        record["reviewed"] = asdict(_prediction(case_id, sample, reviewed.result.guidance))
        record["findings"] = [
            finding.model_dump(mode="json") for finding in reviewed.result.findings
        ]
        record["reviewed_details"] = _details(sample, reviewed.result.guidance)
        record["review_scope"] = reviewed.result.scope.model_dump(mode="json")
    journal.save()


def _reconcile(
    journal: EvaluationJournal,
    samples: dict[str, FixedReviewFixture],
    database_url: str,
) -> None:
    """Recover retained results after a crash; never enqueue or transmit."""
    if not any(v["review_job_id"] is not None for v in journal.record["cases"].values()):
        return
    GuidanceReviewProjector(database_url).project_pending(limit=20)
    repository = GuidanceReviewRepository(database_url, model=MODEL)
    for case_id, record in journal.record["cases"].items():
        if record["review_job_id"] is not None:
            sample = samples[case_id]
            reviewed = repository.get_job(sample.scope, UUID(record["review_job_id"]))
            _collect(journal, case_id, sample, reviewed)


def _run(
    journal: EvaluationJournal,
    cases: Sequence[BenchmarkCase],
    samples: dict[str, FixedReviewFixture],
    database_url: str,
) -> None:
    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise EvaluationBudgetError
    # Refuse paid calls when the companion database adapter has changed the frozen task.
    for case in cases:
        local = Prediction.from_record(journal.record["cases"][case.case_id]["local"])
        if set(local.primary) != set(case.expected_primary) or set(local.conditional) != set(
            case.expected_conditional
        ):
            raise EvaluationBudgetError
    repository = GuidanceReviewRepository(database_url, model=MODEL)
    projector = GuidanceReviewProjector(database_url)
    _require_empty_queue(database_url)
    queue = ExpectedReviewQueue(database_url)
    transport = BudgetedReviewTransport(journal, httpx2.HTTPTransport(retries=0))
    with httpx2.Client(transport=transport, follow_redirects=False, trust_env=False) as client:
        provider = OpenAiResponsesAdapter(
            {SCHEMA_NAME: guidance_review_schema()},
            client_factory=lambda api_key: cast(
                _OpenAiClient,
                openai.OpenAI(
                    api_key=api_key,
                    base_url="https://api.openai.com/v1",
                    max_retries=0,
                    http_client=client,
                ),
            ),
            output_token_limits={SCHEMA_NAME: 4000},
            request_timeouts={SCHEMA_NAME: 40.0},
        )
        runner = GuidanceReviewRunner(
            queue=queue,
            provider=provider,
            request_budget=GuidanceReviewBudget(database_url, daily=len(cases)),
        )
        for case in cases:
            sample = samples[case.case_id]
            record = journal.record["cases"][case.case_id]
            if case.case_id in journal.record["wire"] or record["review_job_id"] is not None:
                # A crash between enqueue and transmission also requires inspection, not retry.
                continue
            transport.case_id = case.case_id
            _require_empty_queue(database_url)
            review = repository.enqueue(
                sample.scope,
                sample.event.id,
                run_id=sample.original.run_id,
                expected_event_version=sample.event.version,
            )
            record["review_job_id"] = str(review.id)
            record["state"] = "enqueued"
            journal.save()
            queue.expected_job_id = review.id
            if not runner.run_once("synthetic-fixed-evaluation"):
                raise EvaluationBudgetError
            if runner._active_call and runner._active_call.is_alive():
                # Retain its durable reservation; no next case can inherit an active call.
                runner._active_call.join(timeout=45)
                if runner._active_call.is_alive():
                    raise EvaluationBudgetError
            projector.project_pending(limit=1)
            reviewed = repository.get_job(sample.scope, review.id)
            if reviewed.state in {"queued", "running"}:
                raise EvaluationBudgetError
            _collect(journal, case.case_id, sample, reviewed)
            record["evaluation_error"] = transport.rejection
            journal.save()
            print(
                json.dumps(
                    {
                        "case_id": case.case_id,
                        "state": record["state"],
                        "evaluation_error": transport.rejection,
                        "requests": journal.budget.request_count,
                        "committed_usd": str(journal.budget.committed_cost),
                    }
                ),
                flush=True,
            )
            if transport.rejection is not None:
                break


def _candidate_effects(
    cases: Sequence[BenchmarkCase],
    before: Sequence[Prediction],
    after: Sequence[Prediction],
) -> dict[str, int]:
    result = {
        "corrected_candidate_labels": 0,
        "new_candidate_errors": 0,
        "changed_but_still_wrong": 0,
    }
    old = {item.case_id: item for item in before}
    new = {item.case_id: item for item in after}
    for case in cases:
        expected = {
            **dict.fromkeys(case.expected_primary, "PRIMARY"),
            **dict.fromkeys(case.expected_conditional, "CONDITIONAL"),
        }

        def labels(value: Prediction) -> dict[str, str]:
            return {
                **dict.fromkeys(value.primary, "PRIMARY"),
                **dict.fromkeys(value.conditional, "CONDITIONAL"),
            }

        left, right = labels(old[case.case_id]), labels(new[case.case_id])
        for key in case.candidate_pool:
            was, now, truth = left.get(key), right.get(key), expected.get(key)
            result["corrected_candidate_labels"] += was != truth and now == truth
            result["new_candidate_errors"] += was == truth and now != truth
            result["changed_but_still_wrong"] += was != now and was != truth and now != truth
    return result


def _point_outcomes(case: BenchmarkCase, details: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    relevant = set(case.expected_primary) | set(case.expected_conditional)
    for raw in cast(list[dict[str, Any]], case.scenario_parameters["coverages"]):
        expected = raw.get("calculation", {}).get("expected_amount")
        if expected is None or raw["coverage_key"] not in relevant:
            continue
        key = raw["coverage_key"]
        observed = details.get(key, {}).get("estimate", {})
        if observed.get("kind") != "POINT" or observed.get("amount") is None:
            result[key] = "uncomputed"
        elif observed.get("currency") == "TST" and Decimal(observed["amount"]) == Decimal(
            str(expected)
        ):
            result[key] = "correct"
        else:
            result[key] = "incorrect"
    return result


def _amount_metrics(case: BenchmarkCase, details: dict[str, Any]) -> dict[str, int]:
    outcomes = _point_outcomes(case, details)
    result = {
        "expected_points": len(outcomes),
        "correct_points": sum(value == "correct" for value in outcomes.values()),
        "incorrect_points": sum(value == "incorrect" for value in outcomes.values()),
        "uncomputed_expected_points": sum(value == "uncomputed" for value in outcomes.values()),
        "points_without_fixed_oracle": 0,
        "ranges": 0,
        "formulas": 0,
        "unavailable": 0,
    }
    for key, item in details.items():
        kind = item["estimate"]["kind"]
        result["points_without_fixed_oracle"] += kind == "POINT" and key not in outcomes
        for label, expected_kind in (
            ("ranges", "RANGE"),
            ("formulas", "FORMULA"),
            ("unavailable", "UNAVAILABLE"),
        ):
            result[label] += kind == expected_kind
    return result


def _amount_effects(
    case: BenchmarkCase,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, int]:
    left, right = _point_outcomes(case, before), _point_outcomes(case, after)
    return {
        "recovered_or_corrected_points": sum(
            left[key] != "correct" and value == "correct" for key, value in right.items()
        ),
        "new_incorrect_points": sum(
            left[key] != "incorrect" and value == "incorrect" for key, value in right.items()
        ),
        "lost_correct_points": sum(
            left[key] == "correct" and value != "correct" for key, value in right.items()
        ),
    }


def _report(journal: EvaluationJournal, cases: Sequence[BenchmarkCase]) -> dict[str, Any]:
    local = [Prediction.from_record(journal.record["cases"][c.case_id]["local"]) for c in cases]
    # Failed/unexecuted review retains its actual local answer, and its state stays visible.
    after = [
        Prediction.from_record(
            journal.record["cases"][c.case_id].get(
                "reviewed", journal.record["cases"][c.case_id]["local"]
            )
        )
        for c in cases
    ]
    records = journal.record["cases"]
    durations = sorted(
        v["job_duration_seconds"]
        for v in records.values()
        if v.get("job_duration_seconds") is not None
    )
    scopes = [v["review_scope"] for v in records.values() if "review_scope" in v]
    usages = [v["usage"] for v in records.values() if v.get("usage") is not None]
    amount: dict[str, dict[str, int]] = {
        stage: {} for stage in ("local", "after_review_or_retained_local")
    }
    amount_effects: dict[str, int] = {}
    for case in cases:
        record = records[case.case_id]
        for key, value in _amount_effects(
            case, record["local_details"], record.get("reviewed_details", record["local_details"])
        ).items():
            amount_effects[key] = amount_effects.get(key, 0) + value
        for stage, details in (
            ("local", record["local_details"]),
            (
                "after_review_or_retained_local",
                record.get("reviewed_details", record["local_details"]),
            ),
        ):
            for key, value in _amount_metrics(case, details).items():
                amount[stage][key] = amount[stage].get(key, 0) + value
    return {
        "case_count": len(cases),
        "requests": journal.budget.request_count,
        "confirmed_http_responses": sum(
            wire.get("response_received") is True for wire in journal.record["wire"].values()
        ),
        "review_result_count": sum("reviewed" in v for v in journal.record["cases"].values()),
        "committed_usd": str(journal.budget.committed_cost),
        "limit_usd": str(journal.budget.limit),
        "source": journal.source,
        "git_source_sha": journal.record.get("git_source_sha"),
        "candidate_effects": _candidate_effects(cases, local, after),
        "amount_support": amount,
        "amount_effects": amount_effects,
        "job_creation_to_terminal_seconds": {
            "measured_cases": len(durations),
            "p50": durations[math.ceil(len(durations) * 0.5) - 1] if durations else None,
            "p95": durations[math.ceil(len(durations) * 0.95) - 1] if durations else None,
        },
        "review_scope": {
            "reported_cases": len(scopes),
            **{
                key: sum(scope[key] for scope in scopes)
                for key in (
                    "total_coverages",
                    "total_packets",
                    "reviewed_packets",
                    "unreviewed_packets",
                    "omitted_packets",
                    "expected_regions",
                    "supplied_regions",
                    "unsupplied_regions",
                )
            },
        },
        "usage": {
            "reported_cases": len(usages),
            "complete_cases": sum(usage["usage_complete"] for usage in usages),
            **{
                f"observed_{key}": sum(usage[key] for usage in usages if usage[key] is not None)
                for key in ("input_tokens", "output_tokens", "total_tokens")
            },
        },
        "cases": {
            key: {
                "state": v["state"],
                "error_code": v.get("error_code"),
                "evaluation_error": v.get("evaluation_error"),
            }
            for key, v in journal.record["cases"].items()
        },
        "local": {
            split: score_predictions(cases, local, split=split).as_dict()
            for split in ("dev", "holdout")
        },
        "after_review_or_retained_local": {
            split: score_predictions(cases, after, split=split).as_dict()
            for split in ("dev", "holdout")
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "run", "report"))
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--maximum-usd", type=Decimal, default=Decimal("1"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=root)
    if args.source_sha != head or dirty or args.journal.resolve().is_relative_to(root):
        raise EvaluationBudgetError
    database_url = configure_integration_test_database()
    from sqlalchemy.engine import make_url

    # Deliberately separate paid reservations from pytest's disposable database.
    if make_url(database_url).database != "familycare_fixed_review_live_test":
        raise EvaluationBudgetError
    cases = load_cases(DEFAULT_CASES)
    if len(cases) != 20:
        raise EvaluationBudgetError
    source = hashlib.sha256(head.encode() + DEFAULT_CASES.read_bytes()).hexdigest()
    with psycopg.connect(
        database_url.replace("postgresql+psycopg://", "postgresql://"), autocommit=True
    ) as owner:
        row = owner.execute(
            "SELECT pg_try_advisory_lock(hashtextextended('fixed-review-evaluation-v1',0))"
        ).fetchone()
        if row is None or not row[0]:
            raise EvaluationBudgetError
        with EvaluationJournal(args.journal, source=source, limit=args.maximum_usd) as journal:
            journal.record["git_source_sha"] = head
            samples = _prepare(journal, cases, database_url)
            _reconcile(journal, samples, database_url)
            if args.mode == "run":
                _run(journal, cases, samples, database_url)
            print(json.dumps(_report(journal, cases), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit("FIXED_REVIEW_EVALUATION_STOPPED") from None
