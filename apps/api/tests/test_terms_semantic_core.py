"""Synthetic semantic graph compiler and dependency regressions."""

from copy import deepcopy
from decimal import Decimal
from typing import Any

import pytest
from familycare_api.clauses.dsl import validate_rule_document
from familycare_api.decisions.domain import FactContext, FactValue
from familycare_api.decisions.knowledge_engine import _CalculationState
from familycare_api.terms_knowledge.core import affected_roots, compile_knowledge, parse_knowledge


def synthetic_graph() -> dict[str, Any]:
    ids = ["daily", "exclusion", "limit", "class", "unrelated"]
    payloads = [
        {
            "kind": "calculation",
            "mode": "daily",
            "basis": "insured_amount_per_payable_day",
            "currency": "KRW",
            "rounding": "half_up",
        },
        {"kind": "footnote", "effect": "initial_excluded_days", "days": 2},
        {
            "kind": "limit",
            "measure": "payable_days",
            "value": "10",
            "unit": "days",
            "currency": None,
        },
        {
            "kind": "classification",
            "field": "MedicalEvent.classification",
            "code_system": "synthetic-classification",
            "code_version": "edition-1",
            "codes": ["class-a"],
        },
        {
            "kind": "calculation",
            "mode": "fixed",
            "amount": "50",
            "currency": "KRW",
            "rounding": "half_up",
        },
    ]
    return {
        "schema_version": "1",
        "schema_revision": "terms-semantic-v1",
        "prompt_revision": "synthetic-prompt-v1",
        "model_revision": "synthetic-model-v1",
        "sources": [
            {
                "source_id": "terms",
                "document_version_id": "00000000-0000-4000-8000-000000000001",
                "terms_edition_id": "00000000-0000-4000-8000-000000000002",
                "generation_id": "00000000-0000-4000-8000-000000000003",
                "content_sha256": "a" * 64,
                "structure_identity_sha256": "b" * 64,
            }
        ],
        "citations": [
            {
                "citation_id": "00000000-0000-4000-8000-" + str(ids.index(key) + 11).zfill(12),
                "source_id": "terms",
                "node_id": "source-" + key,
                "page_number": 1,
                "start": 0,
                "end": 9,
                "text": "synthetic",
                "source_layer": "native",
                "bbox": [0.0, 0.0, 100.0, 10.0],
            }
            for key in ids
        ],
        "nodes": [
            {
                "node_id": key,
                "source_id": "terms",
                "statement": "Synthetic explanation",
                "region_ids": [key],
                "citation_ids": ["00000000-0000-4000-8000-" + str(ids.index(key) + 11).zfill(12)],
                "payload": value,
            }
            for key, value in zip(ids, payloads, strict=True)
        ],
        "edges": [
            {"from_node_id": "daily", "to_node_id": key, "relation": "DEPENDS_ON"}
            for key in ["exclusion", "limit", "class"]
        ],
        "roots": ["daily", "unrelated"],
        "processing": {
            "expected_region_ids": list(ids),
            "consumed_region_ids": list(ids),
            "unresolved_region_ids": [],
        },
    }


def compiled(payload: dict[str, Any]) -> Any:
    return compile_knowledge(
        parse_knowledge(payload),
        verified_citation_ids=frozenset(c["citation_id"] for c in payload["citations"]),
        verified_node_ids=frozenset(n["node_id"] for n in payload["nodes"]),
        verified_edges=frozenset(
            (e["from_node_id"], e["to_node_id"], e["relation"]) for e in payload["edges"]
        ),
    )


def amount(root: Any) -> Decimal:
    validated = validate_rule_document(root.calculation, root.citation_ids)
    assert validated.calculation is not None
    facts = FactContext(
        rider={
            "insured_amount": FactValue(value=Decimal("100"), confirmation="user", evidence_ids=())
        },
        medical_event={"admission_days": FactValue(value=5, confirmation="user", evidence_ids=())},
        policy={},
        claim_history={},
    )
    return _CalculationState(facts, "KRW").evaluate(validated.calculation)


def test_daily_formula_and_shared_footnote_change_only_dependents() -> None:
    graph = synthetic_graph()
    first = compiled(graph)
    assert amount(first.roots[0]) == Decimal("300")
    changed = deepcopy(graph)
    changed["nodes"][1]["payload"]["days"] = 1
    second = compiled(changed)
    assert amount(second.roots[0]) == Decimal("400")
    assert first.roots[1].manifest_sha256 == second.roots[1].manifest_sha256
    assert affected_roots(parse_knowledge(graph), frozenset({"exclusion"})) == ("daily",)
    assert first.roots[0].rules[0]["expression"]["value"] == ["class-a"]


def test_unverified_and_partial_processing_do_not_claim_executable_knowledge() -> None:
    graph = parse_knowledge(synthetic_graph())
    result = compile_knowledge(graph)
    assert result.roots[0].calculation is None
    assert result.roots[0].explanations
    assert "SOURCE_UNVERIFIED" in {d.code for d in result.roots[0].diagnostics}
    partial = synthetic_graph()
    partial["processing"]["consumed_region_ids"].remove("exclusion")
    result = compiled(partial)
    assert result.processing_complete is False


@pytest.mark.parametrize(
    "fault,code",
    [
        ("missing", "DEPENDENCY_MISSING"),
        ("cycle", "DEPENDENCY_CYCLE"),
        ("edition", "CROSS_EDITION_REFERENCE"),
    ],
)
def test_dependency_failures_are_scoped(fault: str, code: str) -> None:
    graph = synthetic_graph()
    if fault == "missing":
        graph["edges"].append(
            {"from_node_id": "daily", "to_node_id": "missing", "relation": "DEPENDS_ON"}
        )
    elif fault == "cycle":
        graph["edges"].append(
            {"from_node_id": "exclusion", "to_node_id": "daily", "relation": "DEPENDS_ON"}
        )
    else:
        other = {
            **graph["sources"][0],
            "source_id": "wrong",
            "terms_edition_id": "00000000-0000-4000-8000-000000000004",
        }
        graph["sources"].append(other)
        graph["nodes"][1]["source_id"] = "wrong"
        graph["citations"][1]["source_id"] = "wrong"
    result = compiled(graph)
    assert result.roots[0].calculation is None
    assert code in {d.code for d in result.roots[0].diagnostics}
    assert result.roots[1].calculation is not None


def test_unbound_edition_retains_explanation_but_no_executable_roots() -> None:
    graph = synthetic_graph()
    graph["sources"][0]["terms_edition_id"] = None
    result = compiled(graph)
    assert all(not root.executable for root in result.roots)
    assert all(root.explanations for root in result.roots)
    assert all(
        "EDITION_BINDING_UNVERIFIED" in {d.code for d in root.diagnostics} for root in result.roots
    )


def test_bad_citation_source_or_span_is_rejected_without_input_echo() -> None:
    from familycare_api.terms_knowledge.core import SemanticKnowledgeError

    graph = synthetic_graph()
    graph["citations"][0]["text"] = "synthetic wrong span"
    with pytest.raises(SemanticKnowledgeError, match="^SEMANTIC_GRAPH_INVALID$"):
        parse_knowledge(graph)


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", "-1"),
        ("amount", "NaN"),
        ("amount", "Infinity"),
        ("amount", float("inf")),
        ("mode", "money_plus_days"),
        ("basis", "premium"),
        ("calculation_document", {"op": "exec", "args": []}),
    ],
)
def test_invalid_money_and_arbitrary_expressions_never_enter_compiler(
    field: str, value: object
) -> None:
    from familycare_api.terms_knowledge.core import SemanticKnowledgeError

    graph = synthetic_graph()
    graph["nodes"][-1]["payload"][field] = value
    with pytest.raises(SemanticKnowledgeError):
        parse_knowledge(graph)


def test_currency_and_dimension_conflicts_only_limit_calculation() -> None:
    graph = synthetic_graph()
    limit = graph["nodes"][2]["payload"]
    limit.update(measure="maximum_amount", unit="amount", currency="USD")
    result = compiled(graph).roots[0]
    assert result.calculation is None
    assert result.rules
    assert "CALCULATION_CURRENCY_MISMATCH" in {d.code for d in result.diagnostics}
    limit.update(measure="payable_days", unit="amount", currency=None)
    result = compiled(graph).roots[0]
    assert "CALCULATION_UNIT_MISMATCH" in {d.code for d in result.diagnostics}


def test_scoped_override_and_conflicting_overrides() -> None:
    graph = synthetic_graph()
    replacement = deepcopy(graph["nodes"][1])
    replacement["node_id"] = "override"
    replacement["payload"]["days"] = 1
    graph["nodes"].append(replacement)
    graph["edges"] += [
        {"from_node_id": "daily", "to_node_id": "override", "relation": "DEPENDS_ON"},
        {"from_node_id": "override", "to_node_id": "exclusion", "relation": "OVERRIDES"},
    ]
    assert amount(compiled(graph).roots[0]) == Decimal("400")
    assert affected_roots(parse_knowledge(graph), frozenset({"override"})) == ("daily",)
    conflicting = {**replacement, "node_id": "conflict"}
    graph["nodes"].append(conflicting)
    graph["edges"] += [
        {"from_node_id": "daily", "to_node_id": "conflict", "relation": "DEPENDS_ON"},
        {"from_node_id": "conflict", "to_node_id": "exclusion", "relation": "OVERRIDES"},
    ]
    result = compiled(graph).roots[0]
    assert result.calculation is None
    assert "OVERRIDE_CONFLICT" in {d.code for d in result.diagnostics}


def test_unsupported_calculation_retains_classification_and_explanations() -> None:
    graph = synthetic_graph()
    graph["nodes"][1]["payload"] = {
        "kind": "information",
        "effect": "unsupported_calculation",
        "reason_code": "UNSUPPORTED_BRANCH",
    }
    result = compiled(graph).roots[0]
    assert result.calculation is None
    assert result.rules and len(result.explanations) == 4
    assert "UNSUPPORTED_BRANCH" in {d.code for d in result.diagnostics}


def test_shared_code_definition_retains_original_classification_version() -> None:
    graph = synthetic_graph()
    graph["nodes"][3]["payload"].update(
        kind="definition",
        effect="classification_codes",
        term="Synthetic code group",
        meaning="Synthetic original-version code definition",
    )
    result = compiled(graph).roots[0]
    assert result.rules[0]["expression"]["value"] == ["class-a"]
    assert result.classification_scopes[0]["code_version"] == "edition-1"


def test_ratio_uses_insured_amount_and_explicit_rounding() -> None:
    graph = synthetic_graph()
    graph["nodes"][-1]["payload"] = {
        "kind": "calculation",
        "mode": "insured_ratio",
        "basis": "insured_amount",
        "ratio": "0.125",
        "currency": "KRW",
        "rounding": "half_up",
    }
    assert amount(compiled(graph).roots[1]) == Decimal("13")
    graph["nodes"][-1]["payload"]["rounding"] = "half_even"
    assert amount(compiled(graph).roots[1]) == Decimal("12")


def test_source_generation_updates_provenance_without_changing_semantic_reuse_key() -> None:
    graph = synthetic_graph()
    previous = compiled(graph).roots[0]
    graph["sources"][0]["generation_id"] = "00000000-0000-4000-8000-000000000009"
    updated = compiled(graph).roots[0]
    assert previous.semantic_sha256 == updated.semantic_sha256
    assert previous.manifest_sha256 != updated.manifest_sha256


def test_missing_footnote_does_not_assert_zero_excluded_days() -> None:
    graph = synthetic_graph()
    graph["edges"] = [edge for edge in graph["edges"] if edge["to_node_id"] != "exclusion"]
    result = compiled(graph).roots[0]
    assert result.calculation is None
    assert "DAILY_EXCLUSION_BASIS_MISSING" in {d.code for d in result.diagnostics}


def test_valid_quote_does_not_authorize_unverified_semantic_claims() -> None:
    payload = synthetic_graph()
    graph = parse_knowledge(payload)
    result = compile_knowledge(
        graph, verified_citation_ids=frozenset(c.citation_id for c in graph.citations)
    )
    assert all(not root.executable for root in result.roots)


def test_cross_edition_classification_does_not_leak_through_independent_rule_compilation() -> None:
    payload = synthetic_graph()
    payload["sources"].append(
        {
            **payload["sources"][0],
            "source_id": "wrong",
            "terms_edition_id": "00000000-0000-4000-8000-000000000005",
        }
    )
    payload["nodes"][3]["source_id"] = "wrong"
    payload["citations"][3]["source_id"] = "wrong"
    result = compiled(payload).roots[0]
    assert result.rules == ()


def test_unverified_footnote_relation_does_not_change_formula_but_retains_independent_rules() -> (
    None
):
    graph = parse_knowledge(synthetic_graph())
    result = compile_knowledge(
        graph,
        verified_citation_ids=frozenset(c.citation_id for c in graph.citations),
        verified_node_ids=frozenset(n.node_id for n in graph.nodes),
        verified_edges=frozenset(
            (e.from_node_id, e.to_node_id, e.relation)
            for e in graph.edges
            if e.to_node_id == "class"
        ),
    )
    root = result.roots[0]
    assert root.calculation is None
    assert root.rules
    assert "SEMANTIC_RELATION_UNVERIFIED" in {d.code for d in root.diagnostics}


def test_unsupported_deep_graph_still_reports_transitive_change_impact() -> None:
    payload = synthetic_graph()
    previous = "daily"
    for index in range(35):
        node = {
            **deepcopy(payload["nodes"][1]),
            "node_id": f"deep-{index}",
            "payload": {
                "kind": "information",
                "effect": "explanation_only",
                "reason_code": "SYNTHETIC_EXPLANATION",
            },
        }
        payload["nodes"].append(node)
        payload["edges"].append(
            {"from_node_id": previous, "to_node_id": node["node_id"], "relation": "DEPENDS_ON"}
        )
        previous = node["node_id"]
    graph = parse_knowledge(payload)
    assert affected_roots(graph, frozenset({previous})) == ("daily",)
    result = compiled(payload).roots[0]
    assert result.calculation is None
    assert "DEPENDENCY_DEPTH_EXCEEDED" in {d.code for d in result.diagnostics}


def test_decimal_rate_preserves_exact_json_round_trip() -> None:
    graph = synthetic_graph()
    graph["nodes"][-1]["payload"] = {
        "kind": "calculation",
        "mode": "insured_ratio",
        "basis": "insured_amount",
        "ratio": "0.123456789012",
        "currency": "KRW",
        "rounding": "half_up",
    }
    result = compiled(graph).roots[1]
    assert result.calculation is not None
    assert result.calculation["calculation"]["args"][0]["args"][1]["value"] == 0.123456789012


def test_unrepresentable_decimal_is_preserved_as_unsupported_calculation() -> None:
    graph = synthetic_graph()
    graph["nodes"][-1]["payload"]["amount"] = "123456789012345678.125"
    result = compiled(graph).roots[1]
    assert result.calculation is None
    assert result.explanations
    assert "CALCULATION_PRECISION_UNSUPPORTED" in {d.code for d in result.diagnostics}


@pytest.mark.parametrize(
    "payload",
    [
        {
            "kind": "condition",
            "rule_kind": "eligibility",
            "field": "MedicalEvent.admission_days",
            "operator": "range",
            "value": [1, 10],
            "unit": "days",
        },
        {
            "kind": "condition",
            "rule_kind": "temporal",
            "field": "PolicyContract.contract_start",
            "operator": "days_since",
            "value": 90,
            "unit": "days",
        },
        {
            "kind": "condition",
            "rule_kind": "frequency",
            "field": "ClaimHistory.counted_occurrence",
            "operator": "count_before",
            "value": 2,
            "unit": "occurrences",
        },
    ],
)
def test_typed_conditions_compile_and_missing_facts_remain_unknown(payload: dict[str, Any]) -> None:
    from familycare_api.decisions.operators import evaluate_expression

    graph = synthetic_graph()
    graph["nodes"][3]["payload"] = payload
    root = compiled(graph).roots[0]
    assert len(root.rules) == 1
    validated = validate_rule_document(root.rules[0], root.citation_ids)
    assert validated.expression is not None
    facts = FactContext(medical_event={}, policy={}, rider={}, claim_history={})
    assert evaluate_expression(validated.expression, facts).result == "UNKNOWN"


def test_large_classification_table_uses_bounded_existing_boolean_dsl() -> None:
    graph = synthetic_graph()
    graph["nodes"][3]["payload"]["codes"] = [f"class-{index}" for index in range(40)]
    root = compiled(graph).roots[0]
    assert len(root.rules) == 1
    expression = root.rules[0]["expression"]
    assert expression["op"] == "any"
    assert [len(group["value"]) for group in expression["args"]] == [16, 16, 8]
