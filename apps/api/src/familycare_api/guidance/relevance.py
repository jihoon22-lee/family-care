"""Find positive event witnesses without turning topics or plans into confirmed facts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

from familycare_api.clauses.dsl import (
    CompiledExpression,
    RuleValidationError,
    validate_rule_document,
)
from familycare_api.decisions.domain import FactContext, FactValue
from familycare_api.decisions.knowledge_domain import KnowledgeFact, KnowledgeFactContext
from familycare_api.decisions.knowledge_engine import _legacy_fact_context
from familycare_api.decisions.operators import OperatorEvaluationError, evaluate_expression
from familycare_api.guidance.domain import (
    GuidanceCitation,
    GuidanceCoverageInput,
    GuidanceRuleInput,
)
from familycare_api.guidance.event_facts import (
    _INELIGIBLE_TOPIC_REASONS,
    CodeScope,
    EventFactRead,
    NormalizerTopic,
    scope_code_facts,
)
from familycare_api.guidance.interpretation import Activity, InterpretedFact, SourceSpan
from familycare_api.guidance.models import GuidanceEvidence, GuidanceSemanticEvidence

MAX_RELEVANCE_RULES = 128
MAX_RELEVANCE_MATCHES = 64
MAX_RELEVANCE_AST_NODES = 2048
MAX_RELEVANCE_COMPARISONS = 8192
_KINDS = frozenset({"eligibility", "classification", "indemnity_eligibility"})
_GUARD_FAILURES = _INELIGIBLE_TOPIC_REASONS | {"LOCAL_ACTIVITY_NEGATED", "LOCAL_FACT_CONFLICT"}
_BOOLEAN_FIELDS = frozenset(
    {
        "MedicalEvent.admission",
        "MedicalEvent.outpatient",
        "MedicalEvent.pharmacy",
        "MedicalEvent.performed",
        "MedicalEvent.diagnosis_confirmed",
        "MedicalEvent.separately_billed_treatment",
    }
)
_STRING_FIELDS = frozenset(
    {
        "MedicalEvent.classification",
        "MedicalEvent.diagnosis_code",
        "MedicalEvent.procedure_code",
        "MedicalEvent.pathology_code",
        "MedicalEvent.anatomical_site_code",
        "MedicalEvent.treatment_kind",
        "MedicalEvent.treatment_setting",
        "MedicalEvent.treatment_context",
    }
)
_CODE_FIELDS = frozenset(
    {
        "MedicalEvent.classification",
        "MedicalEvent.diagnosis_code",
        "MedicalEvent.procedure_code",
        "MedicalEvent.pathology_code",
        "MedicalEvent.anatomical_site_code",
    }
)


class RelevanceUnavailable(ValueError):
    """An invalid or exhausted read is not a deterministic payout mismatch."""

    def __init__(self, code: str = "GUIDANCE_RELEVANCE_INPUT_INVALID") -> None:
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class RelevanceMatch:
    publication_id: UUID
    rule_key: str
    citations: tuple[GuidanceCitation, ...]
    source_kind: Literal["PRIVATE_RULE_PUBLICATION", "OPERATIONAL_RULE_VERSION", "SEMANTIC_NODE"]
    semantic_node_id: str | None
    kind: Literal["CONFIRMED_EVENT", "LOCAL_TOPIC", "PLANNED_EVENT"]
    field_path: str
    spans: tuple[SourceSpan, ...] = ()
    normalizer_key: str | None = None


@dataclass(slots=True, repr=False)
class _Budget:
    nodes: int = MAX_RELEVANCE_AST_NODES
    comparisons: int = MAX_RELEVANCE_COMPARISONS

    def take(self, *, node: bool = False) -> None:
        if node:
            self.nodes -= 1
        else:
            self.comparisons -= 1
        if self.nodes < 0 or self.comparisons < 0:
            raise RelevanceUnavailable("GUIDANCE_RELEVANCE_BUDGET_EXCEEDED")


def _bounded_document(document: Mapping[str, object]) -> None:
    pending: list[tuple[object, int]] = [(document, 0)]
    remaining = 4096
    while pending:
        value, depth = pending.pop()
        remaining -= 1
        if remaining < 0 or depth > 24:
            raise RuleValidationError("INVALID_ARGUMENTS")
        if isinstance(value, Mapping):
            if len(value) > 32 or any(type(key) is not str for key in value):
                raise RuleValidationError("INVALID_ARGUMENTS")
            pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list | tuple):
            if len(value) > 32:
                raise RuleValidationError("INVALID_ARGUMENTS")
            pending.extend((child, depth + 1) for child in value)
        elif type(value) is str:
            if len(value) > 8192:
                raise RuleValidationError("INVALID_ARGUMENTS")
        elif value is not None and type(value) not in (int, bool, float, Decimal, UUID):
            raise RuleValidationError("INVALID_ARGUMENTS")


def _valid_spans(spans: tuple[SourceSpan, ...]) -> bool:
    return (
        isinstance(spans, tuple)
        and 1 <= len(spans) <= 128
        and all(
            isinstance(span, SourceSpan)
            and type(span.start) is int
            and type(span.end) is int
            and 0 <= span.start < span.end <= 2000
            for span in spans
        )
    )


def _positive(field: str, value: object) -> bool:
    if field in _BOOLEAN_FIELDS:
        return value is True
    if field == "MedicalEvent.admission_days":
        return type(value) is int and 0 < value <= 36500
    return (
        field in _STRING_FIELDS
        and type(value) is str
        and 0 < len(value) <= 160
        and bool(value.strip())
    )


def _same(left: object, right: object) -> bool:
    return type(left) is type(right) and left == right


def _strict_facts(facts: KnowledgeFactContext) -> KnowledgeFactContext:
    return KnowledgeFactContext(
        {
            path: fact if fact.is_trusted and not fact.stale else KnowledgeFact(None, "UNCONFIRMED")
            for path, fact in facts.facts.items()
        },
        facts.audit_conflicts,
    )


def _strict_bridge(facts: KnowledgeFactContext, coverage: GuidanceCoverageInput) -> FactContext:
    context = _legacy_fact_context(facts, coverage)

    # The legacy bridge also adds Rider/history inputs; ai_structured never becomes
    # a decisive sibling outcome in this independent relevance read.
    def trusted(values: Mapping[str, FactValue]) -> dict[str, FactValue]:
        return {
            path: fact
            if fact.confirmation == "user" and not fact.evidence_stale
            else FactValue(None, "unconfirmed", ())
            for path, fact in values.items()
        }

    return replace(
        context,
        medical_event=trusted(context.medical_event),
        policy=trusted(context.policy),
        rider=trusted(context.rider),
        claim_history=trusted(context.claim_history),
        receipt=trusted(context.receipt),
    )


def _rule_input(
    rule: GuidanceRuleInput,
) -> tuple[CompiledExpression, tuple[CodeScope, ...], tuple[GuidanceCitation, ...]]:
    if not isinstance(rule, GuidanceRuleInput) or rule.rule_kind not in _KINDS:
        raise RuleValidationError("INVALID_RULE_KIND")
    if rule.terms_applicability != "MATCH" and not (
        rule.terms_applicability == "UNKNOWN"
        and rule.source_kind == "OPERATIONAL_RULE_VERSION"
        and rule.uncertain_terms_relevance_supported is True
    ):
        raise RuleValidationError("TERMS_APPLICABILITY_UNRESOLVED")
    if not isinstance(rule.publication_id, UUID) or not rule.publication_id.int:
        raise RuleValidationError("RULE_METADATA_MISMATCH")
    if type(rule.rule_key) is not str or not 1 <= len(rule.rule_key) <= 256:
        raise RuleValidationError("RULE_METADATA_MISMATCH")
    if not isinstance(rule.citations, tuple) or not 1 <= len(rule.citations) <= 64:
        raise RuleValidationError("MISSING_EVIDENCE")
    keys = []
    for citation in rule.citations:
        if not isinstance(citation, GuidanceCitation) or citation.lineage_valid is not True:
            raise RuleValidationError("CITATION_INVALID")
        evidence = citation.evidence
        if (
            not isinstance(evidence, GuidanceEvidence | GuidanceSemanticEvidence)
            or evidence.publication_id != rule.publication_id
        ):
            raise RuleValidationError("CITATION_INVALID")
        if (
            rule.source_kind == "SEMANTIC_NODE"
            and (
                not isinstance(evidence, GuidanceSemanticEvidence)
                or rule.semantic_node_id != evidence.root_node_id
                or citation.citation_key != str(evidence.citation_id)
            )
            or rule.source_kind == "PRIVATE_RULE_PUBLICATION"
            and evidence.kind != "TERMS_SECTION"
            or rule.source_kind == "OPERATIONAL_RULE_VERSION"
            and (
                not isinstance(evidence, GuidanceEvidence)
                or evidence.kind != "OPERATIONAL_EVIDENCE"
                or citation.citation_key != str(evidence.evidence_id)
            )
            or rule.source_kind
            not in {"SEMANTIC_NODE", "PRIVATE_RULE_PUBLICATION", "OPERATIONAL_RULE_VERSION"}
        ):
            raise RuleValidationError("CITATION_INVALID")
        keys.append(citation.citation_key)
    if len(set(keys)) != len(keys):
        raise RuleValidationError("DUPLICATE_EVIDENCE")
    _bounded_document(rule.rule_document)
    validated = validate_rule_document(rule.rule_document, keys)
    if (
        validated.expression is None
        or validated.required is not rule.required
        or (validated.rule_kind, validated.required, validated.result_reason_code)
        != (rule.rule_kind, rule.required, rule.result_reason_code)
    ):
        raise RuleValidationError("RULE_METADATA_MISMATCH")
    if not isinstance(rule.classification_scopes, tuple) or len(rule.classification_scopes) > 32:
        raise RuleValidationError("RULE_METADATA_MISMATCH")
    scopes = tuple(
        CodeScope(s["field"], s["code_system"], s["code_version"])
        for s in rule.classification_scopes
    )
    used = {str(key) for key in validated.evidence_ids}
    return validated.expression, scopes, tuple(c for c in rule.citations if c.citation_key in used)


def _scoped_facts(
    facts: KnowledgeFactContext, event_read: EventFactRead, scopes: tuple[CodeScope, ...]
) -> KnowledgeFactContext:
    scoped = scope_code_facts(replace(event_read, context=facts), scopes).context
    # The historical internal classification enum has no clinical code identity.
    path = "MedicalEvent.classification"
    if (
        not any(s.field_path == path for s in (*scopes, *event_read.code_scopes))
        and path in facts.facts
    ):
        return replace(scoped, facts={**scoped.facts, path: facts.facts[path]})
    return scoped


def _topic_scope(
    topic: NormalizerTopic, scopes: tuple[CodeScope, ...], metadata: Mapping[str, CodeScope]
) -> bool:
    if topic.field_path not in _CODE_FIELDS:
        return True
    required = {s for s in scopes if s.field_path == topic.field_path}
    declared = metadata.get(topic.normalizer_key)
    if topic.field_path == "MedicalEvent.classification" and not required and declared is None:
        return True
    return len(required) == 1 and declared in required


def _scenario_allowed(
    scenario: InterpretedFact,
    read: EventFactRead,
    facts: KnowledgeFactContext,
    activity: Activity | None,
) -> bool:
    event_activity = {
        "MedicalEvent.admission": "admission",
        "MedicalEvent.admission_days": "admission",
        "MedicalEvent.outpatient": "outpatient",
        "MedicalEvent.performed": "surgery",
    }.get(scenario.field_path)
    if scenario.field_path == "MedicalEvent.treatment_kind" and scenario.value in {
        "admission",
        "outpatient",
        "surgery",
    }:
        event_activity = cast(str, scenario.value)
    if (
        event_activity is None
        or scenario.activity != event_activity
        or scenario.state != "SCENARIO"
        or scenario.provenance != "EXPLICIT_LOCAL"
        or scenario not in read.interpretation.facts
        or not _valid_spans(scenario.spans)
        or bool(set(scenario.reason_codes) & _GUARD_FAILURES)
        or (
            scenario.field_path == "MedicalEvent.performed"
            and (activity != "surgery" or scenario.activity != "surgery")
        )
    ):
        return False
    prerequisite = {
        "admission": "MedicalEvent.admission",
        "outpatient": "MedicalEvent.outpatient",
        "surgery": "MedicalEvent.performed",
    }.get(scenario.activity)
    fact = facts.get(prerequisite) if prerequisite is not None else None
    return fact is None or not fact.is_trusted or fact.value is not False


def _scenario_value(scenario: InterpretedFact) -> object:
    if (
        scenario.value is None
        and "LOCAL_ACTIVITY_PLANNED" in scenario.reason_codes
        and (
            (scenario.field_path, scenario.activity)
            in {
                ("MedicalEvent.admission", "admission"),
                ("MedicalEvent.outpatient", "outpatient"),
                ("MedicalEvent.performed", "surgery"),
            }
        )
    ):
        # This is the event-under-consideration hypothesis, never a completed-care fact.
        return True
    return scenario.value


def _find_rule_matches(
    rule: GuidanceRuleInput,
    strict: KnowledgeFactContext,
    coverage: GuidanceCoverageInput,
    event_read: EventFactRead,
    metadata: Mapping[str, CodeScope],
    activity: Activity | None,
    budget: _Budget,
) -> tuple[RelevanceMatch, ...]:
    expression, scopes, citations = _rule_input(rule)
    scoped = _scoped_facts(strict, event_read, scopes)
    bridge = _strict_bridge(scoped, coverage)
    matches: list[RelevanceMatch] = []

    def add(
        kind: Literal["CONFIRMED_EVENT", "LOCAL_TOPIC", "PLANNED_EVENT"],
        field: str,
        spans: tuple[SourceSpan, ...] = (),
        normalizer: str | None = None,
    ) -> None:
        matches.append(
            RelevanceMatch(
                rule.publication_id,
                rule.rule_key,
                citations,
                rule.source_kind,
                rule.semantic_node_id,
                kind,
                field,
                spans,
                normalizer,
            )
        )
        if len(matches) > MAX_RELEVANCE_MATCHES:
            raise RelevanceUnavailable("GUIDANCE_RELEVANCE_BUDGET_EXCEEDED")

    def walk(node: CompiledExpression) -> None:
        budget.take(node=True)
        actual = evaluate_expression(node, bridge).result
        if actual == "NO_MATCH" or node.operator == "not":
            return
        if node.operator in {"all", "any"}:
            for child in node.operands:
                walk(cast(CompiledExpression, child))
            return
        field = cast(str, node.operands[0])
        if node.operator not in {"equals", "in", "range", "present"}:
            return
        fact = scoped.get(field)
        if (
            actual == "MATCH"
            and fact is not None
            and fact.is_trusted
            and _positive(field, fact.value)
        ):
            spans = tuple(
                span
                for item in event_read.interpretation.facts
                if (
                    field in event_read.local_fact_paths
                    and item.field_path == field
                    and item.state == "CONFIRMED"
                    and _same(item.value, fact.value)
                    and scoped.get(field) == event_read.context.get(field)
                    and _valid_spans(item.spans)
                )
                for span in item.spans
            )
            add("CONFIRMED_EVENT", field, spans)
            return
        if actual != "UNKNOWN":
            return
        if node.operator in {"equals", "in"}:
            expected = (
                (node.operands[1],)
                if node.operator == "equals"
                else cast(tuple[object, ...], node.operands[1])
            )
            for topic in event_read.topics:
                budget.take()
                if (
                    topic.field_path == field
                    and topic.relevance_allowed is True
                    and topic.scope_state in {"AFFIRMED", "PLANNED", "UNKNOWN"}
                    and not bool(set(topic.reason_codes) & _GUARD_FAILURES)
                    and _valid_spans((topic.span,))
                    and _positive(field, topic.normalized_value)
                    and _topic_scope(topic, scopes, metadata)
                    and any(_same(topic.normalized_value, value) for value in expected)
                    and (field != "MedicalEvent.performed" or activity == "surgery")
                ):
                    add("LOCAL_TOPIC", field, (topic.span,), topic.normalizer_key)
        for scenario in event_read.scenarios:
            budget.take()
            value = _scenario_value(scenario)
            if (
                scenario.field_path != field
                or not _positive(field, value)
                or not _scenario_allowed(scenario, event_read, scoped, activity)
            ):
                continue
            hypothetical = replace(
                bridge,
                medical_event={**bridge.medical_event, field: FactValue(value, "user", ())},
            )
            if evaluate_expression(node, hypothetical).result == "MATCH":
                add("PLANNED_EVENT", field, scenario.spans)

    walk(expression)
    return tuple(matches)


def find_relevance(
    facts: KnowledgeFactContext,
    coverage: GuidanceCoverageInput,
    event_read: EventFactRead,
    *,
    normalizer_code_scopes: Mapping[str, CodeScope] | None = None,
    activity: Activity | None = None,
) -> tuple[RelevanceMatch, ...]:
    """Return witnesses only; rule outcomes and original facts are never rewritten.

    No result means no supported witness, not a payout NO_MATCH. Budget exhaustion
    raises RelevanceUnavailable so callers retain the source-read failure.
    """
    if (
        not isinstance(facts, KnowledgeFactContext)
        or not isinstance(coverage, GuidanceCoverageInput)
        or not isinstance(event_read, EventFactRead)
        or activity not in (None, "admission", "outpatient", "surgery")
        or not isinstance(facts.facts, Mapping)
        or len(facts.facts) > 256
        or any(not isinstance(value, KnowledgeFact) for value in facts.facts.values())
    ):
        raise RelevanceUnavailable
    if (
        len(coverage.rules) > MAX_RELEVANCE_RULES
        or len(event_read.topics) > 256
        or len(event_read.scenarios) > 128
        or len(event_read.interpretation.facts) > 128
    ):
        raise RelevanceUnavailable("GUIDANCE_RELEVANCE_BUDGET_EXCEEDED")
    metadata = normalizer_code_scopes if normalizer_code_scopes is not None else {}
    if (
        not isinstance(metadata, Mapping)
        or len(metadata) > 512
        or any(
            type(key) is not str or not 1 <= len(key) <= 128 or not isinstance(value, CodeScope)
            for key, value in metadata.items()
        )
    ):
        raise RelevanceUnavailable
    for value in metadata.values():
        CodeScope(value.field_path, value.code_system, value.code_version)
    strict = _strict_facts(facts)
    result: list[RelevanceMatch] = []
    seen: set[tuple[object, ...]] = set()
    budget = _Budget()
    for rule in coverage.rules:
        try:
            matches = _find_rule_matches(
                rule, strict, coverage, event_read, metadata, activity, budget
            )
        except RuleValidationError, OperatorEvaluationError, KeyError, TypeError:
            continue
        except ValueError as error:
            if isinstance(error, RelevanceUnavailable):
                raise
            continue
        for match in matches:
            identity = (
                match.publication_id,
                match.rule_key,
                match.kind,
                match.field_path,
                match.spans,
                match.normalizer_key,
            )
            if identity not in seen:
                seen.add(identity)
                result.append(match)
                if len(result) > MAX_RELEVANCE_MATCHES:
                    raise RelevanceUnavailable("GUIDANCE_RELEVANCE_BUDGET_EXCEEDED")
    return tuple(result)
