"""Run the real local evaluator against a separately authored expectation set."""

from __future__ import annotations

from scripts.claim_guidance_benchmark import DEFAULT_CASES, load_cases, score_predictions
from scripts.run_claim_guidance_benchmark import predict_cases


def test_local_guidance_meets_frozen_candidate_targets_in_both_splits() -> None:
    cases = load_cases(DEFAULT_CASES)
    predictions = predict_cases(cases, engine="local")
    for split in ("dev", "holdout"):
        result = score_predictions(cases, predictions, split=split)
        assert result.passed, result.as_dict()


def test_expectation_labels_do_not_change_engine_predictions() -> None:
    from dataclasses import replace

    cases = load_cases(DEFAULT_CASES)
    original = predict_cases(cases, engine="local")
    changed_labels = tuple(
        replace(case, expected_primary=(), expected_conditional=()) for case in cases
    )
    assert predict_cases(changed_labels, engine="local") == original
