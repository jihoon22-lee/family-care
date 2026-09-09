"""Interrupted evaluations recover the recorded job without another provider call."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from familycare_worker.guidance_review_jobs import ReviewQueueUnavailable

from scripts import run_fixed_review_evaluation as evaluation
from scripts.fixed_review_evaluation import EvaluationBudgetError, EvaluationJournal


def test_candidate_agreement_is_not_scored_as_improvement_and_errors_keep_direction() -> None:
    from scripts.claim_guidance_benchmark import DEFAULT_CASES, Prediction, load_cases

    cases = load_cases(DEFAULT_CASES)
    correct = [Prediction(c.case_id, c.expected_primary, c.expected_conditional) for c in cases]
    assert evaluation._candidate_effects(cases, correct, correct) == {
        "corrected_candidate_labels": 0,
        "new_candidate_errors": 0,
        "changed_but_still_wrong": 0,
    }
    case = cases[0]
    assert case.expected_conditional
    wrong = [
        Prediction(case.case_id, (*case.expected_primary, *case.expected_conditional), ()),
        *correct[1:],
    ]
    fixed = evaluation._candidate_effects(cases, wrong, correct)
    broken = evaluation._candidate_effects(cases, correct, wrong)
    assert fixed["corrected_candidate_labels"] == len(case.expected_conditional)
    assert fixed["new_candidate_errors"] == 0
    assert broken["corrected_candidate_labels"] == 0
    assert broken["new_candidate_errors"] == len(case.expected_conditional)


def test_uncomputed_amounts_stay_in_the_denominator_and_currency_errors_are_separate() -> None:
    from scripts.claim_guidance_benchmark import DEFAULT_CASES, load_cases

    original = load_cases(DEFAULT_CASES)[0]
    key = original.expected_conditional[0]
    case = replace(
        original,
        scenario_parameters={
            "coverages": [
                {"coverage_key": key, "calculation": {"expected_amount": "100"}},
            ]
        },
    )
    missing = {key: {"estimate": {"kind": "FORMULA"}}}
    correct = {key: {"estimate": {"kind": "POINT", "amount": "100", "currency": "TST"}}}
    wrong = {key: {"estimate": {"kind": "POINT", "amount": "100", "currency": "KRW"}}}
    absent = evaluation._amount_metrics(case, missing)
    assert absent["expected_points"] == 1 and absent["uncomputed_expected_points"] == 1
    assert absent["formulas"] == 1 and absent["incorrect_points"] == 0
    assert evaluation._amount_metrics(case, wrong)["incorrect_points"] == 1
    assert evaluation._amount_effects(case, correct, correct) == {
        "recovered_or_corrected_points": 0,
        "new_incorrect_points": 0,
        "lost_correct_points": 0,
    }
    assert evaluation._amount_effects(case, missing, correct)["recovered_or_corrected_points"] == 1
    assert evaluation._amount_effects(case, correct, wrong)["new_incorrect_points"] == 1
    assert evaluation._amount_effects(case, correct, missing)["lost_correct_points"] == 1


def _sample() -> Any:
    return SimpleNamespace(
        scope=SimpleNamespace(),
        event=SimpleNamespace(id=UUID(int=1), version=2),
        original=SimpleNamespace(run_id=UUID(int=3)),
        coverage_keys={UUID(int=4): "synthetic-coverage"},
    )


def _review() -> Any:
    return SimpleNamespace(
        id=UUID(int=5),
        medical_event_id=UUID(int=1),
        decision_run_id=UUID(int=3),
        event_version=2,
        state="completed",
        error_code=None,
        usage=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        completed_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=2),
        result=SimpleNamespace(
            findings=(),
            scope=SimpleNamespace(model_dump=lambda **kwargs: {}),
            guidance=SimpleNamespace(
                outcome="CANDIDATES_AVAILABLE",
                candidates=(
                    SimpleNamespace(
                        group="PRIMARY",
                        ref=SimpleNamespace(coverage_id=UUID(int=4)),
                        condition_result="MATCH",
                        cases=(),
                        estimate=SimpleNamespace(
                            model_dump=lambda **kwargs: {"kind": "UNAVAILABLE"}
                        ),
                    ),
                ),
            ),
        ),
    )


def test_queue_job_mismatch_is_rejected_before_loading_provider_inputs() -> None:
    queue = evaluation.ExpectedReviewQueue(
        "postgresql://synthetic:synthetic@localhost/synthetic_test"
    )
    queue.expected_job_id = UUID(int=1)
    with pytest.raises(ReviewQueueUnavailable):
        queue.load_inputs(cast(Any, SimpleNamespace(id=UUID(int=2))))


def test_completed_database_result_is_recovered_without_enqueue_or_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recovered = []

    class Repository:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def get_job(self, scope: Any, job_id: UUID) -> Any:
            assert job_id == UUID(int=5)
            recovered.append(job_id)
            return _review()

    monkeypatch.setattr(evaluation, "GuidanceReviewRepository", Repository)
    monkeypatch.setattr(
        evaluation,
        "GuidanceReviewProjector",
        lambda url: SimpleNamespace(
            project_pending=lambda **kwargs: 0,
        ),
    )
    with EvaluationJournal(
        tmp_path / "journal.json", source="a" * 64, limit=Decimal("1")
    ) as journal:
        journal.record["cases"]["synthetic-case-a"] = {
            "review_job_id": str(UUID(int=5)),
            "state": "enqueued",
        }
        evaluation._reconcile(journal, {"synthetic-case-a": _sample()}, "synthetic")
        result = journal.record["cases"]["synthetic-case-a"]
        assert result["state"] == "completed"
        assert result["reviewed"]["primary"] == ("synthetic-coverage",)
        assert journal.budget.request_count == 0
    assert recovered == [UUID(int=5)]


@pytest.mark.parametrize("changed", ["medical_event_id", "decision_run_id", "event_version", "id"])
def test_result_identity_mismatch_cannot_be_scored_as_this_case(
    tmp_path: Path, changed: str
) -> None:
    with EvaluationJournal(
        tmp_path / "journal.json", source="a" * 64, limit=Decimal("1")
    ) as journal:
        journal.record["cases"]["synthetic-case-a"] = {
            "review_job_id": str(UUID(int=5)),
            "state": "enqueued",
        }
        reviewed = _review()
        setattr(reviewed, changed, 99 if changed == "event_version" else UUID(int=99))
        with pytest.raises(EvaluationBudgetError):
            evaluation._collect(journal, "synthetic-case-a", _sample(), reviewed)
        assert "reviewed" not in journal.record["cases"]["synthetic-case-a"]
