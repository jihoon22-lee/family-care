"""A source relation proves disjoint classification assignments, never addition."""

from copy import deepcopy
from dataclasses import replace
from uuid import UUID

import pytest
from familycare_api.guidance.case_relations import source_case_relation

from apps.api.tests.test_guidance_relevance import coverage, leaf

CLASS = "MedicalEvent.classification"
CODE = "MedicalEvent.diagnosis_code"


def case(value, *, field=CLASS, scopes=()):
    return coverage(
        leaf(field, value, "in" if isinstance(value, list) else "equals"), scopes=scopes
    )


def scope(*, field=CODE, system="synthetic-system", version="edition-1"):
    return ({"field": field, "code_system": system, "code_version": version},)


@pytest.mark.parametrize("values", [("class-a", "class-b"), (["class-a", "class-b"], ["class-c"])])
def test_disjoint_required_internal_classification_sets_are_mutually_exclusive(values):
    assert source_case_relation(tuple(case(value) for value in values)) == "MUTUALLY_EXCLUSIVE"


@pytest.mark.parametrize(
    "values", [("class-a", "class-a"), (["class-a", "class-b"], ["class-b", "class-c"])]
)
def test_overlapping_sets_are_not_an_additive_or_exclusive_proof(values):
    assert source_case_relation(tuple(case(value) for value in values)) == "UNRESOLVED"


def test_all_intersects_mandatory_constraints_before_comparing_other_cases():
    left = coverage(
        {
            "op": "all",
            "args": [
                leaf(CLASS, ["class-a", "class-b"], "in"),
                leaf(CLASS, ["class-b", "class-c"], "in"),
            ],
        }
    )
    assert source_case_relation((left, case("class-a"))) == "MUTUALLY_EXCLUSIVE"
    assert source_case_relation((left, case("class-b"))) == "UNRESOLVED"


def test_multiple_required_rules_are_a_conjunction_and_optional_rules_are_not_constraints():
    left = case(["class-a", "class-b"])
    narrow = replace(case(["class-b", "class-c"]).rules[0], rule_key="second-rule")
    constrained = replace(left, rules=(*left.rules, narrow))
    assert source_case_relation((constrained, case("class-a"))) == "MUTUALLY_EXCLUSIVE"
    optional = replace(
        narrow, required=False, rule_document=dict(narrow.rule_document, required=False)
    )
    assert (
        source_case_relation((replace(left, rules=(*left.rules, optional)), case("class-a")))
        == "UNRESOLVED"
    )


def test_any_unions_only_constraints_present_in_every_arm():
    left = coverage({"op": "any", "args": [leaf(CLASS, "class-a"), leaf(CLASS, "class-b")]})
    assert source_case_relation((left, case("class-c"))) == "MUTUALLY_EXCLUSIVE"
    assert source_case_relation((left, case("class-b"))) == "UNRESOLVED"
    unconstrained_arm = coverage(
        {"op": "any", "args": [leaf(CLASS, "class-a"), leaf("MedicalEvent.admission", True)]}
    )
    assert source_case_relation((unconstrained_arm, case("class-b"))) == "UNRESOLVED"


def test_positive_all_constraint_survives_an_unhandled_sibling_without_using_that_sibling():
    left = coverage(
        {
            "op": "all",
            "args": [
                leaf(CLASS, "class-a"),
                {"op": "not", "args": [leaf("MedicalEvent.admission", False)]},
            ],
        }
    )
    assert source_case_relation((left, case("class-b"))) == "MUTUALLY_EXCLUSIVE"


@pytest.mark.parametrize(
    "field,first,second",
    [
        ("MedicalEvent.admission", True, False),
        ("MedicalEvent.performed", True, False),
        ("MedicalEvent.admission_days", 1, 2),
        ("MedicalEvent.event_date", "2026-01-01", "2026-02-01"),
        ("MedicalEvent.treatment_kind", "admission", "surgery"),
        ("MedicalEvent.treatment_setting", "inpatient", "outpatient"),
        ("Rider.status", "active", "inactive"),
    ],
)
def test_activity_time_and_nonmedical_context_are_never_payout_exclusivity(field, first, second):
    assert (
        source_case_relation((case(first, field=field), case(second, field=field))) == "UNRESOLVED"
    )


def test_bare_negation_and_exclusion_rules_do_not_become_positive_classification_sets():
    first = coverage({"op": "not", "args": [leaf(CLASS, "class-a")]})
    second = coverage({"op": "not", "args": [leaf(CLASS, "class-b")]})
    assert source_case_relation((first, second)) == "UNRESOLVED"
    positive = case("class-a")
    rule = positive.rules[0]
    exclusion = replace(
        rule, rule_kind="exclusion", rule_document=dict(rule.rule_document, rule_kind="exclusion")
    )
    assert (
        source_case_relation((replace(positive, rules=(exclusion,)), case("class-b")))
        == "UNRESOLVED"
    )


@pytest.mark.parametrize(
    "right_scope",
    [
        scope(),
        (),
        scope(version="edition-2"),
        scope(system="other-system"),
        scope(field="MedicalEvent.procedure_code"),
    ],
)
def test_clinical_fields_require_identical_actual_system_and_version(right_scope):
    result = source_case_relation(
        (
            case("class-a", field=CODE, scopes=scope()),
            case("class-b", field=CODE, scopes=right_scope),
        )
    )
    assert result == ("MUTUALLY_EXCLUSIVE" if right_scope == scope() else "UNRESOLVED")


def test_two_unscoped_clinical_codes_and_different_fields_are_not_comparable():
    assert (
        source_case_relation((case("class-a", field=CODE), case("class-b", field=CODE)))
        == "UNRESOLVED"
    )
    assert (
        source_case_relation(
            (
                case("class-a", field=CODE, scopes=scope()),
                case(
                    "class-b",
                    field="MedicalEvent.procedure_code",
                    scopes=scope(field="MedicalEvent.procedure_code"),
                ),
            )
        )
        == "UNRESOLVED"
    )


def test_conflicting_scopes_for_one_field_are_unresolved_even_if_one_matches_another_case():
    left = case("class-a", field=CODE, scopes=(*scope(), *scope(version="edition-2")))
    assert source_case_relation((left, case("class-b", field=CODE, scopes=scope()))) == "UNRESOLVED"


def test_every_pair_must_be_exclusive_and_internally_impossible_cases_are_not_proofs():
    assert source_case_relation((case("class-a"), case("class-b"), case("class-a"))) == "UNRESOLVED"
    assert (
        source_case_relation((case("class-a"), case("class-b"), case("class-c")))
        == "MUTUALLY_EXCLUSIVE"
    )
    impossible = coverage(
        {"op": "all", "args": [leaf(CLASS, ["class-a"], "in"), leaf(CLASS, ["class-b"], "in")]}
    )
    assert source_case_relation((impossible, case("class-c"))) == "UNRESOLVED"


@pytest.mark.parametrize(
    "fault", ["lineage", "publication", "metadata", "missing-calculation", "partial", "source-kind"]
)
def test_unreliable_source_cannot_prove_alternative_payout_cases(fault):
    left, right = case("class-a"), case("class-b")
    rule = left.rules[0]
    if fault == "lineage":
        left = replace(
            left,
            rules=(replace(rule, citations=(replace(rule.citations[0], lineage_valid=False),)),),
        )
    elif fault == "publication":
        left = replace(left, rules=(replace(rule, publication_id=UUID(int=999)),))
    elif fault == "metadata":
        left = replace(left, rules=(replace(rule, required=1),))
    elif fault == "missing-calculation":
        left = replace(left, calculation=None)
    elif fault == "partial":
        left = replace(left, knowledge_incomplete=True)
    else:
        left = replace(left, rules=(replace(rule, source_kind="SEMANTIC_NODE"),))
    assert source_case_relation((left, right)) == "UNRESOLVED"


def test_empty_single_too_many_and_cyclic_inputs_fail_closed_without_mutation():
    first = case("class-a")
    before = deepcopy(first.rules[0].rule_document)
    assert source_case_relation(()) == "UNRESOLVED"
    assert source_case_relation((first,)) == "UNRESOLVED"
    assert source_case_relation((first,) * 33) == "UNRESOLVED"
    cyclic = {"op": "all"}
    cyclic["args"] = [cyclic]
    broken = replace(
        first.rules[0], rule_document=dict(first.rules[0].rule_document, expression=cyclic)
    )
    assert source_case_relation((replace(first, rules=(broken,)), case("class-b"))) == "UNRESOLVED"
    assert first.rules[0].rule_document == before


def test_zero_evidence_identity_is_not_a_reliable_classification_source():
    left = case("class-a")
    rule = left.rules[0]
    citation = rule.citations[0]
    citation = replace(
        citation, evidence=citation.evidence.model_copy(update={"evidence_id": UUID(int=0)})
    )
    assert (
        source_case_relation(
            (replace(left, rules=(replace(rule, citations=(citation,)),)), case("class-b"))
        )
        == "UNRESOLVED"
    )


def test_internal_and_versioned_classification_are_different_identities():
    assert (
        source_case_relation((case("class-a"), case("class-b", scopes=scope(field=CLASS))))
        == "UNRESOLVED"
    )


def test_global_rule_and_node_budgets_do_not_return_a_partial_exclusivity_proof():
    cases = tuple(case(f"class-{index}") for index in range(3))
    many_rules = tuple(
        replace(c, rules=tuple(replace(c.rules[0], rule_key=f"rule-{i}") for i in range(128)))
        for c in cases
    )
    assert source_case_relation(many_rules) == "UNRESOLVED"
    many_nodes = []
    for index in range(32):
        current = coverage({"op": "all", "args": [leaf(CLASS, f"class-{index}")] * 16})
        many_nodes.append(
            replace(
                current,
                rules=tuple(replace(current.rules[0], rule_key=f"rule-{i}") for i in range(8)),
            )
        )
    assert source_case_relation(tuple(many_nodes)) == "UNRESOLVED"


def test_large_any_union_is_not_silently_truncated_into_disjoint_sets():
    groups = [
        leaf(CLASS, [f"class-{i}" for i in range(start, start + 16)], "in")
        for start in range(0, 272, 16)
    ]
    large = coverage({"op": "any", "args": [{"op": "any", "args": groups[:16]}, groups[16]]})
    assert source_case_relation((large, case("class-999"))) == "UNRESOLVED"


def test_rules_cannot_be_borrowed_from_a_different_source_kind_than_the_case_formula():
    current = case("class-a")
    rule = current.rules[0]
    evidence = rule.citations[0].evidence.model_copy(update={"kind": "OPERATIONAL_EVIDENCE"})
    citation = replace(rule.citations[0], citation_key=str(evidence.evidence_id), evidence=evidence)
    foreign = replace(
        rule,
        source_kind="OPERATIONAL_RULE_VERSION",
        citations=(citation,),
        rule_document=dict(rule.rule_document, evidence_ids=[citation.citation_key]),
    )
    assert (
        source_case_relation((replace(current, rules=(foreign,)), case("class-b"))) == "UNRESOLVED"
    )
