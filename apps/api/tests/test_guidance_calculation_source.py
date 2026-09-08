"""Source-shaped unit proofs connect original calculations to Decimal traces."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from uuid import UUID

import pytest
from familycare_api.guidance.calculation_runtime import (
    CalculationInput,
    CalculationSourceRef,
    evaluate_calculation,
)
from familycare_api.guidance.calculation_source import (
    CalculationSourceError,
    bind_calculation_source,
)
from familycare_api.guidance.private_adapter import adapt_private_guidance
from familycare_api.guidance.semantic_binding import bind_semantic_root

from apps.api.tests.test_guidance_semantic_binding import source_and_root
from apps.api.tests.test_private_knowledge_engine import _context, _coverage


def op(name, *args, rounding=None):
    result = {"op": name, "args": list(args)}
    if rounding is not None:
        result["rounding"] = rounding
    return result


def literal(value):
    return {"value": value}


INSURED = {"field": "Rider.insured_amount"}
DAYS = {"field": "MedicalEvent.admission_days"}
RECEIPT = {"field": "Receipt.covered_amount"}
COUNT = {"field": "ClaimHistory.counted_occurrence"}
INPUT_REF = CalculationSourceRef("SYNTHETIC_EVENT_FIELD", UUID(int=400, version=4), version=1)


def _fields(value):
    if "field" in value:
        return {value["field"]}
    return {field for arg in value.get("args", ()) for field in _fields(arg)}


def publication(expression=None, *, document_kind="fixed_amount", kind="FIXED", currency=None):
    original = adapt_private_guidance(_context(_coverage(1, "100"))).coverages[0].calculation
    expression = expression or original.calculation_document["calculation"]
    document = dict(
        original.calculation_document,
        rule_kind=document_kind,
        calculation=expression,
        input_field_paths=sorted(_fields(expression)),
    )
    return replace(
        original, calculation_document=document, calculation_kind=kind, source_currency=currency
    )


def input_value(value, unit="MONEY", currency="KRW"):
    return CalculationInput(Decimal(str(value)), unit, currency, "PROGRAM_VERIFIED", (INPUT_REF,))


def evaluate(source, inputs=None, *, currency="KRW"):
    binding = bind_calculation_source(source, currency=currency)
    result = evaluate_calculation(
        binding.calculation, inputs or {}, source=binding.source, unit_hints=binding.unit_hints
    )
    return binding, result


def semantic():
    snapshot, clause, current = source_and_root()
    bound = bind_semantic_root(current, clause, snapshot.source.model_dump())
    return snapshot, current, bound.calculation


def test_original_b03_daily_amount_has_source_bound_units_for_every_operand_and_step():
    snapshot, current, source = semantic()
    assert source.calculation_document["rule_kind"] == "rate_amount"
    binding, result = evaluate(
        source,
        {
            "Rider.insured_amount": input_value(100),
            "MedicalEvent.admission_days": input_value(5, "DAYS", None),
        },
    )
    assert binding.status == "BOUND" and binding.basis == "DAILY_INSURED_AMOUNT"
    assert binding.payout_unit == "MONEY" and result.amount == 300
    assert [step.operation for step in result.steps] == [
        "subtract",
        "max",
        "min",
        "multiply",
        "round",
    ]
    assert [step.unit for step in result.steps] == ["DAYS", "DAYS", "DAYS", "MONEY", "MONEY"]
    assert all(step.unit_source_refs for step in result.steps)
    operands = [operand for step in result.steps for operand in step.operands]
    assert all(
        operand.unit == "DAYS" and operand.source_refs
        for operand in operands
        if operand.kind == "LITERAL"
    )
    assert all(
        operand.source_refs == (INPUT_REF,) for operand in operands if operand.kind == "FIELD"
    )
    refs = {(ref.source_kind, ref.source_id) for ref in binding.source.source_refs}
    assert ("SEMANTIC_PUBLICATION", current.publication_id) in refs
    assert ("DOCUMENT_VERSION", UUID(snapshot.source.document_version_id)) in refs
    assert ("DOCUMENT_STRUCTURE_GENERATION", UUID(snapshot.source.generation_id)) in refs
    assert ("SEMANTIC_ROOT", current.root.root_node_id) in refs
    assert {("SEMANTIC_CITATION", c.evidence.citation_id) for c in source.citations} <= refs
    assert result.source.publication_id == source.publication_id


def test_private_fixed_insured_plus_zero_retains_actual_private_publication_and_section_ids():
    source = publication()
    binding, result = evaluate(source, {"Rider.insured_amount": input_value(100)})
    assert result.amount == 100 and binding.basis == "INSURED_AMOUNT"
    assert result.steps[0].operands[1].unit == "MONEY"
    refs = {(ref.source_kind, ref.source_id) for ref in binding.source.source_refs}
    assert refs == {
        ("PRIVATE_RULE_PUBLICATION", source.publication_id),
        ("TERMS_SECTION", source.citations[0].evidence.evidence_id),
    }


def test_insured_ratio_marks_only_the_dimensionless_rate_as_ratio():
    source = publication(
        op("multiply", INSURED, literal(Decimal("0.8"))), document_kind="rate_amount"
    )
    binding, result = evaluate(source, {"Rider.insured_amount": input_value(100)})
    assert result.amount == 80 and binding.basis == "INSURED_RATIO"
    assert [operand.unit for operand in result.steps[0].operands] == ["MONEY", "RATIO"]


@pytest.mark.parametrize(
    "expression",
    [
        op("round", literal(50), rounding="half_up"),
        op("round", op("multiply", literal(50), literal(1)), rounding="half_up"),
    ],
)
def test_explicit_fixed_literal_amount_uses_its_document_kind_and_currency(expression):
    binding, result = evaluate(publication(expression, currency="KRW"))
    assert result.amount == 50 and binding.basis == "FIXED_AMOUNT"
    if expression["args"][0].get("op") == "multiply":
        assert any(operand.unit == "NUMBER" for operand in result.steps[0].operands)


def test_deductible_cap_and_zero_are_money_while_the_ratio_stays_dimensionless():
    expression = op(
        "round",
        op(
            "min",
            op(
                "max",
                op("subtract", op("multiply", INSURED, literal(Decimal("0.8"))), literal(10)),
                literal(0),
            ),
            literal(65),
        ),
        rounding="half_even",
    )
    binding, result = evaluate(
        publication(expression, document_kind="rate_amount"),
        {"Rider.insured_amount": input_value(100)},
    )
    assert result.amount == 65 and binding.basis == "INSURED_RATIO"
    assert [
        operand.unit
        for step in result.steps
        for operand in step.operands
        if operand.kind == "LITERAL"
    ] == ["RATIO", "MONEY", "MONEY", "MONEY"]


def test_receipt_formula_keeps_its_own_money_basis_and_original_limit():
    expression = op(
        "min",
        op(
            "multiply",
            op("max", op("subtract", RECEIPT, literal(10)), literal(0)),
            literal(Decimal("0.8")),
        ),
        INSURED,
    )
    binding, result = evaluate(
        publication(expression, document_kind="rate_amount", kind="INDEMNITY"),
        {"Receipt.covered_amount": input_value(50), "Rider.insured_amount": input_value(100)},
    )
    assert binding.basis == "RECEIPT_AMOUNT" and result.amount == 32


def test_rounding_positions_and_the_formula_digest_remain_distinct():
    grouped = publication(
        op("round", op("add", literal(Decimal("0.5")), literal(Decimal("0.5"))), rounding="half_up")
    )
    separate = publication(
        op(
            "add",
            op("round", literal(Decimal("0.5")), rounding="half_up"),
            op("round", literal(Decimal("0.5")), rounding="half_up"),
        )
    )
    left, first = evaluate(grouped)
    right, second = evaluate(separate)
    assert first.amount == 1 and second.amount == 2
    assert left.formula_digest_sha256 != right.formula_digest_sha256


@pytest.mark.parametrize(
    "expression,document_kind,kind",
    [
        (op("add", INSURED, DAYS), "fixed_amount", "FIXED"),
        (op("multiply", INSURED, INSURED), "rate_amount", "FIXED"),
        (op("multiply", INSURED, DAYS), "fixed_amount", "FIXED"),
        (op("multiply", INSURED, COUNT), "rate_amount", "FIXED"),
        (op("multiply", RECEIPT, DAYS), "rate_amount", "INDEMNITY"),
        (op("add", DAYS, literal(0)), "fixed_amount", "FIXED"),
        (op("add", COUNT, literal(0)), "fixed_amount", "FIXED"),
        (op("multiply", literal(2), literal(3)), "rate_amount", "FIXED"),
    ],
)
def test_unsupported_dimensions_never_become_money_from_the_benefit_type(
    expression, document_kind, kind
):
    with pytest.raises(
        CalculationSourceError, match="^CALCULATION_(?:SOURCE_UNIT_UNSUPPORTED|UNIT_MISMATCH)$"
    ):
        bind_calculation_source(
            publication(expression, document_kind=document_kind, kind=kind), currency="KRW"
        )


@pytest.mark.parametrize("fault", ["unknown-unit", "different-currency"])
def test_source_hints_cannot_override_supplied_field_units_or_currency(fault):
    value = input_value(100)
    value = (
        replace(value, unit="UNKNOWN", currency=None)
        if fault == "unknown-unit"
        else replace(value, currency="USD")
    )
    _, result = evaluate(publication(), {"Rider.insured_amount": value})
    assert result.status == "FAILED" and result.amount is None


def test_source_and_caller_currency_mismatch_is_a_source_failure():
    with pytest.raises(CalculationSourceError, match="^CALCULATION_CURRENCY_MISMATCH$"):
        bind_calculation_source(publication(currency="USD"), currency="KRW")


def test_missing_caller_currency_preserves_formula_without_money_hints():
    source = publication(currency="KRW")
    binding = bind_calculation_source(source, currency=None)
    assert binding.status == "CURRENCY_UNRESOLVED"
    assert binding.reason_codes == ("CALCULATION_CURRENCY_UNRESOLVED",)
    assert binding.calculation is not None and binding.formula_digest_sha256
    assert all(hint.unit != "MONEY" for hint in binding.unit_hints.values())


@pytest.mark.parametrize(
    "fault",
    [
        "lineage",
        "publication",
        "source-kind",
        "missing-hash",
        "duplicate",
        "reason-code",
        "foreign-key",
    ],
)
def test_invalid_source_citations_and_metadata_are_never_unit_authority(fault):
    source = publication()
    citation = source.citations[0]
    if fault == "lineage":
        source = replace(source, citations=(replace(citation, lineage_valid=False),))
    elif fault == "publication":
        source = replace(source, publication_id=UUID(int=999))
    elif fault == "source-kind":
        source = replace(source, source_kind="OPERATIONAL_RULE_VERSION")
    elif fault == "missing-hash":
        source = replace(
            source,
            citations=(
                replace(
                    citation, evidence=citation.evidence.model_copy(update={"source_sha256": None})
                ),
            ),
        )
    elif fault == "duplicate":
        source = replace(source, citations=source.citations * 2)
    elif fault == "reason-code":
        source = replace(source, result_reason_code="OTHER_REASON")
    else:
        source = replace(
            source,
            calculation_document=dict(source.calculation_document, evidence_ids=["foreign-key"]),
        )
    with pytest.raises(CalculationSourceError):
        bind_calculation_source(source, currency="KRW")


@pytest.mark.parametrize("fault", ["manifest", "root", "citation-id", "generation"])
def test_semantic_address_and_manifest_consistency_are_rechecked(fault):
    _, _, source = semantic()
    citation = source.citations[0]
    updates = {
        "manifest": {"manifest_sha256": "e" * 64},
        "root": {"root_node_id": "other-root"},
        "citation-id": {"citation_id": UUID(int=999)},
        "generation": {"generation_id": UUID(int=999)},
    }[fault]
    source = replace(
        source,
        citations=(
            replace(citation, evidence=citation.evidence.model_copy(update=updates)),
            *source.citations[1:],
        ),
    )
    with pytest.raises(CalculationSourceError):
        bind_calculation_source(source, currency="KRW")


def test_same_section_id_with_conflicting_source_hashes_is_not_merged():
    source = publication()
    citation = source.citations[0]
    other = replace(
        citation,
        citation_key="second-citation",
        evidence=citation.evidence.model_copy(update={"source_sha256": "e" * 64}),
    )
    source = replace(
        source,
        citations=(citation, other),
        calculation_document=dict(
            source.calculation_document, evidence_ids=[citation.citation_key, other.citation_key]
        ),
    )
    with pytest.raises(CalculationSourceError, match="^CALCULATION_SOURCE_REFERENCE_MISMATCH$"):
        bind_calculation_source(source, currency="KRW")


def test_binding_is_immutable_and_formula_and_source_versions_are_separate():
    source = publication()
    before = deepcopy(source.calculation_document)
    first = bind_calculation_source(source, currency="KRW")
    changed_id = UUID(int=700, version=4)
    changed = replace(
        source,
        publication_id=changed_id,
        citations=tuple(
            replace(c, evidence=c.evidence.model_copy(update={"publication_id": changed_id}))
            for c in source.citations
        ),
    )
    second = bind_calculation_source(changed, currency="KRW")
    assert first.formula_digest_sha256 == second.formula_digest_sha256
    assert first.source.digest_sha256 != second.source.digest_sha256
    assert source.calculation_document == before
    with pytest.raises(TypeError):
        first.unit_hints["/calculation"] = None
    with pytest.raises(FrozenInstanceError):
        first.basis = "RECEIPT_AMOUNT"
    assert "synthetic-calculation" not in repr(first)


@pytest.mark.parametrize("identifier", ["not-a-uuid", str(UUID(int=99)), UUID(int=0)])
def test_copied_evidence_cannot_spoof_uuid_reference_types(identifier):
    source = publication()
    citation = source.citations[0]
    source = replace(
        source,
        citations=(
            replace(
                citation, evidence=citation.evidence.model_copy(update={"evidence_id": identifier})
            ),
        ),
    )
    with pytest.raises(CalculationSourceError):
        bind_calculation_source(source, currency="KRW")


def test_operational_source_retains_real_rule_version_and_evidence_ids():
    source = publication()
    original = source.citations[0]
    evidence = original.evidence.model_copy(update={"kind": "OPERATIONAL_EVIDENCE"})
    citation = replace(original, citation_key=str(evidence.evidence_id), evidence=evidence)
    source = replace(
        source,
        source_kind="OPERATIONAL_RULE_VERSION",
        citations=(citation,),
        calculation_document=dict(
            source.calculation_document, evidence_ids=[citation.citation_key]
        ),
    )
    binding, result = evaluate(source, {"Rider.insured_amount": input_value(100)})
    assert result.amount == 100
    assert {(r.source_kind, r.source_id) for r in binding.source.source_refs} == {
        ("OPERATIONAL_RULE_VERSION", source.publication_id),
        ("OPERATIONAL_EVIDENCE", evidence.evidence_id),
    }


def test_missing_values_are_runtime_inputs_and_do_not_invalidate_the_original_unit_binding():
    binding, result = evaluate(publication())
    assert binding.status == "BOUND"
    assert result.amount is None and result.missing_paths == ("Rider.insured_amount",)
    assert "CALCULATION_INPUT_MISSING" in result.reason_codes


def test_source_budget_rejects_cyclic_documents_without_evaluating_or_mutating_them():
    expression = {"op": "add"}
    expression["args"] = [INSURED, expression]
    original = publication()
    source = replace(
        original, calculation_document=dict(original.calculation_document, calculation=expression)
    )
    with pytest.raises(CalculationSourceError, match="^CALCULATION_SOURCE_BUDGET_EXCEEDED$"):
        bind_calculation_source(source, currency="KRW")
    assert expression["args"][1] is expression
