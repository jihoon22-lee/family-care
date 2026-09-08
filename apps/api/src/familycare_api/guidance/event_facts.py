"""Adapt local observations without making keywords into clinical confirmation.

The original context preserves explicit user/system/AI provenance. Every consumer
of versioned codes must call scope_code_facts for its own required code identities.
Those identities are requirements, never evidence about the input's code version.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import date
from types import MappingProxyType

from familycare_api.decisions.domain import FactValue, MedicalEvent
from familycare_api.decisions.knowledge_domain import (
    KnowledgeFact,
    KnowledgeFactContext,
    KnowledgeFactNormalizer,
)
from familycare_api.decisions.knowledge_facts import (
    _STRUCTURED_FIELD_PATHS,
    _legacy_provenance,
    _structured_provenance,
    _token_matches,
    normalized_tokens,
)
from familycare_api.guidance.interpretation import (
    INTERPRETATION_REVISION,
    Activity,
    InterpretationError,
    InterpretationIssue,
    InterpretedFact,
    MatchScope,
    ScopeState,
    SituationInterpretation,
    SourceSpan,
    guard_normalizer_match,
    interpret_situation,
)

MAX_NORMALIZERS = 512
MAX_TOPICS = 256
_CODE_FIELDS = frozenset(
    {
        "MedicalEvent.classification",
        "MedicalEvent.diagnosis_code",
        "MedicalEvent.procedure_code",
        "MedicalEvent.anatomical_site_code",
        "MedicalEvent.pathology_code",
    }
)
_FIELDS = {
    **_STRUCTURED_FIELD_PATHS,
    "performed": "MedicalEvent.performed",
    "admission_days": "MedicalEvent.admission_days",
    "diagnosis_confirmed": "MedicalEvent.diagnosis_confirmed",
}
_EVENT_FIELDS = frozenset(_FIELDS.values())
_BOOLEANS = frozenset(
    {
        "MedicalEvent.admission",
        "MedicalEvent.outpatient",
        "MedicalEvent.performed",
        "MedicalEvent.pharmacy",
        "MedicalEvent.separately_billed_treatment",
        "MedicalEvent.diagnosis_confirmed",
    }
)
_DATES = frozenset({"MedicalEvent.event_date", "MedicalEvent.visit_date"})
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_INELIGIBLE_TOPIC_REASONS = frozenset(
    {
        "LOCAL_OTHER_SUBJECT",
        "LOCAL_SUBJECT_UNRESOLVED",
        "LOCAL_SUBJECT_REFERENCE",
        "LOCAL_OTHER_EVENT_TIME",
        "LOCAL_DOCUMENT_CONTEXT",
        "LOCAL_DATE_UNRESOLVED",
        "LOCAL_MATCH_SCOPE_UNRESOLVED",
    }
)
_ACTIVITY_SUFFIX = re.compile(
    r"(?:하지|할|하는|한|한지|했다|했습니다|했어요|했음|하였습니다|하였음|한다|합니다|중)"
)


class EventFactsError(ValueError):
    def __init__(self) -> None:
        super().__init__("GUIDANCE_EVENT_FACTS_INPUT_INVALID")


@dataclass(frozen=True, slots=True, repr=False)
class CodeScope:
    field_path: str
    code_system: str
    code_version: str

    def __post_init__(self) -> None:
        if (
            type(self.field_path) is not str
            or self.field_path not in _CODE_FIELDS
            or any(
                type(value) is not str or _IDENTIFIER.fullmatch(value) is None
                for value in (self.code_system, self.code_version)
            )
        ):
            raise EventFactsError


@dataclass(frozen=True, slots=True, repr=False)
class NormalizerTopic:
    normalizer_key: str
    field_path: str
    normalized_value: str | bool
    priority: int
    span: SourceSpan
    scope_state: ScopeState
    reason_codes: tuple[str, ...]
    relevance_allowed: bool


@dataclass(frozen=True, slots=True, repr=False)
class EventFactRead:
    context: KnowledgeFactContext
    interpretation: SituationInterpretation
    topics: tuple[NormalizerTopic, ...]
    scenarios: tuple[InterpretedFact, ...]
    code_scopes: tuple[CodeScope, ...]
    reason_codes: tuple[str, ...]
    local_fact_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True, repr=False)
class ScopedEventFacts:
    context: KnowledgeFactContext
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True, repr=False)
class _Explicit:
    fact: KnowledgeFact
    structured: bool
    source: str
    code_scope: CodeScope | None = None


@dataclass(frozen=True, slots=True, repr=False)
class _Token:
    value: str
    span: SourceSpan


def _scope_tuple(values: tuple[CodeScope, ...]) -> None:
    if not isinstance(values, tuple) or len(values) > 32:
        raise EventFactsError
    for value in values:
        if not isinstance(value, CodeScope):
            raise EventFactsError
        CodeScope(value.field_path, value.code_system, value.code_version)


def _valid_value(path: str, value: object) -> bool:
    if value is None:
        return True
    if path in _BOOLEANS:
        return type(value) is bool
    if path == "MedicalEvent.admission_days":
        return type(value) is int and 0 <= value <= 36500
    if path in _DATES:
        if type(value) is date:
            return True
        if type(value) is str:
            try:
                date.fromisoformat(value)
                return True
            except ValueError:
                return False
        return False
    return type(value) is str and 1 <= len(value) <= 160 and bool(value.strip())


def _same(left: object, right: object) -> bool:
    return type(left) is type(right) and left == right


def _read_explicit(
    event: MedicalEvent,
) -> tuple[dict[str, KnowledgeFact], tuple[CodeScope, ...], set[str], set[str]]:
    candidates: dict[str, list[_Explicit]] = {}
    reasons: set[str] = set()
    conflicts: set[str] = set()
    if (
        not isinstance(event.facts, Mapping)
        or len(event.facts) > 128
        or len(event.structured_facts) > 128
    ):
        raise EventFactsError
    for legacy_path, value in event.facts.items():
        if legacy_path not in _EVENT_FIELDS:
            reasons.add("LOCAL_EXPLICIT_FIELD_UNSUPPORTED")
            continue
        if not isinstance(value, FactValue):
            raise EventFactsError
        fact = KnowledgeFact(
            value.value,
            _legacy_provenance(value),
            evidence_keys=tuple(str(key) for key in value.evidence_ids),
            stale=value.evidence_stale,
        )
        candidates.setdefault(legacy_path, []).append(_Explicit(fact, False, "legacy"))
    for raw in event.structured_facts:
        if not isinstance(raw, Mapping):
            raise EventFactsError
        field_id = raw.get("field_id")
        path = _FIELDS.get(field_id) if isinstance(field_id, str) else None
        if path is None:
            reasons.add("LOCAL_EXPLICIT_FIELD_UNSUPPORTED")
            continue
        source = raw.get("source")
        if type(source) is not str or source not in {"user", "system", "ai"}:
            reasons.add("LOCAL_EXPLICIT_FACT_INVALID")
            fact = KnowledgeFact(None, "UNCONFIRMED")
            candidate = _Explicit(fact, True, "unknown")
        else:
            evidence = raw.get("evidence_ids", ())
            fact = KnowledgeFact(
                raw.get("value"),
                _structured_provenance(raw),
                evidence_keys=tuple(str(value) for value in evidence)
                if isinstance(evidence, list | tuple)
                else (),
            )
            scope = None
            if path in _CODE_FIELDS and isinstance(fact.value, str):
                system, version = raw.get("code_system"), raw.get("code_version")
                if isinstance(system, str) and isinstance(version, str):
                    with suppress(EventFactsError):
                        scope = CodeScope(path, system, version)
            candidate = _Explicit(fact, True, str(source), scope)
        candidates.setdefault(path, []).append(candidate)
    for date_path, date_value in (
        ("MedicalEvent.event_date", event.event_date),
        ("MedicalEvent.visit_date", event.visit_date),
    ):
        if date_value is not None and date_path not in candidates:
            candidates[date_path] = [
                _Explicit(KnowledgeFact(date_value, "USER_CONFIRMED"), False, "event")
            ]
    result = {}
    scopes = []
    for path, entries in candidates.items():
        # Current explicit structured user input outranks legacy fields. AI cannot
        # erase a prior explicit user value; ambiguous user input still blocks derivation.
        structured_user = [item for item in entries if item.structured and item.source == "user"]
        trusted_user = [item for item in entries if item.fact.provenance == "USER_CONFIRMED"]
        structured_system = [
            item for item in entries if item.structured and item.source == "system"
        ]
        selected = (
            structured_user
            or trusted_user
            or structured_system
            or [item for item in entries if item.structured]
            or entries
        )
        chosen = selected[-1].fact
        if any(not _same(item.fact.value, chosen.value) for item in entries):
            conflicts.add(path)
        if any(
            not _same(item.fact.value, chosen.value)
            or (item.fact.provenance, item.fact.stale) != (chosen.provenance, chosen.stale)
            for item in selected
        ):
            conflicts.add(path)
            chosen = KnowledgeFact(
                None,
                "CONFLICTING",
                evidence_keys=tuple(
                    dict.fromkeys(key for item in selected for key in item.fact.evidence_keys)
                ),
            )
        if not _valid_value(path, chosen.value):
            chosen = KnowledgeFact(None, "UNCONFIRMED")
            reasons.add("LOCAL_EXPLICIT_FACT_INVALID")
        result[path] = chosen
        identities = {item.code_scope for item in selected}
        if path in _CODE_FIELDS and chosen.value is not None:
            if len(identities) == 1 and None not in identities:
                scopes.append(next(item for item in identities if item is not None))
            elif len(identities) > 1:
                reasons.add("EVENT_CODE_SCOPE_UNRESOLVED")
                conflicts.add(path)
    return result, tuple(sorted(scopes, key=lambda item: item.field_path)), conflicts, reasons


def _tokens(text: str) -> tuple[_Token, ...]:
    spans = []
    start = None
    for index, character in enumerate(text):
        part = (
            character.isalnum()
            or character == "_"
            or (start is not None and unicodedata.category(character).startswith("M"))
        )
        if part and start is None:
            start = index
        elif not part and start is not None:
            spans.append(SourceSpan(start, index))
            start = None
    if start is not None:
        spans.append(SourceSpan(start, len(text)))
    return tuple(
        _Token(value, span)
        for span in spans
        for value in normalized_tokens(text[span.start : span.end])
    )


def _matches(value: str, expected: str) -> bool:
    return _token_matches(value, expected) or (
        expected in {"수술", "입원", "외래", "통원"}
        and value.startswith(expected)
        and _ACTIVITY_SUFFIX.fullmatch(value[len(expected) :]) is not None
    )


def _topics(
    event: MedicalEvent,
    normalizers: tuple[KnowledgeFactNormalizer, ...],
    selected_subject_terms: tuple[str, ...],
    other_subject_terms: tuple[str, ...],
) -> tuple[tuple[NormalizerTopic, ...], set[str]]:
    if not isinstance(normalizers, tuple):
        raise EventFactsError
    if len(normalizers) > MAX_NORMALIZERS:
        return (), {"LOCAL_NORMALIZER_LIMIT_EXCEEDED"}
    tokens = _tokens(event.situation)
    topics: list[NormalizerTopic] = []
    reasons: set[str] = set()
    cache: dict[SourceSpan, MatchScope] = {}
    for normalizer in normalizers:
        if not isinstance(normalizer, KnowledgeFactNormalizer):
            raise EventFactsError
        if normalizer.field_path not in _EVENT_FIELDS:
            reasons.add("LOCAL_NORMALIZER_FIELD_UNSUPPORTED")
            continue
        if (
            type(normalizer.normalizer_key) is not str
            or not 1 <= len(normalizer.normalizer_key) <= 128
            or type(normalizer.priority) is not int
            or not 0 <= normalizer.priority <= 1000
            or not isinstance(normalizer.normalized_tokens, tuple)
            or not 1 <= len(normalizer.normalized_tokens) <= 32
            or any(
                type(token) is not str or not 1 <= len(token) <= 160
                for token in normalizer.normalized_tokens
            )
            or type(normalizer.normalized_value) not in (str, bool)
            or isinstance(normalizer.normalized_value, str)
            and not 1 <= len(normalizer.normalized_value) <= 160
        ):
            reasons.add("LOCAL_NORMALIZER_INVALID")
            continue
        expected = tuple(
            value for token in normalizer.normalized_tokens for value in normalized_tokens(token)
        )
        if not 1 <= len(expected) <= 32:
            reasons.add("LOCAL_NORMALIZER_INVALID")
            continue
        for start in range(len(tokens) - len(expected) + 1):
            window = tokens[start : start + len(expected)]
            if not all(
                _matches(token.value, value) for token, value in zip(window, expected, strict=True)
            ):
                continue
            if len(topics) >= MAX_TOPICS:
                return (), reasons | {"LOCAL_NORMALIZER_LIMIT_EXCEEDED"}
            span = SourceSpan(window[0].span.start, window[-1].span.end)
            if span not in cache:
                try:
                    cache[span] = guard_normalizer_match(
                        event.situation,
                        span.start,
                        span.end,
                        selected_subject_terms=selected_subject_terms,
                        other_subject_terms=other_subject_terms,
                        event_date=event.event_date,
                    )
                except InterpretationError:
                    cache[span] = MatchScope("UNKNOWN", ("LOCAL_MATCH_SCOPE_UNRESOLVED",), span)
            scope = cache[span]
            topics.append(
                NormalizerTopic(
                    normalizer.normalizer_key,
                    normalizer.field_path,
                    normalizer.normalized_value,
                    normalizer.priority,
                    span,
                    scope.state,
                    scope.reason_codes,
                    scope.state != "NEGATED"
                    and not bool(_INELIGIBLE_TOPIC_REASONS & set(scope.reason_codes)),
                )
            )
    priorities: dict[str, int] = {}
    for item in topics:
        if item.relevance_allowed:
            priorities[item.field_path] = max(priorities.get(item.field_path, -1), item.priority)
    return tuple(
        replace(
            item,
            relevance_allowed=False,
            reason_codes=(
                *item.reason_codes,
                "LOCAL_NORMALIZER_LOWER_PRIORITY",
            ),
        )
        if item.relevance_allowed and item.priority < priorities[item.field_path]
        else item
        for item in topics
    ), reasons


def build_event_facts(
    event: MedicalEvent,
    normalizers: tuple[KnowledgeFactNormalizer, ...] = (),
    *,
    selected_subject_terms: tuple[str, ...] = (),
    other_subject_terms: tuple[str, ...] = (),
    activity: Activity | None = None,
    code_scopes: tuple[CodeScope, ...] = (),
) -> EventFactRead:
    """Preserve explicit inputs, local source spans, scenarios and conditional topics.

    code_scopes can select identities present on the winning structured input's
    code_system/code_version fields. It cannot supply absent metadata. The original
    code value remains literal user input until scope_code_facts validates a rule.
    """
    if not isinstance(event, MedicalEvent) or activity not in (
        None,
        "admission",
        "outpatient",
        "surgery",
    ):
        raise EventFactsError
    _scope_tuple(code_scopes)
    explicit, actual_scopes, conflicts, reasons = _read_explicit(event)
    if event.situation.strip():
        try:
            interpretation = interpret_situation(
                event.situation,
                selected_subject_terms=selected_subject_terms,
                other_subject_terms=other_subject_terms,
                event_date=event.event_date,
            )
        except InterpretationError:
            interpretation = SituationInterpretation(
                (),
                (
                    InterpretationIssue(
                        SourceSpan(0, len(event.situation)),
                        ("LOCAL_INTERPRETATION_UNAVAILABLE",),
                    ),
                ),
            )
            reasons.add("LOCAL_INTERPRETATION_UNAVAILABLE")
    else:
        interpretation = SituationInterpretation((), ())
    topics, topic_reasons = _topics(event, normalizers, selected_subject_terms, other_subject_terms)
    reasons.update(topic_reasons)
    derived: dict[str, KnowledgeFact] = {}
    local_paths: set[str] = set()
    for item in interpretation.facts:
        confirmed = item.state == "CONFIRMED"
        if item.field_path == "MedicalEvent.performed" and activity != item.activity:
            confirmed = False
            reasons.add("LOCAL_ACTIVITY_BINDING_REQUIRED")
        derived[item.field_path] = KnowledgeFact(
            item.value if confirmed else None,
            "DERIVED_CONFIRMED" if confirmed else "UNCONFIRMED",
            evidence_keys=tuple(
                f"{INTERPRETATION_REVISION}:{span.start}:{span.end}" for span in item.spans
            ),
        )
        if "LOCAL_FACT_CONFLICT" in item.reason_codes:
            conflicts.add(item.field_path)
            derived[item.field_path] = replace(derived[item.field_path], provenance="CONFLICTING")
        elif confirmed:
            local_paths.add(item.field_path)
    topic_fields: dict[str, list[NormalizerTopic]] = {}
    for topic in topics:
        if topic.relevance_allowed:
            topic_fields.setdefault(topic.field_path, []).append(topic)
    for path, values in topic_fields.items():
        keys = tuple(sorted({item.normalizer_key for item in values}))
        ambiguous = any(
            not _same(item.normalized_value, values[0].normalized_value) for item in values
        )
        if ambiguous:
            conflicts.add(path)
        if path not in derived:
            derived[path] = KnowledgeFact(
                None, "CONFLICTING" if ambiguous else "UNCONFIRMED", normalizer_keys=keys
            )
    for path, fact in explicit.items():
        inferred = derived.get(path)
        if inferred is not None and (
            inferred.provenance == "CONFLICTING"
            or inferred.value is not None
            and not _same(fact.value, inferred.value)
        ):
            conflicts.add(path)
        if any(not _same(fact.value, item.normalized_value) for item in topic_fields.get(path, ())):
            conflicts.add(path)
        derived[path] = fact
        local_paths.discard(path)
    admission = explicit.get("MedicalEvent.admission")
    days_path = "MedicalEvent.admission_days"
    if (
        days_path in local_paths
        and admission is not None
        and (admission.value is not True or not admission.is_trusted)
    ):
        derived[days_path] = replace(derived[days_path], value=None, provenance="CONFLICTING")
        local_paths.remove(days_path)
        conflicts.add(days_path)
        reasons.add("LOCAL_DEPENDENT_FACT_CONFLICT")
    if code_scopes:
        declared = set(actual_scopes)
        for supplied in code_scopes:
            if supplied not in declared:
                reasons.add("EVENT_CODE_SCOPE_UNVERIFIED")
        actual_scopes = tuple(item for item in actual_scopes if item in code_scopes)
    return EventFactRead(
        KnowledgeFactContext(MappingProxyType(dict(derived)), tuple(sorted(conflicts))),
        interpretation,
        topics,
        tuple(item for item in interpretation.facts if item.state == "SCENARIO"),
        actual_scopes,
        tuple(sorted(reasons)),
        tuple(sorted(local_paths)),
    )


def scope_code_facts(
    read: EventFactRead,
    required_scopes: tuple[CodeScope, ...],
) -> ScopedEventFacts:
    """Mask unqualified code inputs in a fresh per-rule context; never rewrite the original."""
    if not isinstance(read, EventFactRead):
        raise EventFactsError
    _scope_tuple(required_scopes)
    facts = dict(read.context.facts)
    reasons = set()
    for path in _CODE_FIELDS & facts.keys():
        fact = facts[path]
        if fact.value is None:
            continue
        required = {item for item in required_scopes if item.field_path == path}
        actual = {item for item in read.code_scopes if item.field_path == path}
        if len(required) == len(actual) == 1 and required == actual:
            continue
        reasons.add(
            "EVENT_CODE_SCOPE_MISMATCH"
            if len(required) == len(actual) == 1
            else "EVENT_CODE_SCOPE_UNRESOLVED"
        )
        facts[path] = replace(fact, value=None, provenance="UNCONFIRMED")
    return ScopedEventFacts(
        KnowledgeFactContext(MappingProxyType(facts), read.context.audit_conflicts),
        tuple(sorted(reasons)),
    )
