"""Accounting boundaries for an explicitly invoked, synthetic-only review evaluation."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import RLock
from time import monotonic
from typing import Any

import httpx2

from scripts.claim_guidance_benchmark import ID_PATTERN

MODEL = "gpt-5.6-terra"
PRICING_DATE = "2026-09-09"
PRICING_URL = "https://developers.openai.com/api/docs/pricing"
_INPUT = Decimal("2")
_CACHED = Decimal("0.20")
_WRITE = Decimal("2.50")
_OUTPUT = Decimal("12")
_MILLION = Decimal(1_000_000)


class EvaluationBudgetError(ValueError):
    """A fixed diagnostic that contains no request or credential material."""

    def __init__(self) -> None:
        super().__init__("FIXED_REVIEW_EVALUATION_BUDGET_INVALID")


def _amount(value: object, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise EvaluationBudgetError
    if positive and value == 0:
        raise EvaluationBudgetError
    return value


def _tokens(value: object, maximum: int = 2**63 - 1) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise EvaluationBudgetError
    return value


def usage_cost(
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    cache_write_input_tokens: int = 0,
) -> Decimal:
    """Price known, disjoint usage counters at the recorded Standard rates, in USD.

    Callers must retain an unknown charge when usage or service tier is unknown;
    this calculation is a token-price estimate, not an invoice or balance lookup.
    Reasoning tokens are already included in output_tokens.
    """
    total = _tokens(input_tokens)
    output = _tokens(output_tokens)
    cached = _tokens(cached_input_tokens)
    written = _tokens(cache_write_input_tokens)
    if cached + written > total:
        raise EvaluationBudgetError
    return (
        (total - cached - written) * _INPUT + cached * _CACHED + written * _WRITE + output * _OUTPUT
    ) / _MILLION


def _bound(input_bound: int, output_bound: int) -> Decimal:
    # Even if every input token were a charged cache write, the cap holds.
    return (
        _tokens(input_bound, 32768) * max(_INPUT, _CACHED, _WRITE)
        + _tokens(output_bound, 4000) * _OUTPUT
    ) / _MILLION


@dataclass
class _Reservation:
    input_bound: int
    output_bound: int
    settled: bool = False
    actual: Decimal | None = None

    @property
    def committed(self) -> Decimal:
        return (
            self.actual if self.actual is not None else _bound(self.input_bound, self.output_bound)
        )


class EvaluationBudget:
    """Reserve before transmission; callers durably save each change under a file lock."""

    def __init__(self, limit: Decimal, *, maximum_requests: int = 20) -> None:
        self.limit = _amount(limit, positive=True)
        if type(maximum_requests) is not int or not 1 <= maximum_requests <= 20:
            raise EvaluationBudgetError
        self.maximum_requests = maximum_requests
        self._reservations: dict[str, _Reservation] = {}
        self._blocked = False

    @property
    def committed_cost(self) -> Decimal:
        return sum((entry.committed for entry in self._reservations.values()), Decimal(0))

    @property
    def request_count(self) -> int:
        return len(self._reservations)

    def reserve(self, case_id: str, *, input_bound: int, output_bound: int) -> None:
        if (
            not isinstance(case_id, str)
            or ID_PATTERN.fullmatch(case_id) is None
            or not case_id.startswith("synthetic-")
            or self._blocked
            or case_id in self._reservations
            or self.request_count >= self.maximum_requests
            or self.committed_cost + _bound(input_bound, output_bound) > self.limit
        ):
            raise EvaluationBudgetError
        self._reservations[case_id] = _Reservation(input_bound, output_bound)

    def settle(self, case_id: str, actual: Decimal | None) -> None:
        entry = self._reservations.get(case_id)
        if entry is None or entry.settled:
            raise EvaluationBudgetError
        if actual is not None:
            _amount(actual)
            if actual > _bound(entry.input_bound, entry.output_bound):
                self._blocked = True
        entry.actual, entry.settled = actual, True

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": "fixed-review-budget-v1",
            "model": MODEL,
            "pricing_date": PRICING_DATE,
            "limit_usd": str(self.limit),
            "maximum_requests": self.maximum_requests,
            "blocked": self._blocked,
            "reservations": {
                key: {
                    "input_bound": value.input_bound,
                    "output_bound": value.output_bound,
                    "settled": value.settled,
                    "actual_usd": str(value.actual) if value.actual is not None else None,
                }
                for key, value in self._reservations.items()
            },
        }

    @classmethod
    def from_record(cls, value: object) -> EvaluationBudget:
        try:
            if (
                not isinstance(value, dict)
                or set(value)
                != {
                    "schema_version",
                    "model",
                    "pricing_date",
                    "limit_usd",
                    "maximum_requests",
                    "blocked",
                    "reservations",
                }
                or value["schema_version"] != "fixed-review-budget-v1"
                or value["model"] != MODEL
                or value["pricing_date"] != PRICING_DATE
                or not isinstance(value["limit_usd"], str)
                or type(value["blocked"]) is not bool
                or not isinstance(value["reservations"], dict)
            ):
                raise EvaluationBudgetError
            result = cls(Decimal(value["limit_usd"]), maximum_requests=value["maximum_requests"])
            result._blocked = value["blocked"]
            for key, raw in value["reservations"].items():
                if (
                    not isinstance(key, str)
                    or ID_PATTERN.fullmatch(key) is None
                    or not key.startswith("synthetic-")
                    or not isinstance(raw, dict)
                    or set(raw) != {"input_bound", "output_bound", "settled", "actual_usd"}
                    or type(raw["settled"]) is not bool
                    or (raw["actual_usd"] is not None and not isinstance(raw["actual_usd"], str))
                ):
                    raise EvaluationBudgetError
                bound = _bound(raw["input_bound"], raw["output_bound"])
                actual = (
                    _amount(Decimal(raw["actual_usd"])) if raw["actual_usd"] is not None else None
                )
                if actual is not None and (
                    not raw["settled"] or (actual > bound and not result._blocked)
                ):
                    raise EvaluationBudgetError
                result._reservations[key] = _Reservation(
                    raw["input_bound"], raw["output_bound"], raw["settled"], actual
                )
            if result.request_count > result.maximum_requests or (
                result.committed_cost > result.limit and not result._blocked
            ):
                raise EvaluationBudgetError
            return result
        except InvalidOperation, KeyError, TypeError:
            raise EvaluationBudgetError from None


class EvaluationJournal:
    """Single-owner durable journal. A lost response never permits a second attempt."""

    def __init__(self, path: Path, *, source: str, limit: Decimal) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", source) is None:
            raise EvaluationBudgetError
        self.path, self.source, self.limit = path, source, limit
        self.budget = EvaluationBudget(limit)
        self.record: dict[str, Any] = {}
        self._lock: int | None = None
        self._writer = RLock()

    def __enter__(self) -> EvaluationJournal:
        self._lock = os.open(
            str(self.path) + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.path.is_symlink():
                raise EvaluationBudgetError
            if self.path.exists():
                self.record = json.loads(self.path.read_text())
                if self.record["source"] != self.source:
                    raise EvaluationBudgetError
                self.budget = EvaluationBudget.from_record(self.record["budget"])
                if self.budget.limit != self.limit or self.budget.maximum_requests != 20:
                    raise EvaluationBudgetError
            else:
                self.record = {"source": self.source, "wire": {}, "cases": {}}
                self.save()
            return self
        except BaseException:
            os.close(self._lock)
            self._lock = None
            raise

    def save(self) -> None:
        with self._writer:
            self._save()

    def _save(self) -> None:
        if self._lock is None:
            raise EvaluationBudgetError
        self.record["budget"] = self.budget.to_record()
        descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".evaluation-")
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(self.record, output, sort_keys=True, indent=2, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def __exit__(self, *args: object) -> None:
        # Closing seals this instance before another process can acquire its lock.
        # A late network response keeps its original full reservation on disk.
        with self._writer:
            if self._lock is not None:
                os.close(self._lock)
                self._lock = None

    def reserve_wire(self, case_id: str, wire: bytes) -> None:
        with self._writer:
            if self._lock is None:
                raise EvaluationBudgetError
            self.budget.reserve(case_id, input_bound=len(wire), output_bound=4000)
            self.record["wire"][case_id] = {
                "sha256": hashlib.sha256(wire).hexdigest(),
                "bytes": len(wire),
                "tier": "default",
            }
            self._save()

    def settle_wire(
        self,
        case_id: str,
        actual: Decimal | None,
        *,
        elapsed_seconds: float,
        response_received: bool,
    ) -> None:
        with self._writer:
            if self._lock is None:
                return
            self.budget.settle(case_id, actual)
            self.record["wire"][case_id].update(
                elapsed_seconds=round(elapsed_seconds, 6),
                response_received=response_received,
            )
            self._save()


class BudgetedReviewTransport(httpx2.BaseTransport):
    """Inspect final SDK wire bytes, persist a charge bound, then transmit once."""

    def __init__(self, journal: EvaluationJournal, inner: httpx2.BaseTransport) -> None:
        self.journal, self.inner = journal, inner
        self.case_id = ""
        self.rejection: str | None = None

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        try:
            return self._handle_request(request)
        except EvaluationBudgetError:
            self.rejection = self.rejection or "EVALUATION_WIRE_INVALID"
            raise

    def _handle_request(self, request: httpx2.Request) -> httpx2.Response:
        if request.method != "POST" or str(request.url) != "https://api.openai.com/v1/responses":
            raise EvaluationBudgetError
        body = json.loads(request.read())
        from familycare_worker.ai.guidance_reviewer import (
            REVIEW_INSTRUCTION,
            SCHEMA_NAME,
            guidance_review_schema,
        )

        if (
            not isinstance(body, dict)
            or set(body) != {"model", "store", "max_output_tokens", "instructions", "input", "text"}
            or body.get("model") != MODEL
            or body.get("store") is not False
            or type(body.get("max_output_tokens")) is not int
            or body["max_output_tokens"] != 4000
            or body.get("instructions") != REVIEW_INSTRUCTION
            or not isinstance(body.get("input"), str)
            or body.get("text")
            != {
                "format": {
                    "type": "json_schema",
                    "name": SCHEMA_NAME,
                    "schema": guidance_review_schema(),
                    "strict": True,
                }
            }
        ):
            raise EvaluationBudgetError
        body["service_tier"] = "default"
        wire = json.dumps(body, separators=(",", ":"), ensure_ascii=True).encode()
        if len(wire) > 32768:
            raise EvaluationBudgetError
        case_id = self.case_id
        try:
            self.journal.reserve_wire(case_id, wire)
        except EvaluationBudgetError:
            self.rejection = "EVALUATION_COST_OR_REQUEST_LIMIT"
            raise
        headers = dict(request.headers)
        headers.pop("content-length", None)
        outgoing = httpx2.Request(
            request.method,
            request.url,
            headers=headers,
            content=wire,
            extensions=request.extensions,
        )
        actual = None
        started, response_received = monotonic(), False
        try:
            response = self.inner.handle_request(outgoing)
            response_received = True
            response.read()
            try:
                from familycare_worker.ai.provider import _response_metadata

                metadata, invalid = _response_metadata(response.json())
                usage = metadata.usage
                if (
                    not invalid
                    and metadata.model == MODEL
                    and metadata.service_tier == "default"
                    and usage
                ):
                    actual = usage_cost(
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        cached_input_tokens=usage.cached_input_tokens or 0,
                        # Missing write detail cannot release a possibly charged write.
                        cache_write_input_tokens=(
                            usage.cache_write_input_tokens
                            if usage.cache_write_input_tokens is not None
                            else usage.input_tokens - (usage.cached_input_tokens or 0)
                        ),
                    )
            except ValueError, TypeError, AttributeError:
                pass
            return response
        finally:
            self.journal.settle_wire(
                case_id,
                actual,
                elapsed_seconds=monotonic() - started,
                response_received=response_received,
            )

    def close(self) -> None:
        self.inner.close()
