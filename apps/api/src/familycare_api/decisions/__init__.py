"""Deterministic, evidence-aware coverage decision primitives."""

from familycare_api.decisions.domain import (
    ClaimCandidate,
    ClaimHistoryFact,
    ClaimHistoryReader,
    CoverageDecisionEngine,
    DecisionReaders,
    DecisionRun,
    DecisionRunResult,
    EvidenceRepository,
    FactContext,
    FactValue,
    MedicalEvent,
    OperatorOutcome,
    PolicySnapshot,
    PolicySnapshotReader,
    Question,
    RuleEvaluation,
    RuleReader,
    TriState,
)
from familycare_api.decisions.facts import (
    FactNormalizationError,
    normalize_fact,
    normalize_fact_mapping,
    normalize_facts,
)
from familycare_api.decisions.operators import (
    OperatorEvaluationError,
    compare_required,
    evaluate_expression,
)
from familycare_api.decisions.rule_runtime import (
    RuleRuntimeError,
    compile_rule_expression,
    evaluate_rule,
)

__all__ = [
    "ClaimCandidate",
    "ClaimHistoryFact",
    "ClaimHistoryReader",
    "CoverageDecisionEngine",
    "DecisionReaders",
    "DecisionRun",
    "DecisionRunResult",
    "EvidenceRepository",
    "FactContext",
    "FactNormalizationError",
    "FactValue",
    "MedicalEvent",
    "OperatorEvaluationError",
    "OperatorOutcome",
    "PolicySnapshot",
    "PolicySnapshotReader",
    "Question",
    "RuleEvaluation",
    "RuleReader",
    "RuleRuntimeError",
    "TriState",
    "compare_required",
    "compile_rule_expression",
    "evaluate_expression",
    "evaluate_rule",
    "normalize_fact",
    "normalize_fact_mapping",
    "normalize_facts",
]
