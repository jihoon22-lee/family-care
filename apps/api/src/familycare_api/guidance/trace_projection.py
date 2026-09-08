"""Transport projections retain actual source identities and exact Decimal text."""

from contextlib import suppress
from decimal import Decimal
from uuid import UUID

from familycare_api.guidance.calculation_runtime import CalculationEvaluation, CalculationSourceRef
from familycare_api.guidance.models import GuidanceCalculationTrace, GuidanceSourceReference


def decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def source_reference(value: CalculationSourceRef) -> GuidanceSourceReference:
    return GuidanceSourceReference(
        source_kind=value.source_kind,
        source_id=str(value.source_id),
        version=value.version,
        digest_sha256=value.digest_sha256,
    )


def runtime_reference(value: GuidanceSourceReference) -> CalculationSourceRef:
    key: UUID | str = value.source_id
    with suppress(ValueError):
        key = UUID(value.source_id)
    return CalculationSourceRef(value.source_kind, key, value.version, value.digest_sha256)


def calculation_trace(
    value: CalculationEvaluation, formula_digest_sha256: str
) -> GuidanceCalculationTrace:
    return GuidanceCalculationTrace.model_validate(
        {
            "publication_id": value.source.publication_id,
            "source_revision": value.source.revision,
            "source_digest_sha256": value.source.digest_sha256,
            "formula_digest_sha256": formula_digest_sha256,
            "runtime_revision": value.runtime_revision,
            "source_refs": tuple(source_reference(ref) for ref in value.source.source_refs),
            "status": value.status,
            "value": decimal_text(value.value),
            "unit": value.unit,
            "currency": value.currency,
            "steps": tuple(
                {
                    "step_number": step.step_number,
                    "expression_path": step.path,
                    "operation": step.operation,
                    "value": decimal_text(step.value),
                    "unit": step.unit,
                    "currency": step.currency,
                    "rounding_rule": step.rounding_rule,
                    "unit_source_refs": tuple(
                        source_reference(ref) for ref in step.unit_source_refs
                    ),
                    "status": step.status,
                    "reason_codes": step.reason_codes,
                    "operands": tuple(
                        {
                            "expression_path": operand.path,
                            "kind": operand.kind,
                            "field_path": operand.field_path,
                            "child_path": operand.child_path,
                            "value": decimal_text(operand.value),
                            "supplied_value": decimal_text(operand.supplied_value),
                            "unit": operand.unit,
                            "currency": operand.currency,
                            "provenance": operand.provenance,
                            "source_refs": tuple(
                                source_reference(ref) for ref in operand.source_refs
                            ),
                            "status": operand.status,
                            "reason_codes": operand.reason_codes,
                            "stale": operand.stale,
                        }
                        for operand in step.operands
                    ),
                }
                for step in value.steps
            ),
            "missing_paths": value.missing_paths,
            "calculated_components": tuple(
                {
                    "parent_path": item.parent_path,
                    "expression_path": item.path,
                    "value": decimal_text(item.value),
                    "unit": item.unit,
                    "currency": item.currency,
                }
                for item in value.completed_addends
            ),
            "reason_codes": value.reason_codes,
        }
    )
