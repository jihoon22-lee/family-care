"""Exact-token normalization for private knowledge decision inputs."""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from collections.abc import Mapping

from familycare_api.decisions.domain import FactValue, MedicalEvent
from familycare_api.decisions.knowledge_domain import (
    KnowledgeFact,
    KnowledgeFactContext,
    KnowledgeFactNormalizer,
    KnowledgeFactProvenance,
)

_STRUCTURED_FIELD_PATHS = {
    "event_date": "MedicalEvent.event_date",
    "visit_date": "MedicalEvent.visit_date",
    "condition_class": "MedicalEvent.classification",
    "diagnosis_label": "MedicalEvent.diagnosis_label",
    "treatment_kind": "MedicalEvent.treatment_kind",
    "admission": "MedicalEvent.admission",
    "outpatient": "MedicalEvent.outpatient",
    "pharmacy": "MedicalEvent.pharmacy",
    "diagnosis_code": "MedicalEvent.diagnosis_code",
    "procedure_code": "MedicalEvent.procedure_code",
    "anatomical_site_code": "MedicalEvent.anatomical_site_code",
    "pathology_code": "MedicalEvent.pathology_code",
    "treatment_setting": "MedicalEvent.treatment_setting",
    "treatment_context": "MedicalEvent.treatment_context",
    "separately_billed_treatment": "MedicalEvent.separately_billed_treatment",
}


def normalized_tokens(value: str) -> tuple[str, ...]:
    """Tokenize NFKC/casefold text without substring or fuzzy matching."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    tokens: list[str] = []
    current: list[str] = []
    for character in normalized:
        if character.isalnum() or character == "_":
            current.append(character)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def _contains_sequence(values: tuple[str, ...], expected: tuple[str, ...]) -> bool:
    if not expected or len(expected) > len(values):
        return False
    width = len(expected)
    return any(
        values[index : index + width] == expected for index in range(len(values) - width + 1)
    )


def _legacy_provenance(value: FactValue) -> KnowledgeFactProvenance:
    if value.confirmation == "user":
        return "USER_CONFIRMED"
    if value.confirmation == "ai_structured":
        return "AI_SUGGESTED"
    if value.confirmation == "conflicting":
        return "CONFLICTING"
    return "UNCONFIRMED"


def _structured_provenance(value: Mapping[str, object]) -> KnowledgeFactProvenance:
    source = value.get("source")
    state = value.get("state")
    if state == "conflict":
        return "CONFLICTING"
    if state != "confirmed":
        return "UNCONFIRMED"
    if source == "user":
        return "USER_CONFIRMED"
    if source == "system":
        return "DERIVED_CONFIRMED"
    return "AI_SUGGESTED"


def _explicit_facts(event: MedicalEvent) -> dict[str, KnowledgeFact]:
    result = {
        field: KnowledgeFact(
            value=value.value,
            provenance=_legacy_provenance(value),
            evidence_keys=tuple(str(item) for item in value.evidence_ids),
            stale=value.evidence_stale,
        )
        for field, value in event.facts.items()
    }
    for raw in event.structured_facts:
        field_id = raw.get("field_id")
        if not isinstance(field_id, str):
            continue
        field_path = _STRUCTURED_FIELD_PATHS.get(field_id)
        if field_path is None:
            continue
        evidence = raw.get("evidence_ids", ())
        evidence_keys = (
            tuple(str(item) for item in evidence) if isinstance(evidence, list | tuple) else ()
        )
        result[field_path] = KnowledgeFact(
            value=raw.get("value"),
            provenance=_structured_provenance(raw),
            evidence_keys=evidence_keys,
        )
    if event.event_date is not None:
        result.setdefault(
            "MedicalEvent.event_date",
            KnowledgeFact(event.event_date, "USER_CONFIRMED"),
        )
    if event.visit_date is not None:
        result.setdefault(
            "MedicalEvent.visit_date",
            KnowledgeFact(event.visit_date, "USER_CONFIRMED"),
        )
    return result


def normalize_private_event_facts(
    event: MedicalEvent,
    normalizers: tuple[KnowledgeFactNormalizer, ...],
) -> KnowledgeFactContext:
    """Derive reviewed codes, then overlay explicit facts without upgrading trust."""

    situation_tokens = normalized_tokens(event.situation)
    matches: dict[str, list[KnowledgeFactNormalizer]] = defaultdict(list)
    for normalizer in normalizers:
        expected = tuple(
            token for raw in normalizer.normalized_tokens for token in normalized_tokens(raw)
        )
        if _contains_sequence(situation_tokens, expected):
            matches[normalizer.field_path].append(normalizer)

    derived: dict[str, KnowledgeFact] = {}
    conflicts: set[str] = set()
    for field_path, field_matches in matches.items():
        top_priority = max(item.priority for item in field_matches)
        selected = tuple(
            sorted(
                (item for item in field_matches if item.priority == top_priority),
                key=lambda item: item.normalizer_key,
            )
        )
        values = {repr(item.normalized_value): item.normalized_value for item in selected}
        if len(values) != 1:
            conflicts.add(field_path)
            derived[field_path] = KnowledgeFact(
                value=None,
                provenance="CONFLICTING",
                normalizer_keys=tuple(item.normalizer_key for item in selected),
            )
            continue
        derived[field_path] = KnowledgeFact(
            value=next(iter(values.values())),
            provenance="DERIVED_CONFIRMED",
            normalizer_keys=tuple(item.normalizer_key for item in selected),
        )

    explicit = _explicit_facts(event)
    for field_path, fact in explicit.items():
        existing = derived.get(field_path)
        if existing is not None and (
            existing.provenance == "CONFLICTING"
            or (fact.provenance == "USER_CONFIRMED" and existing.value != fact.value)
        ):
            conflicts.add(field_path)
        derived[field_path] = fact
    return KnowledgeFactContext(
        facts=derived,
        audit_conflicts=tuple(sorted(conflicts)),
    )


__all__ = ["normalize_private_event_facts", "normalized_tokens"]
