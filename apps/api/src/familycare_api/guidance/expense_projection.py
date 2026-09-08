"""Project registered costs and bind only confirmed covered costs to a formula."""

from dataclasses import dataclass
from decimal import Decimal

from familycare_api.decisions.domain import MedicalEvent
from familycare_api.guidance.calculation_runtime import CalculationSourceRef
from familycare_api.guidance.expenses import ExpenseCostGroup, ExpenseRead
from familycare_api.guidance.models import (
    GuidanceCostGroup,
    GuidanceCurrencyCosts,
    GuidanceExpenses,
)
from familycare_api.guidance.trace_projection import decimal_text, source_reference

RECEIPT_FIELDS = frozenset({"Receipt.confirmed_amount", "Receipt.covered_amount"})


@dataclass(frozen=True, slots=True, repr=False)
class CoveredCostInput:
    value: Decimal | None = None
    source_refs: tuple[CalculationSourceRef, ...] = ()
    partial: bool = False


def expense_failure(expenses: ExpenseRead | None, event: MedicalEvent) -> str | None:
    if expenses is None:
        return None
    if expenses.event_id != event.id:
        return "EXPENSE_EVENT_SCOPE_MISMATCH"
    if expenses.event_version != event.version or expenses.expected_event_version != event.version:
        return "EXPENSE_EVENT_VERSION_MISMATCH"
    if expenses.status == "UNAVAILABLE":
        return "EXPENSE_SOURCE_UNAVAILABLE"
    return None


def covered_cost_input(
    expenses: ExpenseRead | None, event: MedicalEvent, currency: str | None
) -> CoveredCostInput:
    if expenses is None or expense_failure(expenses, event) or currency is None:
        return CoveredCostInput()
    group = next((item for item in expenses.currencies if item.currency == currency), None)
    if group is None:
        return CoveredCostInput()
    partial = bool(
        expenses.review_required
        or expenses.unassigned_line_ids
        or len(expenses.currencies) != 1
        or group.covered.unknown_amount_line_ids
    )
    if group.covered.known_cost is not None:
        return CoveredCostInput(group.covered.known_cost, group.covered.source_refs, partial)
    # Zero applies only to the provided, fully classified expense set. Missing
    # coverage/amount/currency or an empty receipt set can never establish zero.
    if not partial and group.excluded.total_cost is not None and not group.covered.line_ids:
        return CoveredCostInput(Decimal(0), group.excluded.source_refs)
    return CoveredCostInput()


def _cost_group(group: ExpenseCostGroup) -> GuidanceCostGroup:
    return GuidanceCostGroup(
        line_ids=group.line_ids,
        known_line_ids=group.known_line_ids,
        unknown_amount_line_ids=group.unknown_amount_line_ids,
        known_cost=decimal_text(group.known_cost),
        total_cost=decimal_text(group.total_cost),
        source_refs=tuple(source_reference(ref) for ref in group.source_refs),
    )


def expense_summary(expenses: ExpenseRead | None, event: MedicalEvent) -> GuidanceExpenses | None:
    if expenses is None:
        return None
    failure = expense_failure(expenses, event)
    return GuidanceExpenses(
        event_id=event.id,
        event_version=event.version,
        status="UNAVAILABLE" if failure else expenses.status,
        reader_revision=expenses.reader_revision,
        digest_sha256=expenses.digest_sha256,
        currencies=()
        if failure
        else tuple(
            GuidanceCurrencyCosts(
                currency=group.currency,
                covered=_cost_group(group.covered),
                excluded=_cost_group(group.excluded),
                coverage_review=_cost_group(group.coverage_review),
                unconfirmed=_cost_group(group.unconfirmed),
            )
            for group in expenses.currencies
        ),
        unassigned_line_ids=() if failure else expenses.unassigned_line_ids,
        reason_codes=tuple(
            dict.fromkeys((*expenses.reason_codes, *((failure,) if failure else ())))
        ),
    )
