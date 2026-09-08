"""Only positive event witnesses in still-possible rule branches establish relevance."""

from dataclasses import FrozenInstanceError, replace
from datetime import date
from uuid import UUID

import pytest
from familycare_api.decisions.knowledge_domain import (
    KnowledgeFact,
    KnowledgeFactContext,
    KnowledgeFactNormalizer,
)
from familycare_api.guidance.event_facts import CodeScope, build_event_facts
from familycare_api.guidance.interpretation import (
    InterpretedFact,
    SituationInterpretation,
    SourceSpan,
)
from familycare_api.guidance.private_adapter import adapt_private_guidance
from familycare_api.guidance.relevance import RelevanceUnavailable, find_relevance

from apps.api.tests.test_private_knowledge_engine import _context, _coverage, _event


def leaf(field, value, op="equals"):
    return {"op": op, "field": field, "value": value}


ADMISSION = leaf("MedicalEvent.admission", True)
CLASSIFICATION = leaf("MedicalEvent.classification", "sample_category")
DIAGNOSIS = leaf("MedicalEvent.diagnosis_code", "class-a")
DAYS = {
    "op": "range",
    "field": "MedicalEvent.admission_days",
    "value": {"min": 1, "max": 10},
    "unit": "days",
}
SCOPE = CodeScope("MedicalEvent.diagnosis_code", "synthetic-system", "edition-1")


def _fields(expression):
    return sorted(
        {expression["field"]}
        if "field" in expression
        else {path for child in expression["args"] for path in _fields(child)}
    )


def coverage(expression, *, scopes=()):
    original = adapt_private_guidance(_context(_coverage(1, "100"))).coverages[0]
    rule = original.rules[0]
    document = dict(
        rule.rule_document, expression=expression, input_field_paths=_fields(expression)
    )
    return replace(
        original, rules=(replace(rule, rule_document=document, classification_scopes=scopes),)
    )


def read(text="", *, normalizers=(), structured_facts=(), activity=None):
    return build_event_facts(
        replace(_event(), facts={}, situation=text, structured_facts=structured_facts),
        normalizers,
        selected_subject_terms=("Family Member A",),
        other_subject_terms=("Family Member B",),
        activity=activity,
    )


def run(
    expression,
    values=None,
    *,
    event_read=None,
    scopes=(),
    normalizer_code_scopes=None,
    activity=None,
):
    event_read = event_read or read()
    facts = KnowledgeFactContext({**event_read.context.facts, **(values or {})}, ())
    return find_relevance(
        facts,
        coverage(expression, scopes=scopes),
        event_read,
        normalizer_code_scopes=normalizer_code_scopes,
        activity=activity,
    )


def topic_read(text, *, field="MedicalEvent.classification", value="sample_category"):
    return read(
        text,
        normalizers=(KnowledgeFactNormalizer("reviewed", field, ("violet", "delta"), value, 10),),
    )


def test_positive_admission_leaf_survives_unknown_compound_condition():
    result = run(
        {"op": "all", "args": [ADMISSION, CLASSIFICATION]},
        {
            "MedicalEvent.admission": KnowledgeFact(True, "USER_CONFIRMED"),
        },
    )
    assert len(result) == 1
    assert result[0].field_path == "MedicalEvent.admission"
    assert result[0].kind == "CONFIRMED_EVENT"


def test_decisive_sibling_mismatch_blocks_the_entire_all_branch():
    assert not run(
        {"op": "all", "args": [ADMISSION, CLASSIFICATION]},
        {
            "MedicalEvent.admission": KnowledgeFact(True, "USER_CONFIRMED"),
            "MedicalEvent.classification": KnowledgeFact("unrelated_category", "USER_CONFIRMED"),
        },
    )


def test_any_preserves_only_still_possible_arms():
    expression = {
        "op": "any",
        "args": [
            {"op": "all", "args": [ADMISSION, CLASSIFICATION]},
            leaf("MedicalEvent.treatment_setting", "outpatient"),
        ],
    }
    result = run(
        expression,
        {
            "MedicalEvent.admission": KnowledgeFact(True, "USER_CONFIRMED"),
            "MedicalEvent.classification": KnowledgeFact("unrelated_category", "USER_CONFIRMED"),
            "MedicalEvent.treatment_setting": KnowledgeFact("outpatient", "USER_CONFIRMED"),
        },
    )
    assert [item.field_path for item in result] == ["MedicalEvent.treatment_setting"]


@pytest.mark.parametrize(
    "expression,values",
    [
        (
            {"op": "not", "args": [leaf("MedicalEvent.admission", False)]},
            {"MedicalEvent.admission": KnowledgeFact(True, "USER_CONFIRMED")},
        ),
        (
            leaf("MedicalEvent.admission", False),
            {"MedicalEvent.admission": KnowledgeFact(False, "USER_CONFIRMED")},
        ),
        (
            {"op": "present", "field": "MedicalEvent.admission"},
            {"MedicalEvent.admission": KnowledgeFact(False, "USER_CONFIRMED")},
        ),
        (
            leaf("MedicalEvent.event_date", "2026-06-01"),
            {"MedicalEvent.event_date": KnowledgeFact(date(2026, 6, 1), "USER_CONFIRMED")},
        ),
        (
            {
                "op": "count_before",
                "field": "ClaimHistory.counted_occurrence",
                "value": 1,
                "unit": "occurrences",
            },
            {"ClaimHistory.counted_occurrence": KnowledgeFact(1, "USER_CONFIRMED")},
        ),
        (leaf("Rider.status", "active"), {}),
        (
            leaf("MedicalEvent.admission_days", 0),
            {"MedicalEvent.admission_days": KnowledgeFact(0, "USER_CONFIRMED")},
        ),
    ],
)
def test_negative_only_and_context_only_rules_do_not_fill_candidate_lists(expression, values):
    assert not run(expression, values)


@pytest.mark.parametrize(
    "provenance,stale",
    [
        ("AI_SUGGESTED", False),
        ("UNCONFIRMED", False),
        ("CONFLICTING", False),
        ("USER_CONFIRMED", True),
    ],
)
def test_untrusted_or_stale_value_cannot_be_confirmed_relevance(provenance, stale):
    assert not run(
        ADMISSION, {"MedicalEvent.admission": KnowledgeFact(True, provenance, stale=stale)}
    )


def test_ai_suggested_mismatch_is_unknown_and_cannot_suppress_real_admission():
    result = run(
        {"op": "all", "args": [ADMISSION, CLASSIFICATION]},
        {
            "MedicalEvent.admission": KnowledgeFact(True, "USER_CONFIRMED"),
            "MedicalEvent.classification": KnowledgeFact("different_category", "AI_SUGGESTED"),
        },
    )
    assert [item.field_path for item in result] == ["MedicalEvent.admission"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("violet delta", True),
        ("violet deltas", False),
        ("not violet delta", False),
        ("Family Member B had violet delta.", False),
        ("2025-01-02 violet delta.", False),
    ],
)
def test_reviewed_exact_topic_keeps_subject_negation_and_time_guards(text, expected):
    event_read = topic_read(text)
    result = run(CLASSIFICATION, event_read=event_read)
    assert bool(result) is expected
    if expected:
        assert result[0].kind == "LOCAL_TOPIC"
        assert result[0].normalizer_key == "reviewed"
        assert result[0].spans == (event_read.topics[0].span,)
        assert not event_read.context.get("MedicalEvent.classification").is_trusted


def test_topic_value_requires_exact_equals_or_in_and_cannot_override_explicit_no_match():
    event_read = topic_read("violet delta", value="sample_category_suffix")
    assert not run(CLASSIFICATION, event_read=event_read)
    event_read = topic_read("violet delta")
    assert not run(
        CLASSIFICATION,
        {"MedicalEvent.classification": KnowledgeFact("other_category", "USER_CONFIRMED")},
        event_read=event_read,
    )
    assert not run({"op": "present", "field": "MedicalEvent.classification"}, event_read=event_read)
    assert (
        run(
            leaf("MedicalEvent.classification", ["sample_category", "another_category"], "in"),
            event_read=event_read,
        )[0].kind
        == "LOCAL_TOPIC"
    )


def _rule_scope(scope=SCOPE):
    return (
        {
            "field": scope.field_path,
            "code_system": scope.code_system,
            "code_version": scope.code_version,
        },
    )


@pytest.mark.parametrize(
    "case",
    ["matching", "missing-topic", "wrong-system", "wrong-version", "wrong-field", "missing-rule"],
)
def test_clinical_topic_requires_the_same_reviewed_code_system_and_edition(case):
    event_read = topic_read("violet delta", field=SCOPE.field_path, value="class-a")
    requested = SCOPE
    if case == "wrong-system":
        requested = CodeScope(SCOPE.field_path, "other-system", SCOPE.code_version)
    elif case == "wrong-version":
        requested = CodeScope(SCOPE.field_path, SCOPE.code_system, "edition-2")
    elif case == "wrong-field":
        requested = CodeScope("MedicalEvent.procedure_code", SCOPE.code_system, SCOPE.code_version)
    result = run(
        DIAGNOSIS,
        event_read=event_read,
        scopes=() if case == "missing-rule" else _rule_scope(),
        normalizer_code_scopes={} if case == "missing-topic" else {"reviewed": requested},
    )
    assert bool(result) is (case == "matching")


@pytest.mark.parametrize(
    "version,expected", [("edition-1", True), ("edition-2", False), (None, False)]
)
def test_actual_clinical_code_is_scoped_before_relevance(version, expected):
    structured = {
        "field_id": "diagnosis_code",
        "value": "class-a",
        "source": "user",
        "state": "confirmed",
        "evidence_ids": [],
    }
    if version is not None:
        structured.update(code_system=SCOPE.code_system, code_version=version)
    event_read = read(structured_facts=(structured,))
    result = run(DIAGNOSIS, event_read=event_read, scopes=_rule_scope())
    assert bool(result) is expected
    assert event_read.context.get(SCOPE.field_path).value == "class-a"


def test_planned_five_days_supply_only_scenario_relevance_with_original_spans():
    event_read = read("5일간 입원 예정입니다.")
    result = run(DAYS, event_read=event_read)
    assert len(result) == 1 and result[0].kind == "PLANNED_EVENT"
    scenario = next(
        s for s in event_read.scenarios if s.field_path == "MedicalEvent.admission_days"
    )
    assert result[0].spans == scenario.spans
    assert event_read.context.get("MedicalEvent.admission_days").value is None


def test_explicit_non_admission_blocks_planned_admission_and_dependent_days():
    event_read = read(
        "5일간 입원 예정입니다.",
        structured_facts=(
            {
                "field_id": "admission",
                "value": False,
                "source": "user",
                "state": "confirmed",
            },
        ),
    )
    assert not run(ADMISSION, event_read=event_read)
    assert not run(DAYS, event_read=event_read)


@pytest.mark.parametrize("activity", [None, "admission", "outpatient", "surgery"])
def test_planned_performed_requires_explicit_surgery_activity_binding(activity):
    result = run(
        leaf("MedicalEvent.performed", True), event_read=read("수술 예정입니다."), activity=activity
    )
    assert bool(result) is (activity == "surgery")


@pytest.mark.parametrize(
    "fault",
    ["lineage", "metadata", "duplicate-citation", "publication", "unsupported-kind", "invalid-dsl"],
)
def test_unvalidated_rule_or_citation_never_produces_relevance(fault):
    cov = coverage(ADMISSION)
    rule = cov.rules[0]
    if fault == "lineage":
        rule = replace(rule, citations=(replace(rule.citations[0], lineage_valid=False),))
    elif fault == "metadata":
        rule = replace(rule, required=False)
    elif fault == "duplicate-citation":
        rule = replace(rule, citations=rule.citations * 2)
    elif fault == "publication":
        rule = replace(rule, publication_id=UUID(int=999))
    elif fault == "unsupported-kind":
        rule = replace(
            rule,
            rule_kind="exclusion",
            rule_document=dict(rule.rule_document, rule_kind="exclusion"),
        )
    else:
        rule = replace(rule, rule_document=dict(rule.rule_document, expression={"op": "invented"}))
    assert not find_relevance(
        KnowledgeFactContext({"MedicalEvent.admission": KnowledgeFact(True, "USER_CONFIRMED")}, ()),
        replace(cov, rules=(rule,)),
        read(),
    )


def test_match_preserves_source_identity_and_never_mutates_input_facts_or_rules():
    event_read = read("입원했습니다.")
    cov = coverage(ADMISSION)
    before = dict(event_read.context.facts)
    result = find_relevance(event_read.context, cov, event_read)
    assert result[0].publication_id == cov.rules[0].publication_id
    assert result[0].rule_key == cov.rules[0].rule_key
    assert result[0].citations == cov.rules[0].citations
    assert result[0].spans == next(
        f.spans for f in event_read.interpretation.facts if f.field_path == "MedicalEvent.admission"
    )
    assert dict(event_read.context.facts) == before
    with pytest.raises(FrozenInstanceError):
        result[0].kind = "LOCAL_TOPIC"
    assert "MedicalEvent.admission" not in repr(result[0])


def test_integer_required_metadata_is_not_a_boolean_approval():
    cov = coverage(ADMISSION)
    assert not find_relevance(
        KnowledgeFactContext({"MedicalEvent.admission": KnowledgeFact(True, "USER_CONFIRMED")}, ()),
        replace(cov, rules=(replace(cov.rules[0], required=1),)),
        read(),
    )


def test_any_sibling_with_only_known_mismatches_blocks_its_all_parent():
    expression = {
        "op": "all",
        "args": [
            ADMISSION,
            {
                "op": "any",
                "args": [
                    CLASSIFICATION,
                    leaf("MedicalEvent.classification", "another_category"),
                ],
            },
        ],
    }
    assert not run(
        expression,
        {
            "MedicalEvent.admission": KnowledgeFact(True, "USER_CONFIRMED"),
            "MedicalEvent.classification": KnowledgeFact("unrelated_category", "USER_CONFIRMED"),
        },
    )


def test_planned_admission_boolean_keeps_the_missing_completed_care_fact():
    event_read = read("입원 예정입니다.")
    result = run(ADMISSION, event_read=event_read)
    assert result[0].kind == "PLANNED_EVENT"
    assert event_read.context.get("MedicalEvent.admission").value is None
    assert event_read.scenarios[0].value is None


@pytest.mark.parametrize(
    "text",
    [
        "입원하지 않을 예정입니다.",
        "Family Member B plans admission.",
        "2025-01-02 입원 예정입니다.",
    ],
)
def test_negative_other_subject_and_other_date_plans_are_not_hypotheses(text):
    assert not run(ADMISSION, event_read=read(text))


def test_planned_topic_cannot_override_a_decisive_different_code_in_the_same_arm():
    event_read = topic_read("violet delta 예정입니다.")
    assert not run(
        {"op": "all", "args": [CLASSIFICATION, ADMISSION]},
        {
            "MedicalEvent.admission": KnowledgeFact(False, "USER_CONFIRMED"),
        },
        event_read=event_read,
    )


def test_clinical_topic_and_explicit_scoped_code_do_not_bypass_each_other():
    event_read = topic_read("violet delta", field=SCOPE.field_path, value="class-a")
    event_read = replace(event_read, code_scopes=(SCOPE,))
    assert not run(
        DIAGNOSIS,
        {SCOPE.field_path: KnowledgeFact("class-b", "USER_CONFIRMED")},
        event_read=event_read,
        scopes=_rule_scope(),
        normalizer_code_scopes={"reviewed": SCOPE},
    )


def test_declared_classification_code_is_not_reinterpreted_as_an_unversioned_enum():
    scope = CodeScope("MedicalEvent.classification", "synthetic-classification", "edition-1")
    event_read = read(
        structured_facts=(
            {
                "field_id": "condition_class",
                "value": "sample_category",
                "source": "user",
                "state": "confirmed",
                "code_system": scope.code_system,
                "code_version": scope.code_version,
            },
        )
    )
    assert not run(CLASSIFICATION, event_read=event_read)


def test_valid_other_rule_survives_a_cyclic_unvalidated_document():
    cov = coverage(ADMISSION)
    cyclic = {"op": "all"}
    cyclic["args"] = [cyclic]
    invalid = replace(
        cov.rules[0],
        rule_key="cyclic",
        rule_document=dict(cov.rules[0].rule_document, expression=cyclic),
    )
    result = find_relevance(
        KnowledgeFactContext({"MedicalEvent.admission": KnowledgeFact(True, "USER_CONFIRMED")}, ()),
        replace(cov, rules=(invalid, *cov.rules)),
        read(),
    )
    assert len(result) == 1 and result[0].rule_key == cov.rules[0].rule_key


def test_budget_exhaustion_is_an_unavailable_read_instead_of_a_no_match():
    cov = coverage({"op": "all", "args": [ADMISSION] * 16})
    facts = KnowledgeFactContext(
        {"MedicalEvent.admission": KnowledgeFact(True, "USER_CONFIRMED")}, ()
    )
    with pytest.raises(RelevanceUnavailable, match="^GUIDANCE_RELEVANCE_BUDGET_EXCEEDED$"):
        find_relevance(facts, replace(cov, rules=cov.rules * 128), read())


def test_unreviewed_certificate_amount_cannot_be_a_decisive_sibling():
    expression = {"op": "all", "args": [ADMISSION, leaf("Rider.insured_amount", 200)]}
    cov = replace(
        coverage(expression),
        certificate_amount_decision="UNKNOWN",
        certificate_amount_evidence_state="UNAVAILABLE",
    )
    result = find_relevance(
        KnowledgeFactContext({"MedicalEvent.admission": KnowledgeFact(True, "USER_CONFIRMED")}, ()),
        cov,
        read(),
    )
    assert [item.field_path for item in result] == ["MedicalEvent.admission"]


def test_scenario_records_cannot_manufacture_clinical_code_relevance():
    scenario = InterpretedFact(
        SCOPE.field_path,
        "class-a",
        "SCENARIO",
        (SourceSpan(0, 5),),
        ("LOCAL_ACTIVITY_PLANNED",),
        "surgery",
    )
    event_read = replace(
        read(), scenarios=(scenario,), interpretation=SituationInterpretation((scenario,), ())
    )
    assert not run(DIAGNOSIS, event_read=event_read, scopes=_rule_scope(), activity="surgery")


def test_topic_guard_reason_cannot_be_overridden_by_an_inconsistent_positive_flag():
    event_read = topic_read("violet delta")
    event_read = replace(
        event_read,
        topics=(replace(event_read.topics[0], reason_codes=("LOCAL_ACTIVITY_NEGATED",)),),
    )
    assert not run(CLASSIFICATION, event_read=event_read)


def test_dsl_uuid_evidence_values_preserve_the_existing_operator_contract():
    cov = coverage(ADMISSION)
    rule = cov.rules[0]
    identifier = UUID(int=99)
    citation = replace(rule.citations[0], citation_key=str(identifier))
    updated = replace(
        rule,
        citations=(citation,),
        rule_document=dict(rule.rule_document, evidence_ids=[identifier]),
    )
    result = find_relevance(
        KnowledgeFactContext({"MedicalEvent.admission": KnowledgeFact(True, "USER_CONFIRMED")}, ()),
        replace(cov, rules=(updated,)),
        read(),
    )
    assert result[0].citations == (citation,)


@pytest.mark.parametrize("wrong_root", [False, True])
def test_semantic_relevance_retains_original_publication_and_exact_root_citations(wrong_root):
    from familycare_api.guidance.semantic_binding import bind_semantic_root

    from apps.api.tests.test_guidance_semantic_binding import source_and_root

    snapshot, clause, current = source_and_root()
    bound = bind_semantic_root(current, clause, snapshot.source.model_dump())
    cov = replace(coverage(ADMISSION), rules=bound.rules)
    if wrong_root:
        cov = replace(
            cov, rules=tuple(replace(rule, semantic_node_id="other-root") for rule in cov.rules)
        )
    event_read = read("5일간 입원했습니다.")
    result = find_relevance(event_read.context, cov, event_read)
    if wrong_root:
        assert not result
        return
    assert result and all(item.source_kind == "SEMANTIC_NODE" for item in result)
    assert all(item.publication_id == current.publication_id for item in result)
    assert all(item.semantic_node_id == current.root.root_node_id for item in result)
    assert all(c.evidence.kind == "SEMANTIC_CITATION" for item in result for c in item.citations)


def test_operational_relevance_preserves_its_actual_evidence_identity():
    cov = coverage(ADMISSION)
    original = cov.rules[0]
    evidence = original.citations[0].evidence.model_copy(update={"kind": "OPERATIONAL_EVIDENCE"})
    citation = replace(
        original.citations[0], citation_key=str(evidence.evidence_id), evidence=evidence
    )
    rule = replace(
        original,
        source_kind="OPERATIONAL_RULE_VERSION",
        citations=(citation,),
        rule_document=dict(original.rule_document, evidence_ids=[citation.citation_key]),
    )
    event_read = read("입원했습니다.")
    result = find_relevance(event_read.context, replace(cov, rules=(rule,)), event_read)
    assert result[0].source_kind == "OPERATIONAL_RULE_VERSION"
    assert result[0].citations == (citation,)
