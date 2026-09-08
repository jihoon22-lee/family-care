"""Read versioned receipt costs in the caller's transaction, never benefit amounts.

Receipt rows contain no document-Evidence link. References therefore identify the
actual receipt row, or the actual event plus its receipt-set digest for a large
aggregate. They must never be presented as independently verified PDF evidence.
Known costs, full bucket costs, absent items and unavailable amounts are distinct.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Context, Decimal, localcontext
from typing import Any, Literal, cast
from uuid import UUID

import psycopg

from familycare_api.common.scope import HouseholdScope
from familycare_api.guidance.calculation_runtime import CalculationSourceRef

EXPENSE_READER_REVISION = "guidance-expenses-v1"
MAX_EXPENSE_LINES = 256
_MAX_AMOUNT = Decimal("1e16")
_CURRENCY = re.compile(r"[A-Z]{3}")
_NOTE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_CATEGORIES = frozenset({"inpatient", "outpatient", "pharmacy"})
_COVERAGE = frozenset({"covered", "excluded", "possible_excluded", "unknown"})
_CONFIRMATION = frozenset({"user", "ai_structured", "unconfirmed"})
_SOURCE_FIELDS = (
    "id",
    "household_space_id",
    "medical_event_id",
    "version",
    "category",
    "coverage_category",
    "amount",
    "currency",
    "confirmation_level",
    "note_code",
    "deleted_at",
)
type ExpenseBucket = Literal["COVERED", "EXCLUDED", "COVERAGE_REVIEW", "UNCONFIRMED"]
type ExpenseReadStatus = Literal["AVAILABLE", "EMPTY", "PARTIAL", "UNAVAILABLE"]


@dataclass(frozen=True, slots=True, repr=False)
class ExpenseLine:
    line_id: UUID
    version: int
    category: str | None
    currency: str | None
    amount: Decimal | None
    confirmation_level: str | None
    coverage_category: str | None
    note_code: str | None
    bucket: ExpenseBucket
    source_refs: tuple[CalculationSourceRef, ...]
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class ExpenseCostGroup:
    """A cost bucket. known_cost is only the sum of its known-valued line IDs."""

    currency: str
    line_ids: tuple[UUID, ...]
    known_line_ids: tuple[UUID, ...]
    unknown_amount_line_ids: tuple[UUID, ...]
    known_cost: Decimal | None
    total_cost: Decimal | None
    source_refs: tuple[CalculationSourceRef, ...]
    unit: Literal["COST"] = "COST"


@dataclass(frozen=True, slots=True, repr=False)
class ExpenseCurrency:
    currency: str
    covered: ExpenseCostGroup
    excluded: ExpenseCostGroup
    coverage_review: ExpenseCostGroup
    unconfirmed: ExpenseCostGroup


@dataclass(frozen=True, slots=True, repr=False)
class ExpenseRead:
    event_id: UUID
    event_version: int | None
    expected_event_version: int
    status: ExpenseReadStatus
    lines: tuple[ExpenseLine, ...]
    currencies: tuple[ExpenseCurrency, ...]
    unassigned_line_ids: tuple[UUID, ...]
    digest_sha256: str
    source_refs: tuple[CalculationSourceRef, ...]
    reason_codes: tuple[str, ...]
    reader_revision: str = EXPENSE_READER_REVISION

    @property
    def review_required(self) -> bool:
        return self.status in {"PARTIAL", "UNAVAILABLE"} or any(
            line.bucket in {"COVERAGE_REVIEW", "UNCONFIRMED"} or line.reason_codes
            for line in self.lines
        )


def _digest(value: object) -> str:
    def scalar(item: object) -> str:
        if isinstance(item, datetime):
            return item.astimezone(UTC).isoformat()
        return str(item)

    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), default=scalar, allow_nan=False
        ).encode()
    ).hexdigest()


def _source_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: {"unsupported_float": repr(value)} if isinstance(value, float) else value
        for key in _SOURCE_FIELDS
        for value in (row.get(key),)
    }


def _unavailable(
    scope: HouseholdScope,
    event_id: UUID,
    expected: int,
    reason: str,
    actual: int | None = None,
    observed: object = None,
) -> ExpenseRead:
    digest = _digest(
        [
            EXPENSE_READER_REVISION,
            str(scope.household_space_id),
            str(event_id),
            expected,
            actual,
            reason,
            observed,
        ]
    )
    return ExpenseRead(event_id, actual, expected, "UNAVAILABLE", (), (), (), digest, (), (reason,))


def _choice(value: object, allowed: frozenset[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _amount(value: object) -> Decimal | None:
    if not isinstance(value, Decimal) or not value.is_finite() or not 0 <= value < _MAX_AMOUNT:
        return None
    if cast(int, value.as_tuple().exponent) < -2:
        return None
    return value


def _line(row: dict[str, Any]) -> ExpenseLine:
    reasons = []
    amount = _amount(row.get("amount"))
    if amount is None:
        reasons.append(
            "EXPENSE_AMOUNT_UNAVAILABLE" if row.get("amount") is None else "EXPENSE_AMOUNT_INVALID"
        )
    currency = row.get("currency")
    if not isinstance(currency, str) or _CURRENCY.fullmatch(currency) is None:
        reasons.append(
            "EXPENSE_CURRENCY_UNAVAILABLE" if currency is None else "EXPENSE_CURRENCY_INVALID"
        )
        currency = None
    category = _choice(row.get("category"), _CATEGORIES)
    confirmation = _choice(row.get("confirmation_level"), _CONFIRMATION)
    coverage = _choice(row.get("coverage_category"), _COVERAGE)
    if category is None:
        reasons.append("EXPENSE_CATEGORY_INVALID")
    if confirmation is None:
        reasons.append("EXPENSE_CONFIRMATION_INVALID")
    if coverage is None:
        reasons.append("EXPENSE_COVERAGE_INVALID")
    note = row.get("note_code")
    if note is not None and (not isinstance(note, str) or _NOTE.fullmatch(note) is None):
        reasons.append("EXPENSE_NOTE_INVALID")
        note = None
    bucket: ExpenseBucket = "UNCONFIRMED"
    if confirmation == "user" and category is not None:
        bucket = (
            "COVERED"
            if coverage == "covered"
            else "EXCLUDED"
            if coverage == "excluded"
            else "COVERAGE_REVIEW"
        )
    ref = CalculationSourceRef(
        "RECEIPT_LINE",
        row["id"],
        version=row["version"],
        digest_sha256=_digest([EXPENSE_READER_REVISION, _source_row(row)]),
    )
    return ExpenseLine(
        row["id"],
        row["version"],
        category,
        currency,
        amount,
        confirmation,
        coverage,
        note,
        bucket,
        (ref,),
        tuple(reasons),
    )


def _group(
    currency: str,
    bucket: ExpenseBucket,
    lines: tuple[ExpenseLine, ...],
    set_ref: CalculationSourceRef,
) -> ExpenseCostGroup:
    selected = tuple(line for line in lines if line.currency == currency and line.bucket == bucket)
    known = tuple(line for line in selected if line.amount is not None)
    unknown = tuple(line.line_id for line in selected if line.amount is None)
    context = Context(prec=40)
    for signal in context.traps:
        context.traps[signal] = True
    with localcontext(context):
        cost = sum((cast(Decimal, line.amount) for line in known), Decimal(0)) if known else None
    refs = tuple(ref for line in known for ref in line.source_refs)
    return ExpenseCostGroup(
        currency,
        tuple(line.line_id for line in selected),
        tuple(line.line_id for line in known),
        unknown,
        cost,
        cost if selected and not unknown else None,
        refs if len(refs) <= 64 else (set_ref,),
    )


def _read(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    event_id: UUID,
    expected: int,
) -> ExpenseRead:
    event = connection.execute(
        "/* expenses-event */ SELECT id,household_space_id,family_member_id,version,deleted_at "
        "FROM medical_events WHERE id=%s AND household_space_id=%s AND deleted_at IS NULL",
        (event_id, scope.household_space_id),
    ).fetchone()
    if (
        event is None
        or event["id"] != event_id
        or event["household_space_id"] != scope.household_space_id
        or event["deleted_at"] is not None
    ):
        return _unavailable(scope, event_id, expected, "EXPENSE_EVENT_UNAVAILABLE")
    actual = event["version"]
    if type(actual) is not int or actual < 1:
        return _unavailable(scope, event_id, expected, "EXPENSE_EVENT_VERSION_INVALID")
    if actual != expected:
        return _unavailable(scope, event_id, expected, "EXPENSE_EVENT_VERSION_MISMATCH", actual)
    rows = connection.execute(
        "/* expenses-lines */ SELECT r.id,r.household_space_id,r.medical_event_id,r.version,"
        "r.category,r.coverage_category,r.amount,r.currency,r.confirmation_level,"
        "r.note_code,r.deleted_at "
        "FROM receipt_lines r JOIN medical_events e ON e.id=r.medical_event_id "
        "AND e.household_space_id=r.household_space_id "
        "WHERE e.id=%s AND e.household_space_id=%s AND e.version=%s "
        "AND e.deleted_at IS NULL AND r.deleted_at IS NULL ORDER BY r.id LIMIT %s",
        (event_id, scope.household_space_id, actual, MAX_EXPENSE_LINES + 1),
    ).fetchall()
    if len(rows) > MAX_EXPENSE_LINES:
        return _unavailable(
            scope,
            event_id,
            expected,
            "EXPENSE_LINE_LIMIT_EXCEEDED",
            actual,
            [_source_row(row) for row in rows[: MAX_EXPENSE_LINES + 1]],
        )
    if any(
        row.get("household_space_id") != scope.household_space_id
        or row.get("medical_event_id") != event_id
        or row.get("deleted_at") is not None
        for row in rows
    ):
        return _unavailable(scope, event_id, expected, "EXPENSE_LINE_SCOPE_INVALID", actual)
    if any(
        not isinstance(row.get("id"), UUID)
        or row["id"].int == 0
        or type(row.get("version")) is not int
        or row["version"] < 1
        for row in rows
    ) or len({row["id"] for row in rows}) != len(rows):
        return _unavailable(scope, event_id, expected, "EXPENSE_LINE_IDENTITY_INVALID", actual)
    rows.sort(key=lambda row: str(row["id"]))
    digest = _digest(
        [
            EXPENSE_READER_REVISION,
            str(scope.household_space_id),
            event,
            [_source_row(row) for row in rows],
        ]
    )
    set_ref = CalculationSourceRef(
        "EVENT_RECEIPT_SET", event_id, version=actual, digest_sha256=digest
    )
    lines = tuple(_line(row) for row in rows)
    currencies = tuple(
        ExpenseCurrency(
            currency,
            *(
                _group(currency, bucket, lines, set_ref)
                for bucket in ("COVERED", "EXCLUDED", "COVERAGE_REVIEW", "UNCONFIRMED")
            ),
        )
        for currency in sorted({line.currency for line in lines if line.currency is not None})
    )
    reasons = list(dict.fromkeys(code for line in lines for code in line.reason_codes))
    if any(line.bucket == "COVERAGE_REVIEW" for line in lines):
        reasons.append("EXPENSE_COVERAGE_REVIEW_REQUIRED")
    if any(line.bucket == "UNCONFIRMED" for line in lines):
        reasons.append("EXPENSE_CONFIRMATION_REQUIRED")
    status: ExpenseReadStatus = (
        "EMPTY"
        if not lines
        else "PARTIAL"
        if any(line.reason_codes for line in lines)
        else "AVAILABLE"
    )
    return ExpenseRead(
        event_id,
        actual,
        expected,
        status,
        lines,
        currencies,
        tuple(line.line_id for line in lines if line.currency is None),
        digest,
        (set_ref,),
        tuple(reasons),
    )


def read_expenses(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    event_id: UUID,
    expected_event_version: int,
) -> ExpenseRead:
    """Read within the caller's RR snapshot; a nested savepoint isolates SQL failure.

    This function never opens another connection, changes isolation, commits the
    caller's transaction, updates a receipt, or reconstructs an old event version
    using today's costs. Archived reads must use their previously saved snapshot.
    """
    if (
        not isinstance(scope, HouseholdScope)
        or not isinstance(event_id, UUID)
        or event_id.int == 0
        or type(expected_event_version) is not int
        or expected_event_version < 1
    ):
        raise ValueError("EXPENSE_EVENT_SCOPE_INVALID")
    try:
        with connection.transaction():
            return _read(connection, scope, event_id, expected_event_version)
    except psycopg.Error:
        return _unavailable(scope, event_id, expected_event_version, "EXPENSE_SOURCE_UNAVAILABLE")
