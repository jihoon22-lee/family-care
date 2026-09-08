"""Source-index legacy adaptation retains historical meaning without new authority."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from uuid import UUID

import pytest
from familycare_api.private_knowledge.models import (
    ClauseRecord,
    SemanticReviewRecord,
    TermsSectionRecord,
)
from familycare_api.terms_knowledge.legacy_adapter import (
    LegacyAdapterError,
    LegacyClauseRow,
    LegacyFactIdentity,
    LegacyReviewContext,
    adapt_legacy_review,
)

from apps.api.tests.private_knowledge_fixtures import synthetic_records


def fixture():
    records = synthetic_records()
    context = LegacyReviewContext(
        household_space_id=UUID(int=101),
        import_run_id=UUID(int=102),
        section_id=UUID(int=103),
        review_id=UUID(int=104),
    )
    section = TermsSectionRecord.model_validate(records["terms-sections.jsonl"][0])
    raw = deepcopy(records["terms-semantic-review.jsonl"][0])
    raw["facts"][0]["numeric_terms_ko"] = ["Synthetic insured amount basis: 100 units."]
    raw["facts"][0]["condition_details_ko"].append("Synthetic additional exception.")
    raw["warnings"] = ["Synthetic retained warning."]
    raw["missing_categories"] = ["waiting_period"]
    raw["previous_fact_audit"] = [
        {
            "lexical_support_ratio": 0.5,
            "previous_category": "definition",
            "previous_fact_id": "synthetic-old-fact",
            "reason_codes": ["SYNTHETIC_REFERENCE"],
            "review_decision": "NEEDS_REVIEW",
        }
    ]
    review = SemanticReviewRecord.model_validate(raw)
    clause = LegacyClauseRow(
        household_space_id=context.household_space_id,
        import_run_id=context.import_run_id,
        section_id=context.section_id,
        clause_id=UUID(int=105),
        record=ClauseRecord.model_validate(records["clause-evidence-index.jsonl"][0]),
    )
    fact = LegacyFactIdentity(
        household_space_id=context.household_space_id,
        import_run_id=context.import_run_id,
        section_id=context.section_id,
        fact_id=UUID(int=106),
        source_fact_id=review.facts[0].fact_id,
    )
    return context, section, review, clause, fact


def test_adaptation_preserves_models_ids_counts_and_every_legacy_field() -> None:
    context, section, review, clause, fact = fixture()
    result = adapt_legacy_review(
        context, section=section, review=review, clauses=(clause,), fact_ids=(fact,)
    )
    assert result.context == context
    assert result.restore_section().model_dump() == section.model_dump()
    assert result.restore_review().model_dump() == review.model_dump()
    assert result.clauses[0].restore_record().model_dump() == clause.record.model_dump()
    assert result.facts[0].restore_record().model_dump() == review.facts[0].model_dump()
    assert result.facts[0].fact_id == fact.fact_id
    assert result.facts[0].source_fact_id == fact.source_fact_id
    assert result.clauses[0].clause_id == clause.clause_id
    assert result.counts.fact_count == 1 and result.counts.clause_count == 1
    assert result.counts.citation_count == 2
    assert result.counts.unresolved_citation_count == 0
    assert result.facts[0].citations[0].index_status == "INDEX_MATCHED"
    assert result.status == "REPROCESSING_REQUIRED" and not result.executable_rule
    assert result.restore_review().section_review_state == "SOL_DIRECT_GROUNDED"
    assert "USER_CONFIRMED" not in result.review_record_json
    assert not hasattr(result, "compiled_rules")
    assert not hasattr(result.facts[0].citations[0], "start")


def test_missing_index_and_fact_identity_are_preserved_for_reprocessing() -> None:
    context, section, review, _, _ = fixture()
    result = adapt_legacy_review(context, section=section, review=review)
    assert result.facts[0].fact_id is None
    assert result.facts[0].source_fact_id == review.facts[0].fact_id
    assert result.counts.fact_count == 1 and result.counts.unresolved_citation_count == 2
    assert result.restore_review().model_dump() == review.model_dump()
    assert "LEGACY_FACT_ID_UNAVAILABLE" in result.facts[0].reason_codes
    assert result.facts[0].citations[0].index_status == "CLAUSE_MISSING"


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_text_sha256", "b" * 64),
        ("physical_page_start", 3),
        ("physical_page_end", 1),
        ("clause_index", 99),
    ],
)
def test_mismatched_or_unknown_citations_are_not_original_evidence(field, value) -> None:
    context, section, review, clause, fact = fixture()
    raw = review.model_dump()
    raw["facts"][0]["citations"][0][field] = value
    changed = SemanticReviewRecord.model_validate(raw)
    result = adapt_legacy_review(
        context, section=section, review=changed, clauses=(clause,), fact_ids=(fact,)
    )
    assert result.facts[0].citations[0].index_status != "INDEX_MATCHED"
    assert result.restore_review().model_dump() == changed.model_dump()
    assert not result.executable_rule


@pytest.mark.parametrize("kind", ["clause", "fact"])
@pytest.mark.parametrize("field", ["household_space_id", "import_run_id", "section_id"])
def test_other_scope_rows_are_rejected_with_no_values(kind, field) -> None:
    context, section, review, clause, fact = fixture()
    if kind == "clause":
        clause = replace(clause, **{field: UUID(int=999)})
    else:
        fact = replace(fact, **{field: UUID(int=999)})
    with pytest.raises(LegacyAdapterError, match="^LEGACY_SCOPE_MISMATCH$") as error:
        adapt_legacy_review(
            context, section=section, review=review, clauses=(clause,), fact_ids=(fact,)
        )
    assert "999" not in str(error.value) and review.facts[0].statement_ko not in str(error.value)


def test_missing_references_and_historical_review_state_remain_unresolved() -> None:
    context, section, review, clause, fact = fixture()
    raw = review.model_dump()
    raw["facts"][0].update(unresolved_reference=True, review_state="NEEDS_REFERENCE_REVIEW")
    changed = SemanticReviewRecord.model_validate(raw)
    result = adapt_legacy_review(
        context, section=section, review=changed, clauses=(clause,), fact_ids=(fact,)
    )
    assert "LEGACY_REFERENCE_UNRESOLVED" in result.facts[0].reason_codes
    assert result.facts[0].restore_record().review_state == "NEEDS_REFERENCE_REVIEW"
    assert result.restore_review().analysis_status == "complete"  # Historical value only.
    assert result.status == "REPROCESSING_REQUIRED"


def test_result_is_immutable_and_has_no_sensitive_default_repr_or_aliasing() -> None:
    context, section, review, clause, fact = fixture()
    result = adapt_legacy_review(
        context, section=section, review=review, clauses=(clause,), fact_ids=(fact,)
    )
    text = review.facts[0].numeric_terms_ko[0]
    assert text not in repr(result) and text not in repr(result.facts[0])
    assert review.facts[0].statement_ko not in repr(clause)
    with pytest.raises(FrozenInstanceError):
        result.status = "CONFIRMED"
    restored = result.restore_review()
    restored.facts[0].numeric_terms_ko.append("Synthetic later mutation.")
    review.facts[0].condition_details_ko.append("Synthetic source mutation.")
    assert result.restore_review().facts[0].numeric_terms_ko == [text]
    assert "Synthetic source mutation." not in result.restore_review().facts[0].condition_details_ko


def test_all_legacy_categories_and_context_round_trip_without_flattening() -> None:
    context, section, review, clause, fact = fixture()
    categories = [
        "amount_basis",
        "claim_documents",
        "cross_reference",
        "definition",
        "exclusion",
        "frequency_limit",
        "payment_reason",
        "reduction_period",
        "renewal",
        "termination",
        "waiting_period",
    ]
    raw = review.model_dump()
    raw["facts"] = []
    identities = []
    for index, category in enumerate(categories):
        value = review.facts[0].model_dump()
        value.update(
            category=category,
            fact_id=f"synthetic-fact-{index}",
            numeric_terms_ko=[
                "Synthetic unit basis: 100 per eligible day.",
                "Synthetic exclusion: first 2 days.",
            ],
            unresolved_reference=category == "cross_reference",
        )
        raw["facts"].append(value)
        identities.append(
            replace(fact, fact_id=UUID(int=200 + index), source_fact_id=value["fact_id"])
        )
    raw["found_categories"] = categories
    changed = SemanticReviewRecord.model_validate(raw)
    section = TermsSectionRecord.model_validate(
        {**section.model_dump(), "semantic_fact_count": len(categories)}
    )
    result = adapt_legacy_review(
        context, section=section, review=changed, clauses=(clause,), fact_ids=tuple(identities)
    )
    assert result.counts.fact_count == len(categories)
    assert result.counts.citation_count == len(categories) + len(changed.summary_citations)
    assert result.restore_review().model_dump() == changed.model_dump()
    assert [item.restore_record().category for item in result.facts] == categories
    assert [item.fact_id for item in result.facts] == [item.fact_id for item in identities]
    assert not result.executable_rule


@pytest.mark.parametrize(
    "fault",
    [
        "version",
        "zero_identity",
        "section",
        "clause_alias",
        "unknown_fact",
        "duplicate_fact",
        "duplicate_clause",
    ],
)
def test_invalid_schema_scope_and_row_identity_never_silently_drop_data(fault: str) -> None:
    context, section, review, clause, fact = fixture()
    clauses, identities = (clause,), (fact,)
    if fault == "version":
        context = replace(context, package_schema_version="unsupported-version")
    elif fault == "zero_identity":
        context = replace(context, import_run_id=UUID(int=0))
    elif fault == "section":
        review = SemanticReviewRecord.model_validate(
            {**review.model_dump(), "section_id": "synthetic-other-section"}
        )
    elif fault == "clause_alias":
        clauses = (
            replace(
                clause,
                record=ClauseRecord.model_validate(
                    {**clause.record.model_dump(), "terms_alias": "synthetic-other-source"}
                ),
            ),
        )
    elif fault == "unknown_fact":
        identities = (replace(fact, source_fact_id="synthetic-unknown-fact"),)
    elif fault == "duplicate_fact":
        identities = (fact, fact)
    else:
        clauses = (clause, clause)
    with pytest.raises(LegacyAdapterError) as error:
        adapt_legacy_review(
            context, section=section, review=review, clauses=clauses, fact_ids=identities
        )
    assert str(error.value).startswith("LEGACY_")
    assert "synthetic" not in str(error.value)


def test_mutated_invalid_model_is_sanitized_without_serializer_warnings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import warnings

    context, section, review, clause, fact = fixture()
    review.facts[0].numeric_terms_ko.append({"synthetic-sensitive": "Synthetic private detail."})
    with (
        warnings.catch_warnings(record=True) as captured,
        pytest.raises(LegacyAdapterError, match="^LEGACY_RECORD_INVALID$") as error,
    ):
        adapt_legacy_review(
            context, section=section, review=review, clauses=(clause,), fact_ids=(fact,)
        )
    assert not captured
    assert "Synthetic private detail." not in repr(error.value)
    assert capsys.readouterr().err == ""


def test_declared_counts_are_retained_and_mismatch_is_diagnostic() -> None:
    context, section, review, clause, fact = fixture()
    section = TermsSectionRecord.model_validate({**section.model_dump(), "semantic_fact_count": 4})
    result = adapt_legacy_review(
        context, section=section, review=review, clauses=(clause,), fact_ids=(fact,)
    )
    assert result.counts.fact_count == 1
    assert result.restore_section().semantic_fact_count == 4
    assert "LEGACY_COUNT_MISMATCH" in result.reason_codes
