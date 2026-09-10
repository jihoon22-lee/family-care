"""Bounded Decimal arithmetic with real source identities and incomplete traces.

Callers verify publication/field authority before constructing these inputs. Unit
hints are original-source bindings at JSON-pointer AST addresses, never guesses.
A completed addend is only an expression component: it is not a subtotal, lower
bound or partial payout. Only a complete, nonnegative MONEY result has an amount.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import (
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Context,
    Decimal,
    DecimalException,
    Inexact,
    Overflow,
    Rounded,
    localcontext,
)
from typing import Literal
from uuid import UUID

from familycare_api.clauses.dsl import CompiledCalculation

CALCULATION_RUNTIME_REVISION = "guidance-calculation-v3"
MAX_CALCULATION_NODES = 256
MAX_CALCULATION_STEPS = 256
MAX_CALCULATION_DEPTH = 16
MAX_CALCULATION_MAGNITUDE = Decimal("1e60")
DECIMAL_PRECISION = 80
_ROOT = "/calculation"
_UNITS = frozenset({"MONEY", "DAYS", "COUNT", "RATIO", "NUMBER", "BOOLEAN", "UNKNOWN"})
_TRUSTED = frozenset(
    {"USER_CONFIRMED", "DOCUMENT_REVIEWED", "DERIVED_CONFIRMED", "PROGRAM_VERIFIED"}
)
_ROUNDING = {
    "half_up": ROUND_HALF_UP,
    "half_even": ROUND_HALF_EVEN,
    "up": ROUND_UP,
    "down": ROUND_DOWN,
}
_OPERATIONS = frozenset({"add", "subtract", "multiply", "min", "max", "round", "if"})
_SCENARIO_FIELD = "MedicalEvent.admission_days"
_CURRENCY = re.compile(r"[A-Z]{3}")
_SHA = re.compile(r"[0-9a-f]{64}")
type CalculationUnit = Literal["MONEY", "DAYS", "COUNT", "RATIO", "NUMBER", "BOOLEAN", "UNKNOWN"]
type EvaluationStatus = Literal["COMPLETE", "PARTIAL", "UNAVAILABLE", "FAILED"]
type OperandStatus = Literal["AVAILABLE", "UNAVAILABLE", "FAILED"]


def _require(condition: bool, code: str = "CALCULATION_INPUT_INVALID") -> None:
    if not condition:
        raise ValueError(code)


def _unit_valid(unit: str, currency: str | None) -> bool:
    return (
        isinstance(unit, str)
        and unit in _UNITS
        and (
            currency is None
            or unit == "MONEY"
            and isinstance(currency, str)
            and _CURRENCY.fullmatch(currency) is not None
        )
    )


@dataclass(frozen=True, slots=True, repr=False)
class CalculationSourceRef:
    source_kind: str
    source_id: UUID | str
    version: int | str | None = None
    digest_sha256: str | None = None

    def __post_init__(self) -> None:
        _require(isinstance(self.source_kind, str) and 1 <= len(self.source_kind) <= 64)
        _require(
            isinstance(self.source_id, UUID)
            and self.source_id.int != 0
            or isinstance(self.source_id, str)
            and 1 <= len(self.source_id) <= 256
        )
        _require(
            self.version is None
            or type(self.version) is int
            and self.version > 0
            or isinstance(self.version, str)
            and 1 <= len(self.version) <= 128
        )
        _require(
            self.digest_sha256 is None
            or isinstance(self.digest_sha256, str)
            and _SHA.fullmatch(self.digest_sha256) is not None
        )


def _refs_valid(refs: tuple[CalculationSourceRef, ...]) -> bool:
    return (
        isinstance(refs, tuple)
        and len(refs) <= 64
        and all(isinstance(ref, CalculationSourceRef) for ref in refs)
    )


@dataclass(frozen=True, slots=True, repr=False)
class CalculationSource:
    publication_id: UUID
    revision: str
    digest_sha256: str
    source_refs: tuple[CalculationSourceRef, ...] = ()

    def __post_init__(self) -> None:
        _require(
            isinstance(self.publication_id, UUID) and self.publication_id.int != 0,
            "CALCULATION_SOURCE_INVALID",
        )
        _require(
            isinstance(self.revision, str) and 1 <= len(self.revision) <= 128,
            "CALCULATION_SOURCE_INVALID",
        )
        _require(
            isinstance(self.digest_sha256, str)
            and _SHA.fullmatch(self.digest_sha256) is not None
            and _refs_valid(self.source_refs),
            "CALCULATION_SOURCE_INVALID",
        )


@dataclass(frozen=True, slots=True, repr=False)
class CalculationInput:
    value: Decimal | bool | None
    unit: CalculationUnit
    currency: str | None
    provenance: str
    source_refs: tuple[CalculationSourceRef, ...] = ()
    stale: bool = False

    def __post_init__(self) -> None:
        _require(
            self.value is None
            or type(self.value) is bool
            and self.unit == "BOOLEAN"
            or isinstance(self.value, Decimal)
            and self.unit != "BOOLEAN"
        )
        _require(_unit_valid(self.unit, self.currency))
        _require(isinstance(self.provenance, str) and 1 <= len(self.provenance) <= 64)
        _require(_refs_valid(self.source_refs) and type(self.stale) is bool)


@dataclass(frozen=True, slots=True, repr=False)
class CalculationUnitHint:
    unit: CalculationUnit
    currency: str | None
    source_refs: tuple[CalculationSourceRef, ...]

    def __post_init__(self) -> None:
        _require(_unit_valid(self.unit, self.currency), "CALCULATION_UNIT_HINT_INVALID")
        _require(
            bool(self.source_refs) and _refs_valid(self.source_refs),
            "CALCULATION_UNIT_HINT_INVALID",
        )
        _require(self.unit != "MONEY" or self.currency is not None, "CALCULATION_UNIT_HINT_INVALID")


@dataclass(frozen=True, slots=True, repr=False)
class CalculationOperand:
    path: str
    kind: Literal["FIELD", "LITERAL", "CHILD"]
    field_path: str | None
    child_path: str | None
    value: Decimal | bool | None
    supplied_value: Decimal | bool | None
    unit: CalculationUnit
    currency: str | None
    provenance: str | None
    source_refs: tuple[CalculationSourceRef, ...]
    status: OperandStatus
    reason_codes: tuple[str, ...] = ()
    stale: bool = False


@dataclass(frozen=True, slots=True, repr=False)
class CalculationStep:
    step_number: int
    path: str
    operation: str
    operands: tuple[CalculationOperand, ...]
    value: Decimal | None
    unit: CalculationUnit
    currency: str | None
    rounding_rule: str | None
    unit_source_refs: tuple[CalculationSourceRef, ...]
    status: OperandStatus
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class CompletedAddend:
    parent_path: str
    path: str
    value: Decimal
    unit: CalculationUnit
    currency: str | None


@dataclass(frozen=True, slots=True, repr=False)
class CalculationEvaluation:
    source: CalculationSource
    runtime_revision: str
    status: EvaluationStatus
    value: Decimal | None
    unit: CalculationUnit
    currency: str | None
    steps: tuple[CalculationStep, ...]
    missing_paths: tuple[str, ...]
    completed_addends: tuple[CompletedAddend, ...]
    reason_codes: tuple[str, ...]

    @property
    def amount(self) -> Decimal | None:
        return (
            self.value
            if self.status == "COMPLETE" and self.unit == "MONEY" and self.currency is not None
            else None
        )


class _Failure(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _unique(codes: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(codes))


def _checked(value: Decimal) -> Decimal:
    if not value.is_finite() or value.copy_abs() >= MAX_CALCULATION_MAGNITUDE:
        raise _Failure("CALCULATION_NUMERIC_LIMIT")
    if not value.is_zero() and value.adjusted() < -60:
        raise _Failure("CALCULATION_NUMERIC_LIMIT")
    return Decimal(0) if value.is_zero() else +value


def _paths(root: CompiledCalculation) -> set[str]:
    paths: set[str] = set()
    active: set[int] = set()

    def visit(value: object, path: str, depth: int) -> None:
        if depth > MAX_CALCULATION_DEPTH or len(paths) >= MAX_CALCULATION_NODES:
            raise _Failure("CALCULATION_LIMIT_EXCEEDED")
        paths.add(path)
        if isinstance(value, CompiledCalculation):
            if (
                id(value) in active
                or value.operator not in _OPERATIONS
                or not isinstance(value.operands, tuple)
            ):
                raise _Failure("CALCULATION_TREE_INVALID")
            if len(value.operands) > 16:
                raise _Failure("CALCULATION_LIMIT_EXCEEDED")
            if value.operator == "round":
                if len(value.operands) != 1 or value.rounding not in _ROUNDING:
                    raise _Failure("CALCULATION_ROUNDING_INVALID")
            elif value.operator == "if":
                if (
                    len(value.operands) != 3
                    or not isinstance(value.operands[0], str)
                    or value.rounding is not None
                ):
                    raise _Failure("CALCULATION_TREE_INVALID")
            elif len(value.operands) < 2 or value.rounding is not None:
                raise _Failure("CALCULATION_TREE_INVALID")
            active.add(id(value))
            for index, operand in enumerate(value.operands):
                visit(operand, f"{path}/args/{index}", depth + 1)
            active.remove(id(value))
        elif not isinstance(value, Decimal) and not (
            isinstance(value, str) and 1 <= len(value) <= 160
        ):
            raise _Failure("CALCULATION_TREE_INVALID")

    visit(root, _ROOT, 0)
    return paths


def _result_unit(
    operator: str, operands: tuple[CalculationOperand, ...], hint: CalculationUnitHint | None
) -> tuple[CalculationUnit, str | None]:
    currencies = {item.currency for item in operands if item.currency is not None}
    if hint is not None and hint.currency is not None:
        currencies.add(hint.currency)
    if len(currencies) > 1:
        raise _Failure("CALCULATION_CURRENCY_MISMATCH")
    currency = next(iter(currencies), None)
    units = {item.unit for item in operands}
    if "BOOLEAN" in units or hint is not None and hint.unit == "BOOLEAN":
        raise _Failure("CALCULATION_UNIT_MISMATCH")
    if "UNKNOWN" in units:
        return "UNKNOWN", None
    dimensions = units - {"NUMBER", "RATIO"}
    if operator == "multiply":
        dimensioned = [item.unit for item in operands if item.unit not in {"NUMBER", "RATIO"}]
        if len(dimensioned) > 1:
            if dimensioned.count("MONEY") != 1 or not set(dimensioned) <= {
                "MONEY",
                "DAYS",
                "COUNT",
            }:
                raise _Failure("CALCULATION_UNIT_MISMATCH")
            inferred: CalculationUnit = "UNKNOWN"
        else:
            inferred = next(iter(dimensions)) if dimensions else "NUMBER"
    elif len(dimensions) > 1:
        raise _Failure("CALCULATION_UNIT_MISMATCH")
    elif len(units) == 1:
        inferred = operands[0].unit
    elif dimensions:
        inferred = "UNKNOWN"
    else:
        inferred = "NUMBER"
    if hint is not None:
        if inferred not in {"NUMBER", "UNKNOWN", hint.unit}:
            raise _Failure("CALCULATION_UNIT_MISMATCH")
        inferred = hint.unit
        currency = hint.currency if hint.unit == "MONEY" else None
    return inferred, currency if inferred == "MONEY" else None


def _scenario_accepted(
    field: str,
    item: CalculationInput,
    accepted: Mapping[str, CalculationInput],
) -> bool:
    if (
        field != _SCENARIO_FIELD
        or item.provenance != "SCENARIO_ASSUMPTION"
        or item.stale
        or item.unit != "DAYS"
        or item.currency is not None
        or not isinstance(item.value, Decimal)
        or not item.value.is_finite()
        or not 0 <= item.value <= 36500
        or item.value != item.value.to_integral_value()
        or not item.source_refs
        or len(set(item.source_refs)) != 1
    ):
        return False
    ref = item.source_refs[0]
    return (
        ref.source_kind == "EVENT_SCENARIO"
        and isinstance(ref.source_id, UUID)
        and ref.source_id.int != 0
        and type(ref.version) is int
        and ref.version > 0
        and ref.digest_sha256 is not None
        and accepted.get(field) == item
    )


class _Runtime:
    def __init__(
        self,
        inputs: Mapping[str, CalculationInput],
        source: CalculationSource,
        hints: Mapping[str, CalculationUnitHint],
        scenarios: Mapping[str, CalculationInput],
    ) -> None:
        self.inputs, self.source, self.hints = inputs, source, hints
        self.scenarios = scenarios
        self.steps: list[CalculationStep] = []
        self.missing: list[str] = []
        self.addends: list[CompletedAddend] = []

    def operand(self, value: object, path: str, *, boolean: bool = False) -> CalculationOperand:
        if isinstance(value, CompiledCalculation):
            step = self.evaluate(value, path)
            return CalculationOperand(
                path,
                "CHILD",
                None,
                path,
                step.value,
                None,
                step.unit,
                step.currency,
                None,
                (),
                step.status,
                step.reason_codes,
            )
        hint = self.hints.get(path)
        if isinstance(value, str):
            item = self.inputs.get(value)
            scenario = item is not None and _scenario_accepted(value, item, self.scenarios)
            reasons = []
            if item is None or item.value is None:
                reasons.append("CALCULATION_INPUT_MISSING")
            if item is not None:
                if item.stale:
                    reasons.append("CALCULATION_INPUT_STALE")
                if item.provenance not in _TRUSTED and not scenario:
                    reasons.append("CALCULATION_INPUT_UNTRUSTED")
                if not item.source_refs:
                    reasons.append("CALCULATION_INPUT_SOURCE_MISSING")
                if item.unit == "MONEY" and item.currency is None:
                    reasons.append("CALCULATION_CURRENCY_UNRESOLVED")
            numeric: Decimal | bool | None = None
            supplied: Decimal | bool | None = None
            status: OperandStatus = "UNAVAILABLE" if reasons else "AVAILABLE"
            if item is not None and item.value is not None:
                try:
                    if boolean:
                        if item.unit != "BOOLEAN" or type(item.value) is not bool:
                            raise _Failure("CALCULATION_UNIT_MISMATCH")
                        supplied = item.value
                    else:
                        if not isinstance(item.value, Decimal) or item.unit == "BOOLEAN":
                            raise _Failure("CALCULATION_UNIT_MISMATCH")
                        supplied = _checked(item.value)
                    if status == "AVAILABLE":
                        numeric = supplied
                    if (
                        status == "AVAILABLE"
                        and hint is not None
                        and (hint.unit != item.unit or hint.currency != item.currency)
                    ):
                        raise _Failure("CALCULATION_UNIT_MISMATCH")
                except _Failure as error:
                    reasons.append(error.code)
                    status = "FAILED"
                except Overflow:
                    reasons.append("CALCULATION_NUMERIC_LIMIT")
                    status = "FAILED"
                except Inexact, Rounded:
                    reasons.append("CALCULATION_IMPLICIT_ROUNDING")
                    status = "FAILED"
                except DecimalException:
                    reasons.append("CALCULATION_NUMERIC_LIMIT")
                    status = "FAILED"
            if status != "AVAILABLE":
                self.missing.append(value)
                numeric = None
            return CalculationOperand(
                path,
                "FIELD",
                value,
                None,
                numeric,
                supplied,
                "UNKNOWN" if item is None else item.unit,
                None if item is None else item.currency,
                None if item is None else item.provenance,
                () if item is None else item.source_refs,
                status,
                (*reasons, "CALCULATION_SCENARIO_ASSUMPTION")
                if scenario and status == "AVAILABLE"
                else tuple(reasons),
                stale=False if item is None else item.stale,
            )
        if not isinstance(value, Decimal):
            raise _Failure("CALCULATION_TREE_INVALID")
        if boolean or hint is not None and hint.unit == "BOOLEAN":
            raise _Failure("CALCULATION_UNIT_MISMATCH")
        reasons = []
        numeric = None
        try:
            numeric = _checked(value)
        except _Failure as error:
            reasons.append(error.code)
        except Overflow:
            reasons.append("CALCULATION_NUMERIC_LIMIT")
        except Inexact, Rounded:
            reasons.append("CALCULATION_IMPLICIT_ROUNDING")
        except DecimalException:
            reasons.append("CALCULATION_NUMERIC_LIMIT")
        return CalculationOperand(
            path,
            "LITERAL",
            None,
            None,
            numeric,
            numeric,
            "NUMBER" if hint is None else hint.unit,
            None if hint is None else hint.currency,
            None,
            self.source.source_refs if hint is None else hint.source_refs,
            "FAILED" if reasons else "AVAILABLE",
            tuple(reasons),
        )

    def evaluate(self, node: CompiledCalculation, path: str) -> CalculationStep:
        operands: tuple[CalculationOperand, ...]
        if node.operator == "if":
            predicate = self.operand(node.operands[0], f"{path}/args/0", boolean=True)
            operands = (predicate,)
            if predicate.status == "AVAILABLE":
                if type(predicate.value) is not bool:
                    raise _Failure("CALCULATION_TREE_INVALID")
                selected = 1 if predicate.value else 2
                operands += (self.operand(node.operands[selected], f"{path}/args/{selected}"),)
        else:
            operands = tuple(
                self.operand(value, f"{path}/args/{index}")
                for index, value in enumerate(node.operands)
            )
        reasons = _unique(tuple(code for operand in operands for code in operand.reason_codes))
        status: OperandStatus = (
            "FAILED"
            if any(item.status == "FAILED" for item in operands)
            else "UNAVAILABLE"
            if any(item.status == "UNAVAILABLE" for item in operands)
            else "AVAILABLE"
        )
        unit: CalculationUnit = "UNKNOWN"
        currency = None
        output = None
        hint = self.hints.get(path)
        if status == "AVAILABLE":
            try:
                numeric_operands = operands[1:] if node.operator == "if" else operands
                unit, currency = _result_unit(node.operator, numeric_operands, hint)
                values = tuple(
                    item.value for item in numeric_operands if isinstance(item.value, Decimal)
                )
                if len(values) != len(numeric_operands):
                    raise _Failure("CALCULATION_TREE_INVALID")
                if node.operator == "if":
                    output = values[0]
                elif node.operator == "add":
                    output = sum(values, Decimal(0))
                elif node.operator == "subtract":
                    output = values[0] - sum(values[1:], Decimal(0))
                elif node.operator == "multiply":
                    output = Decimal(1)
                    for value in values:
                        output = _checked(output * value)
                elif node.operator == "min":
                    output = min(values)
                elif node.operator == "max":
                    output = max(values)
                else:
                    if node.rounding is None:
                        raise _Failure("CALCULATION_ROUNDING_INVALID")
                    with localcontext() as rounding_context:
                        rounding_context.traps[Inexact] = False
                        rounding_context.traps[Rounded] = False
                        output = values[0].quantize(Decimal(1), rounding=_ROUNDING[node.rounding])
                output = _checked(output)
            except _Failure as error:
                reasons = (*reasons, error.code)
                output, status = None, "FAILED"
            except Overflow:
                reasons = (*reasons, "CALCULATION_NUMERIC_LIMIT")
                output, status = None, "FAILED"
            except Inexact, Rounded:
                reasons = (*reasons, "CALCULATION_IMPLICIT_ROUNDING")
                output, status = None, "FAILED"
            except DecimalException:
                reasons = (*reasons, "CALCULATION_NUMERIC_LIMIT")
                output, status = None, "FAILED"
        if len(self.steps) >= MAX_CALCULATION_STEPS:
            raise _Failure("CALCULATION_LIMIT_EXCEEDED")
        step = CalculationStep(
            len(self.steps) + 1,
            path,
            node.operator,
            operands,
            output,
            unit,
            currency,
            node.rounding,
            () if hint is None else hint.source_refs,
            status,
            _unique(reasons),
        )
        self.steps.append(step)
        if node.operator == "add" and status != "AVAILABLE":
            self.addends.extend(
                CompletedAddend(path, operand.path, operand.value, operand.unit, operand.currency)
                for operand in operands
                if operand.status == "AVAILABLE" and isinstance(operand.value, Decimal)
            )
        return step


def evaluate_calculation(
    calculation: CompiledCalculation,
    inputs: Mapping[str, CalculationInput],
    *,
    source: CalculationSource,
    unit_hints: Mapping[str, CalculationUnitHint] | None = None,
    scenario_inputs: Mapping[str, CalculationInput] | None = None,
) -> CalculationEvaluation:
    """Evaluate one expression; field missing_paths and operand AST paths stay distinct.

    NUMBER is a dimensionless numeric value, not money. An original-source root
    hint may establish a daily or fixed amount basis, but cannot override unknown
    input units, known dimensional conflicts, mixed currencies or input trust.

    scenario_inputs explicitly opts one exact admission-days assumption into this
    call only. The caller validates the event's scope/current version; the runtime
    requires its actual EVENT_SCENARIO UUID/version/digest and retains assumption
    provenance. This permission neither supplies absent inputs nor changes trust.
    """
    _require(isinstance(source, CalculationSource), "CALCULATION_SOURCE_INVALID")
    runtime = _Runtime({}, source, {}, {})
    status: EvaluationStatus = "FAILED"
    value = None
    unit: CalculationUnit = "UNKNOWN"
    currency = None
    reasons: tuple[str, ...] = ()
    try:
        if not isinstance(calculation, CompiledCalculation) or not isinstance(inputs, Mapping):
            raise _Failure("CALCULATION_TREE_INVALID")
        if unit_hints is not None and not isinstance(unit_hints, Mapping):
            raise _Failure("CALCULATION_UNIT_HINT_INVALID")
        if scenario_inputs is not None and not isinstance(scenario_inputs, Mapping):
            raise _Failure("CALCULATION_SCENARIO_INPUT_INVALID")
        if scenario_inputs is not None and len(scenario_inputs) > MAX_CALCULATION_NODES:
            raise _Failure("CALCULATION_LIMIT_EXCEEDED")
        scenarios = {} if scenario_inputs is None else dict(scenario_inputs)
        if any(
            not isinstance(key, str) or not isinstance(item, CalculationInput)
            for key, item in scenarios.items()
        ):
            raise _Failure("CALCULATION_SCENARIO_INPUT_INVALID")
        hints = {} if unit_hints is None else dict(unit_hints)
        if len(inputs) > MAX_CALCULATION_NODES or len(hints) > MAX_CALCULATION_NODES:
            raise _Failure("CALCULATION_LIMIT_EXCEEDED")
        if any(
            not isinstance(key, str) or not isinstance(item, CalculationInput)
            for key, item in inputs.items()
        ):
            raise _Failure("CALCULATION_INPUT_INVALID")
        paths = _paths(calculation)
        if any(
            key not in paths or not isinstance(item, CalculationUnitHint)
            for key, item in hints.items()
        ):
            raise _Failure("CALCULATION_UNIT_HINT_INVALID")
        if any(not set(item.source_refs) <= set(source.source_refs) for item in hints.values()):
            raise _Failure("CALCULATION_UNIT_HINT_SOURCE_MISMATCH")
        runtime = _Runtime(dict(inputs), source, hints, scenarios)
        context = Context(prec=DECIMAL_PRECISION, Emin=-60, Emax=60)
        for signal in context.traps:
            context.traps[signal] = True
        with localcontext(context):
            root = runtime.evaluate(calculation, _ROOT)
        unit, currency, reasons = root.unit, root.currency, root.reason_codes
        if root.status == "AVAILABLE":
            if root.value is not None and root.value < 0:
                reasons = (*reasons, "CALCULATION_NEGATIVE_RESULT")
            else:
                value, status = root.value, "COMPLETE"
                if unit != "MONEY" or currency is None:
                    reasons = (*reasons, "CALCULATION_UNIT_UNRESOLVED")
        elif root.status == "UNAVAILABLE":
            status = (
                "PARTIAL"
                if runtime.addends or any(step.status == "AVAILABLE" for step in runtime.steps)
                else "UNAVAILABLE"
            )
    except _Failure as error:
        reasons = (error.code,)
    return CalculationEvaluation(
        source,
        CALCULATION_RUNTIME_REVISION,
        status,
        value,
        unit,
        currency,
        tuple(runtime.steps),
        tuple(dict.fromkeys(runtime.missing)),
        tuple(runtime.addends),
        _unique(reasons),
    )
