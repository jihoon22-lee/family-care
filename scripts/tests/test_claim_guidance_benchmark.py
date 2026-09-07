from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.claim_guidance_benchmark import (
    MAX_CASES,
    BenchmarkCase,
    Prediction,
    load_cases,
    load_predictions,
    negative_controls,
    score_predictions,
)

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/synthetic/claim-guidance/cases.v1.json"


def _case(**changes: object) -> BenchmarkCase:
    values: dict[str, object] = {
        "case_id": "synthetic-case-a",
        "split": "dev",
        "contract_group": "synthetic-contract-a",
        "scenario_id": "S01",
        "candidate_pool": ("coverage-a", "coverage-b", "coverage-c"),
        "expected_primary": ("coverage-a",),
        "expected_conditional": ("coverage-b",),
        "answerable": True,
        "scenario_parameters": {},
    }
    values.update(changes)
    # Exercise the public JSON parser rather than bypassing its runtime validation.
    return BenchmarkCase.from_record(values)


def test_scores_independent_expectations_and_predictions() -> None:
    report = score_predictions(
        [_case()], [Prediction("synthetic-case-a", ("coverage-a",), ("coverage-c",))]
    )
    assert report.recall.numerator == 1
    assert report.recall.denominator == 2
    assert report.recall.value == 0.5
    assert report.primary_precision.value == 1.0
    assert report.conditional_precision.value == 0.0
    assert report.output_count == 2
    assert report.unrelated_event_false_recommendation_rate.value is None
    assert report.unnecessary_hold_rate.value == 0.0
    assert not report.passed
    assert set(report.failures) == {"recall", "conditional_precision"}


def test_group_precision_rejects_conditional_promoted_to_primary() -> None:
    report = score_predictions(
        [_case()], [Prediction("synthetic-case-a", ("coverage-a", "coverage-b"), ())]
    )
    assert report.recall.value == 1.0
    assert report.primary_precision.value == 0.5
    assert report.conditional_precision.value is None
    assert "conditional_precision" in report.failures


def test_undefined_denominators_are_not_fabricated_success_rates() -> None:
    case = _case(expected_primary=(), expected_conditional=())
    report = score_predictions([case], [Prediction(case.case_id, (), ())])
    assert report.recall.value is None
    assert report.primary_precision.value is None
    assert report.conditional_precision.value is None
    assert report.unrelated_event_false_recommendation_rate.value == 0.0
    assert report.passed


def test_unrelated_case_and_unnecessary_holds_have_case_denominators() -> None:
    cases = [
        _case(),
        _case(case_id="synthetic-case-b", expected_primary=(), expected_conditional=()),
    ]
    report = score_predictions(
        cases,
        [Prediction(cases[0].case_id, (), ()), Prediction(cases[1].case_id, (), ("coverage-c",))],
    )
    assert report.unrelated_event_false_recommendation_rate.numerator == 1
    assert report.unrelated_event_false_recommendation_rate.denominator == 1
    assert report.unnecessary_hold_rate.numerator == 1
    assert report.unnecessary_hold_rate.denominator == 2


def test_irrelevant_output_does_not_hide_an_unnecessary_hold() -> None:
    report = score_predictions([_case()], [Prediction("synthetic-case-a", ("coverage-c",), ())])
    assert report.unnecessary_hold_rate.value == 1.0


@pytest.mark.parametrize(
    "changes",
    [
        {"case_id": "bad id"},
        {"case_id": "a" * 65},
        {"split": "unknown"},
        {"scenario_id": "S99"},
        {"candidate_pool": ("coverage-a", "coverage-a")},
        {"candidate_pool": tuple(f"coverage-{index}" for index in range(129))},
        {"expected_primary": ("missing",)},
        {"expected_primary": ("coverage-a", "coverage-a")},
        {"expected_conditional": ("coverage-a",)},
        {"answerable": "true"},
        {"contract_group": ""},
    ],
)
def test_rejects_malformed_expected_records(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _case(**changes)


@pytest.mark.parametrize(
    "predictions",
    [
        [],
        [Prediction("unknown-case", (), ())],
        [Prediction("synthetic-case-a", (), ()), Prediction("synthetic-case-a", (), ())],
        [Prediction("synthetic-case-a", ("missing",), ())],
        [Prediction("synthetic-case-a", ("coverage-a", "coverage-a"), ())],
        [Prediction("synthetic-case-a", ("coverage-a",), ("coverage-a",))],
        [Prediction("synthetic-case-a", ("coverage-a",), (), held=True)],
    ],
)
def test_rejects_missing_unknown_duplicate_and_ambiguous_predictions(
    predictions: list[Prediction],
) -> None:
    with pytest.raises(ValueError):
        score_predictions([_case()], predictions)


def test_rejects_duplicate_cases_and_contract_leakage_across_splits() -> None:
    case = _case()
    for other in (case, replace(case, case_id="synthetic-case-b", split="holdout")):
        with pytest.raises(ValueError):
            score_predictions([case, other], [])


def test_rejects_empty_and_unbounded_case_collections() -> None:
    with pytest.raises(ValueError):
        score_predictions([], [])
    with pytest.raises(ValueError):
        score_predictions([_case()] * (MAX_CASES + 1), [])


def test_validates_full_inputs_before_scoring_a_split() -> None:
    cases = [_case(), _case(case_id="synthetic-case-b", split="holdout", contract_group="group-b")]
    with pytest.raises(ValueError):
        score_predictions(cases, [Prediction("synthetic-case-a", (), ())], split="dev")


def test_fixture_splits_and_all_three_negative_controls() -> None:
    cases = load_cases(FIXTURE)
    assert {case.scenario_id for case in cases} == {
        "S01",
        "S02",
        "S05",
        "S06",
        "S07",
        "S08",
        "S09",
        "S10",
        "S11",
    }
    dev_groups = {case.contract_group for case in cases if case.split == "dev"}
    holdout_groups = {case.contract_group for case in cases if case.split == "holdout"}
    assert dev_groups.isdisjoint(holdout_groups)
    controls = negative_controls(cases)
    assert set(controls) == {"all_hold", "all_primary", "all_conditional"}
    for split in (None, "dev", "holdout"):
        for predictions in controls.values():
            assert not score_predictions(cases, predictions, split=split).passed


def test_oracle_is_only_scorer_calibration_and_passes_each_split() -> None:
    cases = load_cases(FIXTURE)
    oracle = [
        Prediction(case.case_id, case.expected_primary, case.expected_conditional) for case in cases
    ]
    for split in (None, "dev", "holdout"):
        report = score_predictions(cases, oracle, split=split)
        assert report.passed
        assert report.recall.value == 1.0
        assert report.primary_precision.value == 1.0
        assert report.conditional_precision.value == 1.0


def test_json_loaders_reject_duplicate_keys_and_unknown_prediction_fields(tmp_path: Path) -> None:
    file = tmp_path / "synthetic-input.json"
    file.write_text('[{"case_id":"a","case_id":"b"}]', encoding="utf-8")
    with pytest.raises(ValueError):
        load_predictions(file)
    file.write_text(json.dumps([{"case_id": "a", "primary": [], "conditional": [], "extra": 1}]))
    with pytest.raises(ValueError):
        load_predictions(file)
