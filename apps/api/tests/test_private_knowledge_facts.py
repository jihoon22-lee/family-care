"""Exact, trust-preserving normalization for private event facts."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from familycare_api.decisions.domain import FactValue, MedicalEvent
from familycare_api.decisions.knowledge_domain import KnowledgeFactNormalizer
from familycare_api.decisions.knowledge_facts import normalize_private_event_facts


def _event(
    situation: str,
    *,
    facts: dict[str, FactValue] | None = None,
) -> MedicalEvent:
    return MedicalEvent(
        id=UUID("00000000-0000-4000-8000-000000006001"),
        household_space_id=UUID("00000000-0000-4000-8000-000000006002"),
        family_member_id=UUID("00000000-0000-4000-8000-000000006003"),
        mode="post_treatment",
        situation=situation,
        event_date=date(2026, 6, 1),
        visit_date=date(2026, 6, 1),
        facts=facts or {},
    )


def _normalizer(
    key: str,
    tokens: tuple[str, ...],
    value: str,
    *,
    priority: int = 100,
) -> KnowledgeFactNormalizer:
    return KnowledgeFactNormalizer(
        normalizer_key=key,
        field_path="MedicalEvent.classification",
        normalized_tokens=tokens,
        normalized_value=value,
        priority=priority,
    )


def test_unicode_exact_token_sequence_and_priority_are_deterministic() -> None:
    event = _event("VIOLET\u3000DELTA was recorded")
    context = normalize_private_event_facts(
        event,
        (
            _normalizer("lower", ("violet", "delta"), "lower_value", priority=10),
            _normalizer("higher", ("violet", "delta"), "sample_code", priority=20),
        ),
    )

    fact = context.get("MedicalEvent.classification")
    assert fact is not None
    assert fact.value == "sample_code"
    assert fact.provenance == "DERIVED_CONFIRMED"
    assert fact.normalizer_keys == ("higher",)

    no_substring = normalize_private_event_facts(
        _event("violet deltaforce was recorded"),
        (_normalizer("exact", ("violet", "delta"), "sample_code"),),
    )
    assert no_substring.get("MedicalEvent.classification") is None


def test_korean_case_marker_does_not_break_an_exact_reviewed_token() -> None:
    context = normalize_private_event_facts(
        _event("샘플처치를 받음"),
        (_normalizer("reviewed", ("샘플처치",), "sample_code"),),
    )

    fact = context.get("MedicalEvent.classification")
    assert fact is not None
    assert fact.value == "sample_code"
    assert fact.provenance == "DERIVED_CONFIRMED"

    unrelated_prefix = normalize_private_event_facts(
        _event("샘플처치실을 방문함"),
        (_normalizer("reviewed", ("샘플처치",), "sample_code"),),
    )
    assert unrelated_prefix.get("MedicalEvent.classification") is None


def test_equal_priority_conflict_is_preserved_and_user_fact_overrides_derivation() -> None:
    normalizers = (
        _normalizer("one", ("violet", "delta"), "sample_one"),
        _normalizer("two", ("violet", "delta"), "sample_two"),
    )
    conflicting = normalize_private_event_facts(
        _event("violet delta"),
        normalizers,
    )
    fact = conflicting.get("MedicalEvent.classification")
    assert fact is not None
    assert fact.value is None
    assert fact.provenance == "CONFLICTING"
    assert conflicting.audit_conflicts == ("MedicalEvent.classification",)

    overridden = normalize_private_event_facts(
        _event(
            "violet delta",
            facts={
                "MedicalEvent.classification": FactValue(
                    value="user_code",
                    confirmation="user",
                    evidence_ids=(),
                )
            },
        ),
        normalizers,
    )
    user_fact = overridden.get("MedicalEvent.classification")
    assert user_fact is not None
    assert user_fact.value == "user_code"
    assert user_fact.provenance == "USER_CONFIRMED"
    assert overridden.audit_conflicts == ("MedicalEvent.classification",)

    reviewed_override = normalize_private_event_facts(
        _event(
            "violet delta",
            facts={
                "MedicalEvent.classification": FactValue(
                    value="user_code",
                    confirmation="user",
                    evidence_ids=(),
                )
            },
        ),
        (_normalizer("one", ("violet", "delta"), "sample_one"),),
    )
    assert reviewed_override.audit_conflicts == ("MedicalEvent.classification",)


def test_identical_normalizer_values_are_deduplicated_without_conflict() -> None:
    context = normalize_private_event_facts(
        _event("violet delta"),
        (
            _normalizer("one", ("violet", "delta"), "sample_one"),
            _normalizer("two", ("violet", "delta"), "sample_one"),
        ),
    )

    fact = context.get("MedicalEvent.classification")
    assert fact is not None
    assert fact.value == "sample_one"
    assert fact.normalizer_keys == ("one", "two")
    assert context.audit_conflicts == ()


def test_ai_structured_value_stays_suggested_until_user_confirmation() -> None:
    context = normalize_private_event_facts(
        _event(
            "sample situation",
            facts={
                "MedicalEvent.classification": FactValue(
                    value="suggested_code",
                    confirmation="ai_structured",
                    evidence_ids=(),
                )
            },
        ),
        (),
    )

    fact = context.get("MedicalEvent.classification")
    assert fact is not None
    assert fact.value == "suggested_code"
    assert fact.provenance == "AI_SUGGESTED"
    assert fact.is_trusted is False
