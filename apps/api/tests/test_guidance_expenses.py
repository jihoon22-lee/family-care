"""Receipt costs retain actual field authority without becoming benefit estimates."""

from contextlib import nullcontext
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.guidance.expenses import MAX_EXPENSE_LINES, read_expenses


def uid(number):
    return UUID(int=number, version=4)


def line(number, amount="50000", *, coverage="covered", confirmation="user", currency="KRW"):
    return {
        "id": uid(number),
        "household_space_id": uid(1),
        "medical_event_id": uid(2),
        "version": 1,
        "category": "inpatient",
        "coverage_category": coverage,
        "amount": None if amount is None else Decimal(amount),
        "currency": currency,
        "confirmation_level": confirmation,
        "note_code": None,
        "deleted_at": None,
    }


class Rows:
    def __init__(self, values):
        self.values = values

    def fetchone(self):
        return self.values[0] if self.values else None

    def fetchall(self):
        return self.values


class ExpenseDatabase:
    def __init__(self, rows=()):
        self.event = {
            "id": uid(2),
            "household_space_id": uid(1),
            "family_member_id": uid(3),
            "version": 3,
            "deleted_at": None,
        }
        self.rows = list(rows)
        self.queries = []
        self.fail = False

    def transaction(self):
        return nullcontext()

    def execute(self, query, params):
        self.queries.append(query)
        if self.fail:
            raise psycopg.OperationalError("synthetic-error-detail-must-not-escape")
        if "expenses-event" in query:
            return Rows(
                [self.event]
                if params == (uid(2), uid(1)) and self.event["deleted_at"] is None
                else []
            )
        if "expenses-lines" in query:
            return Rows([row for row in self.rows if row["deleted_at"] is None])
        raise AssertionError("unexpected synthetic expense query")

    def read(self, *, scope=None, event_id=None, version=3):
        return read_expenses(self, scope or HouseholdScope(uid(1)), event_id or uid(2), version)


def test_user_covered_review_excluded_and_ai_costs_remain_separate():
    database = ExpenseDatabase(
        [
            line(10),
            line(11, "20000", coverage="unknown"),
            line(12, "5000", coverage="excluded"),
            line(13, "10000", confirmation="ai_structured"),
        ]
    )
    result = database.read()
    assert result.status == "AVAILABLE" and result.review_required
    costs = result.currencies[0]
    assert costs.currency == "KRW"
    assert costs.covered.total_cost == costs.covered.known_cost == 50000
    assert costs.coverage_review.total_cost == 20000
    assert costs.excluded.total_cost == 5000
    assert costs.unconfirmed.total_cost == 10000
    assert result.lines[3].confirmation_level == "ai_structured"
    assert result.lines[3].coverage_category == "covered"
    assert result.lines[3].source_refs[0].source_id == uid(13)
    assert result.lines[3].source_refs[0].version == 1
    assert result.lines[3].source_refs[0].source_kind == "RECEIPT_LINE"
    assert not hasattr(costs, "benefit_amount") and not hasattr(costs, "lower")
    assert "50000" not in repr(result)
    with pytest.raises(FrozenInstanceError):
        result.status = "EMPTY"


def test_known_zero_is_distinct_from_no_receipts_and_no_bucket_items():
    empty = ExpenseDatabase().read()
    assert empty.status == "EMPTY" and empty.lines == empty.currencies == ()
    zero = ExpenseDatabase([line(10, "0")]).read()
    assert zero.status == "AVAILABLE"
    assert zero.currencies[0].covered.known_cost == zero.currencies[0].covered.total_cost == 0
    assert zero.currencies[0].excluded.known_cost is zero.currencies[0].excluded.total_cost is None
    assert zero.digest_sha256 != empty.digest_sha256


@pytest.mark.parametrize("amount", [None, "NaN", "Infinity", "-1", "1.001", "1e16"])
def test_unavailable_amount_does_not_erase_other_known_covered_cost(amount):
    result = ExpenseDatabase([line(10), line(11, amount)]).read()
    group = result.currencies[0].covered
    assert result.status == "PARTIAL" and result.review_required
    assert group.known_cost == 50000 and group.total_cost is None
    assert group.unknown_amount_line_ids == (uid(11),)
    assert group.known_line_ids == (uid(10),)
    assert group.source_refs[0].source_id == uid(10)
    assert result.lines[1].amount is None


def test_currency_and_unassigned_currency_costs_are_not_mixed():
    bad = line(12, "7")
    bad["currency"] = None
    result = ExpenseDatabase([line(10, "50"), line(11, "10", currency="USD"), bad]).read()
    assert [(c.currency, c.covered.total_cost) for c in result.currencies] == [
        ("KRW", 50),
        ("USD", 10),
    ]
    assert result.unassigned_line_ids == (uid(12),)
    assert result.status == "PARTIAL" and "EXPENSE_CURRENCY_UNAVAILABLE" in result.reason_codes


@pytest.mark.parametrize("confirmation", ["ai_structured", "unconfirmed"])
@pytest.mark.parametrize("coverage", ["covered", "excluded", "possible_excluded", "unknown"])
def test_non_user_values_do_not_acquire_confirmed_coverage(confirmation, coverage):
    result = ExpenseDatabase([line(10, "10", coverage=coverage, confirmation=confirmation)]).read()
    groups = result.currencies[0]
    assert groups.unconfirmed.total_cost == 10
    assert (
        groups.covered.total_cost
        is groups.excluded.total_cost
        is groups.coverage_review.total_cost
        is None
    )
    assert result.lines[0].coverage_category == coverage


@pytest.mark.parametrize("fault", ["household", "event", "version", "deleted"])
def test_unavailable_event_or_wrong_version_does_not_read_receipt_rows(fault):
    database = ExpenseDatabase([line(10)])
    if fault == "household":
        result = database.read(scope=HouseholdScope(uid(99)))
    elif fault == "event":
        result = database.read(event_id=uid(99))
    elif fault == "version":
        result = database.read(version=2)
    else:
        database.event["deleted_at"] = datetime(2026, 1, 1, tzinfo=UTC)
        result = database.read()
    assert result.status == "UNAVAILABLE" and result.lines == result.currencies == ()
    assert result.source_refs == ()
    assert len(database.queries) == 1


def test_more_than_64_known_lines_use_the_actual_event_set_reference():
    result = ExpenseDatabase([line(100 + i, "1") for i in range(70)]).read()
    group = result.currencies[0].covered
    assert group.total_cost == 70 and len(result.lines) == len(group.known_line_ids) == 70
    assert len(group.source_refs) == 1
    ref = group.source_refs[0]
    assert ref.source_kind == "EVENT_RECEIPT_SET" and ref.source_id == uid(2)
    assert ref.version == 3 and ref.digest_sha256 == result.digest_sha256
    assert len({item.line_id for item in result.lines}) == 70


def test_budget_and_database_failure_are_explicit_and_do_not_expose_partial_totals():
    result = ExpenseDatabase([line(100 + i, "1") for i in range(MAX_EXPENSE_LINES + 1)]).read()
    assert result.status == "UNAVAILABLE" and result.currencies == ()
    assert "EXPENSE_LINE_LIMIT_EXCEEDED" in result.reason_codes
    database = ExpenseDatabase([line(10)])
    database.fail = True
    failed = database.read()
    assert failed.status == "UNAVAILABLE" and failed.reason_codes == ("EXPENSE_SOURCE_UNAVAILABLE",)
    assert "synthetic-error-detail" not in repr(failed)


@pytest.mark.parametrize(
    "change",
    [
        "amount",
        "category",
        "currency",
        "confirmation_level",
        "coverage_category",
        "version",
        "deleted_at",
    ],
)
def test_digest_binds_every_receipt_value_and_preserves_old_read(change):
    database = ExpenseDatabase([line(10), line(11, "0")])
    original = database.read()
    database.rows.reverse()
    assert database.read() == original
    changed = database.rows[1]
    changed[change] = {
        "amount": Decimal(619),
        "category": "outpatient",
        "currency": "USD",
        "confirmation_level": "unconfirmed",
        "coverage_category": "excluded",
        "version": 2,
        "deleted_at": datetime(2026, 1, 1, tzinfo=UTC),
    }[change]
    assert database.read().digest_sha256 != original.digest_sha256
    assert original.lines[0].amount == 50000 and original.lines[0].version == 1


def test_foreign_line_identity_is_not_accepted_even_if_a_row_reader_is_wrong():
    foreign = line(10)
    foreign["household_space_id"] = uid(99)
    result = ExpenseDatabase([foreign]).read()
    assert result.status == "UNAVAILABLE" and result.currencies == ()
    assert "EXPENSE_LINE_SCOPE_INVALID" in result.reason_codes


def test_non_decimal_nan_is_an_unknown_cost_with_a_stable_error_label():
    invalid = line(11)
    invalid["amount"] = float("nan")
    result = ExpenseDatabase([line(10), invalid]).read()
    assert result.status == "PARTIAL"
    assert result.currencies[0].covered.known_cost == 50000
    assert result.currencies[0].covered.total_cost is None
    assert "EXPENSE_AMOUNT_INVALID" in result.reason_codes
