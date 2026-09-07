#!/usr/bin/env python3
"""Score independent, wholly synthetic claim-guidance expectations and predictions."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

MAX_CASES = 1000
MAX_CANDIDATES = 128
MAX_INPUT_BYTES = 1024 * 1024
ID_PATTERN = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
MIN_RECALL = 0.90
MIN_PRIMARY_PRECISION = 0.90
MIN_CONDITIONAL_PRECISION = 0.90
MAX_UNRELATED_EVENT_RATE = 0.05
MAX_UNNECESSARY_HOLD_RATE = 0.05
DEFAULT_CASES = (
    Path(__file__).resolve().parents[1] / "fixtures/synthetic/claim-guidance/cases.v1.json"
)


def _identifier(value: object) -> str:
    if not isinstance(value, str) or ID_PATTERN.fullmatch(value) is None:
        raise ValueError("invalid identifier")
    return value


def _identifiers(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_CANDIDATES:
        raise ValueError("invalid candidate collection")
    items = tuple(_identifier(item) for item in value)
    if len(set(items)) != len(items):
        raise ValueError("duplicate candidate")
    return items


def _record(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("invalid record")
    return value


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    split: str
    contract_group: str
    scenario_id: str
    candidate_pool: tuple[str, ...]
    expected_primary: tuple[str, ...]
    expected_conditional: tuple[str, ...]
    answerable: bool
    scenario_parameters: Mapping[str, object]

    def __post_init__(self) -> None:
        _identifier(self.case_id)
        _identifier(self.contract_group)
        if self.split not in ("dev", "holdout"):
            raise ValueError("invalid split")
        if self.scenario_id not in {f"S{index:02d}" for index in range(1, 15)}:
            raise ValueError("invalid scenario")
        pool = set(_identifiers(self.candidate_pool))
        primary = set(_identifiers(self.expected_primary))
        conditional = set(_identifiers(self.expected_conditional))
        if primary & conditional or not (primary | conditional) <= pool:
            raise ValueError("ambiguous or unknown expected candidate")
        if not isinstance(self.answerable, bool):
            raise ValueError("invalid answerable flag")
        if not isinstance(self.scenario_parameters, Mapping):
            raise ValueError("invalid scenario parameters")

    @classmethod
    def from_record(cls, value: object) -> BenchmarkCase:
        record = _record(value)
        fields = {
            "case_id",
            "split",
            "contract_group",
            "scenario_id",
            "candidate_pool",
            "expected_primary",
            "expected_conditional",
            "answerable",
            "scenario_parameters",
        }
        if set(record) != fields:
            raise ValueError("invalid case fields")
        split, scenario, answerable = record["split"], record["scenario_id"], record["answerable"]
        if (
            not isinstance(split, str)
            or not isinstance(scenario, str)
            or not isinstance(answerable, bool)
        ):
            raise ValueError("invalid case field types")
        return cls(
            _identifier(record["case_id"]),
            split,
            _identifier(record["contract_group"]),
            scenario,
            _identifiers(record["candidate_pool"]),
            _identifiers(record["expected_primary"]),
            _identifiers(record["expected_conditional"]),
            answerable,
            _record(record["scenario_parameters"]),
        )


@dataclass(frozen=True)
class Prediction:
    case_id: str
    primary: tuple[str, ...]
    conditional: tuple[str, ...]
    held: bool = False

    def validate(self, case: BenchmarkCase) -> None:
        _identifier(self.case_id)
        primary = set(_identifiers(self.primary))
        conditional = set(_identifiers(self.conditional))
        if primary & conditional or not (primary | conditional) <= set(case.candidate_pool):
            raise ValueError("ambiguous or unknown predicted candidate")
        if not isinstance(self.held, bool) or (self.held and (primary or conditional)):
            raise ValueError("invalid hold flag")

    @classmethod
    def from_record(cls, value: object) -> Prediction:
        record = _record(value)
        if not {"case_id", "primary", "conditional"} <= set(record) or set(record) - {
            "case_id",
            "primary",
            "conditional",
            "held",
        }:
            raise ValueError("invalid prediction fields")
        held = record.get("held", False)
        if not isinstance(held, bool):
            raise ValueError("invalid hold flag")
        return cls(
            _identifier(record["case_id"]),
            _identifiers(record["primary"]),
            _identifiers(record["conditional"]),
            held,
        )


@dataclass(frozen=True)
class Metric:
    numerator: int
    denominator: int

    @property
    def value(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None

    def as_dict(self) -> dict[str, int | float | None]:
        return {"numerator": self.numerator, "denominator": self.denominator, "value": self.value}


@dataclass(frozen=True)
class BenchmarkResult:
    case_count: int
    output_count: int
    recall: Metric
    primary_precision: Metric
    conditional_precision: Metric
    unrelated_event_false_recommendation_rate: Metric
    unnecessary_hold_rate: Metric
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures

    def as_dict(self) -> dict[str, object]:
        return {
            "case_count": self.case_count,
            "output_count": self.output_count,
            "recall": self.recall.as_dict(),
            "primary_precision": self.primary_precision.as_dict(),
            "conditional_precision": self.conditional_precision.as_dict(),
            "unrelated_event_false_recommendation_rate": (
                self.unrelated_event_false_recommendation_rate.as_dict()
            ),
            "unnecessary_hold_rate": self.unnecessary_hold_rate.as_dict(),
            "failures": list(self.failures),
            "passed": self.passed,
        }


def _validate_cases(cases: Sequence[BenchmarkCase]) -> None:
    if not 1 <= len(cases) <= MAX_CASES:
        raise ValueError("invalid case count")
    case_ids: set[str] = set()
    groups: dict[str, str] = {}
    candidate_splits: dict[str, str] = {}
    for case in cases:
        case.__post_init__()
        if case.case_id in case_ids:
            raise ValueError("duplicate case")
        case_ids.add(case.case_id)
        if groups.setdefault(case.contract_group, case.split) != case.split:
            raise ValueError("contract group overlaps splits")
        for candidate in case.candidate_pool:
            if candidate_splits.setdefault(candidate, case.split) != case.split:
                raise ValueError("candidate identity overlaps splits")


def score_predictions(
    cases: Sequence[BenchmarkCase],
    predictions: Sequence[Prediction],
    *,
    split: str | None = None,
) -> BenchmarkResult:
    """Validate the entire input, then micro-average candidates and macro-count events."""
    _validate_cases(cases)
    if split is not None and split not in ("dev", "holdout"):
        raise ValueError("invalid split")
    if len(predictions) != len(cases):
        raise ValueError("missing or extra predictions")
    case_map = {case.case_id: case for case in cases}
    prediction_map: dict[str, Prediction] = {}
    for prediction in predictions:
        if prediction.case_id not in case_map or prediction.case_id in prediction_map:
            raise ValueError("unknown or duplicate predicted case")
        prediction.validate(case_map[prediction.case_id])
        prediction_map[prediction.case_id] = prediction
    selected = [case for case in cases if split is None or case.split == split]
    if not selected:
        raise ValueError("empty selected split")
    relevant_count = recovered = primary_count = primary_correct = 0
    conditional_count = conditional_correct = expected_primary_count = (
        expected_conditional_count
    ) = 0
    unrelated_count = unrelated_recommended = answerable_count = unnecessary_holds = 0
    for case in selected:
        prediction = prediction_map[case.case_id]
        expected_primary, expected_conditional = (
            set(case.expected_primary),
            set(case.expected_conditional),
        )
        expected = expected_primary | expected_conditional
        primary, conditional = set(prediction.primary), set(prediction.conditional)
        emitted = primary | conditional
        relevant_count += len(expected)
        recovered += len(expected & emitted)
        primary_count += len(primary)
        primary_correct += len(primary & expected_primary)
        conditional_count += len(conditional)
        conditional_correct += len(conditional & expected_conditional)
        expected_primary_count += len(expected_primary)
        expected_conditional_count += len(expected_conditional)
        if not expected:
            unrelated_count += 1
            unrelated_recommended += bool(emitted)
        if case.answerable:
            answerable_count += 1
            unnecessary_holds += prediction.held or bool(expected and not expected & emitted)
    recall = Metric(recovered, relevant_count)
    primary_precision = Metric(primary_correct, primary_count)
    conditional_precision = Metric(conditional_correct, conditional_count)
    unrelated_rate = Metric(unrelated_recommended, unrelated_count)
    hold_rate = Metric(unnecessary_holds, answerable_count)
    failures = []
    for name, metric, minimum, expected_count in (
        ("recall", recall, MIN_RECALL, relevant_count),
        ("primary_precision", primary_precision, MIN_PRIMARY_PRECISION, expected_primary_count),
        (
            "conditional_precision",
            conditional_precision,
            MIN_CONDITIONAL_PRECISION,
            expected_conditional_count,
        ),
    ):
        if (metric.value is None and expected_count) or (
            metric.value is not None and metric.value < minimum
        ):
            failures.append(name)
    for name, metric, maximum in (
        ("unrelated_event_false_recommendation_rate", unrelated_rate, MAX_UNRELATED_EVENT_RATE),
        ("unnecessary_hold_rate", hold_rate, MAX_UNNECESSARY_HOLD_RATE),
    ):
        if metric.value is not None and metric.value > maximum:
            failures.append(name)
    return BenchmarkResult(
        len(selected),
        primary_count + conditional_count,
        recall,
        primary_precision,
        conditional_precision,
        unrelated_rate,
        hold_rate,
        tuple(failures),
    )


def negative_controls(cases: Sequence[BenchmarkCase]) -> dict[str, tuple[Prediction, ...]]:
    """Return intentionally broken baselines; never call these engine predictions."""
    _validate_cases(cases)
    return {
        "all_hold": tuple(Prediction(case.case_id, (), (), held=True) for case in cases),
        "all_primary": tuple(Prediction(case.case_id, case.candidate_pool, ()) for case in cases),
        "all_conditional": tuple(
            Prediction(case.case_id, (), case.candidate_pool) for case in cases
        ),
    }


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _load_records(path: Path) -> list[object]:
    with path.open("rb") as stream:
        content = stream.read(MAX_INPUT_BYTES + 1)
    if len(content) > MAX_INPUT_BYTES:
        raise ValueError("input too large")
    records: object = json.loads(content, object_pairs_hook=_unique_object)
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_CASES:
        raise ValueError("invalid input collection")
    return records


def load_cases(path: Path) -> tuple[BenchmarkCase, ...]:
    cases = tuple(BenchmarkCase.from_record(record) for record in _load_records(path))
    _validate_cases(cases)
    return cases


def load_predictions(path: Path) -> tuple[Prediction, ...]:
    return tuple(Prediction.from_record(record) for record in _load_records(path))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--split", choices=("dev", "holdout"))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--predictions", type=Path)
    mode.add_argument("--negative-controls", action="store_true")
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.cases)
        if args.negative_controls:
            reports = {
                name: score_predictions(cases, predictions, split=args.split)
                for name, predictions in negative_controls(cases).items()
            }
            print(
                json.dumps(
                    {name: report.as_dict() for name, report in reports.items()}, sort_keys=True
                )
            )
            return int(any(report.passed for report in reports.values()))
        report = score_predictions(cases, load_predictions(args.predictions), split=args.split)
        print(json.dumps(report.as_dict(), sort_keys=True))
        return int(not report.passed)
    except OSError, ValueError, TypeError, RecursionError:
        # Input content and paths may not be safe to echo in logs.
        print(json.dumps({"error": "invalid-benchmark-input"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
