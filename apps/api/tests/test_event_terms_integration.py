"""Event reads use the retained amendment without changing another Rider's terms."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from familycare_api.clauses.terms_change_repository import TermsChangeProjector
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.calculation_repository import CalculationRepository
from familycare_api.decisions.repository import DecisionRepository
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests.test_rider_clause_rules_integration import _insert_candidate
from apps.api.tests.test_terms_change_integration import (
    _psycopg_url,
    _sources,
    enrollment_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_terms_change_integration import (
    changes_database as changes_database,
)

pytestmark = pytest.mark.integration


def _published_rule(connection: Any, household: UUID, rider: UUID, edition: UUID) -> UUID:
    """Insert an independently reviewed synthetic rule; this is not a compiler test."""
    clause, link, rule, version, evidence, link_candidate, rule_candidate = (
        uuid4() for _ in range(7)
    )
    source = connection.execute(
        "SELECT e.document_version_id,e.content_sha256,g.extraction_id "
        "FROM terms_editions e JOIN terms_applicability_component_sources c "
        "ON c.id=e.source_component_id JOIN document_structure_generations g "
        "ON g.id=c.generation_id WHERE e.id=%s",
        (edition,),
    ).fetchone()
    connection.execute(
        "INSERT INTO evidence(id,household_space_id,document_version_id,extraction_id,"
        "content_sha256,physical_page,review_state) VALUES(%s,%s,%s,%s,%s,1,'AI_VERIFIED')",
        (
            evidence,
            household,
            source["document_version_id"],
            source["extraction_id"],
            source["content_sha256"],
        ),
    )
    connection.execute(
        "INSERT INTO clauses(id,household_space_id,terms_edition_id,clause_type,label,"
        "normalized_title,normalized_text,physical_page_start,physical_page_end,"
        "normalization_version) VALUES(%s,%s,%s,'article','Article A','synthetic temporal',"
        "'Synthetic reviewed event condition',1,1,'unicode-nfc-v1')",
        (clause, household, edition),
    )
    for candidate, aggregate, kind in (
        (link_candidate, link, "rider_clause"),
        (rule_candidate, rule, "coverage_rule"),
    ):
        _insert_candidate(
            connection,
            candidate_id=candidate,
            review_item_id=uuid4(),
            household_id=household,
            candidate_kind=kind,
            aggregate_id=aggregate,
            evidence=(("rule_kind", source["document_version_id"], evidence, 1),),
        )
    connection.execute(
        "INSERT INTO rider_clause_links(id,household_space_id,rider_id,terms_edition_id,"
        "clause_id,candidate_version_id,review_state,applicability_reason_code) "
        "VALUES(%s,%s,%s,%s,%s,%s,'AI_VERIFIED','APPLICABLE')",
        (link, household, rider, edition, clause, link_candidate),
    )
    connection.execute(
        "INSERT INTO coverage_rules(id,household_space_id,rider_clause_link_id,rule_key,"
        "current_status) VALUES(%s,%s,%s,'synthetic-event-condition','published')",
        (rule, household, link),
    )
    document = {
        "schema_version": "coverage-rule-v1",
        "rule_kind": "temporal",
        "required": True,
        "input_field_paths": ["MedicalEvent.event_date"],
        "expression": {
            "op": "date_between",
            "field": "MedicalEvent.event_date",
            "value": {"start": "2025-01-01", "end": "2025-12-31"},
            "unit": "date",
        },
        "result_reason_code": "SYNTHETIC_TEMPORAL_MATCH",
        "evidence_ids": [str(evidence)],
    }
    connection.execute(
        "INSERT INTO coverage_rule_versions(id,coverage_rule_id,candidate_version_id,"
        "version_number,schema_version,rule_kind,required,input_field_paths,expression_json,"
        "result_reason_code,review_state,executable,published_at,generator_version,"
        "verifier_version) "
        "VALUES(%s,%s,%s,1,'coverage-rule-v1','temporal',true,%s,%s,'SYNTHETIC_TEMPORAL_MATCH',"
        "'AI_VERIFIED',true,clock_timestamp(),'synthetic-v1','synthetic-v1')",
        (version, rule, rule_candidate, Jsonb(document["input_field_paths"]), Jsonb(document)),
    )
    connection.execute(
        "INSERT INTO coverage_rule_evidence(coverage_rule_version_id,evidence_id) VALUES(%s,%s)",
        (version, evidence),
    )
    return version


def _calculation_rule(connection: Any, household: UUID, rider: UUID, edition: UUID) -> UUID:
    version = _published_rule(connection, household, rider, edition)
    connection.execute(
        "UPDATE coverage_rule_versions SET rule_kind='rate_amount',input_field_paths=%s,"
        "expression_json=(expression_json-'expression') || %s WHERE id=%s",
        (
            Jsonb(["Rider.insured_amount"]),
            Jsonb(
                {
                    "rule_kind": "rate_amount",
                    "input_field_paths": ["Rider.insured_amount"],
                    "calculation": {
                        "op": "multiply",
                        "args": [{"field": "Rider.insured_amount"}, {"value": 1}],
                    },
                }
            ),
            version,
        ),
    )
    return version


@pytest.mark.parametrize("missing_date", [False, True])
def test_event_rules_keep_edition_uncertainty_and_other_rider_independent(
    changes_database: Any,
    missing_date: bool,
) -> None:
    url, job = changes_database
    sources = _sources(url, job, missing_date=missing_date)
    assert TermsChangeProjector(url).refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        rules = {
            (rider, edition): _published_rule(
                connection,
                job.household_space_id,
                sources[rider],
                sources[edition],
            )
            for rider in ("Sample Rider", "Another Rider")
            for edition in ("EDITION-A", "EDITION-B")
        }
    repository = DecisionRepository(url)
    scope = HouseholdScope(job.household_space_id)
    for rider in ("Sample Rider", "Another Rider"):
        for event_date in (date(2025, 6, 30), date(2025, 7, 1)):
            read = repository.executable_for_rider(
                scope,
                sources[rider],
                family_member_id=job.family_member_id,
                event_date=event_date,
            )
            assert read.terms_selections
            assert all(s.base_assessment_ids for s in read.terms_selections)
            states = {rule.id: read.status_for(rule.id) for rule in read}
            if missing_date and rider == "Sample Rider":
                assert states == {
                    rules[rider, edition]: "UNKNOWN" for edition in ("EDITION-A", "EDITION-B")
                }
            else:
                edition = (
                    "EDITION-B"
                    if rider == "Sample Rider" and event_date.month == 7
                    else "EDITION-A"
                )
                assert states == {rules[rider, edition]: "MATCH"}
    assert not repository.executable_for_rider(
        scope,
        sources["Sample Rider"],
        family_member_id=uuid4(),
        event_date=date(2025, 7, 1),
    )


def test_event_selection_is_retained_when_no_rules_exist(changes_database: Any) -> None:
    url, job = changes_database
    sources = _sources(url, job)
    assert TermsChangeProjector(url).refresh_pending() == 1
    read = DecisionRepository(url).executable_for_rider(
        HouseholdScope(job.household_space_id),
        sources["Sample Rider"],
        family_member_id=job.family_member_id,
        event_date=date(2025, 7, 1),
    )
    assert not read and len(read.terms_selections) == 1
    assert read.terms_selections[0].applied_relation_ids


def test_stale_base_evidence_is_unknown_instead_of_a_decisive_mismatch(
    changes_database: Any,
) -> None:
    url, job = changes_database
    sources = _sources(url, job)
    assert TermsChangeProjector(url).refresh_pending() == 1
    rider = sources["Sample Rider"]
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        version = _published_rule(connection, job.household_space_id, rider, sources["EDITION-A"])
        connection.execute(
            "UPDATE policy_contracts SET product_display='Sample Corrected Plan',version=version+1 "
            "WHERE id=%s",
            (sources["policy_id"],),
        )
    read = DecisionRepository(url).executable_for_rider(
        HouseholdScope(job.household_space_id),
        rider,
        family_member_id=job.family_member_id,
        event_date=date(2025, 6, 30),
    )
    assert [rule.id for rule in read] == [version]
    assert read.status_for(version) == "UNKNOWN"


def test_decision_retains_selection_after_source_and_event_change(changes_database: Any) -> None:
    url, job = changes_database
    sources = _sources(url, job)
    assert TermsChangeProjector(url).refresh_pending() == 1
    repository = DecisionRepository(url)
    scope = HouseholdScope(job.household_space_id)
    event = repository.create_medical_event(
        scope,
        family_member_id=job.family_member_id,
        mode="post_treatment",
        situation="Synthetic event",
        event_date=date(2025, 7, 1),
        visit_date=None,
        facts={},
    )
    result = repository.analyze_medical_event(scope, event.id)
    assert result.terms_selections
    loaded = repository.get_decision_result(scope, event.id, event.version)
    assert loaded.terms_selections == result.terms_selections
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        connection.execute(
            "UPDATE policy_contracts SET product_display='Sample Corrected Plan',version=version+1 "
            "WHERE id=%s",
            (sources["policy_id"],),
        )
        connection.execute(
            "UPDATE medical_events SET event_date='2025-06-01',version=version+1 WHERE id=%s",
            (event.id,),
        )
    loaded = repository.get_decision_result(scope, event.id, event.version)
    assert loaded.terms_selections == result.terms_selections
    new_result = repository.analyze_medical_event(scope, event.id)
    assert all(s.event_date == date(2025, 6, 1) for s in new_result.terms_selections)
    assert (
        repository.get_decision_result(scope, event.id, event.version).terms_selections
        == result.terms_selections
    )
    with (
        psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection,
        pytest.raises(psycopg.IntegrityError),
        connection.transaction(),
    ):
        connection.execute(
            "UPDATE decision_runs SET terms_selections_json='[]' WHERE id=%s",
            (result.run_id,),
        )


def test_calculation_reads_captured_event_terms_and_keeps_publication_cutoff(
    changes_database: Any,
) -> None:
    url, job = changes_database
    sources = _sources(url, job)
    assert TermsChangeProjector(url).refresh_pending() == 1
    rider = sources["Sample Rider"]
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        old = _calculation_rule(connection, job.household_space_id, rider, sources["EDITION-A"])
        new = _calculation_rule(connection, job.household_space_id, rider, sources["EDITION-B"])
    scope = HouseholdScope(job.household_space_id)
    repository = DecisionRepository(url)
    june = repository.executable_for_rider(
        scope, rider, family_member_id=job.family_member_id, event_date=date(2025, 6, 30)
    )
    july = repository.executable_for_rider(
        scope, rider, family_member_id=job.family_member_id, event_date=date(2025, 7, 1)
    )
    cutoff = datetime.now(UTC)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        connection.execute(
            "UPDATE policy_contracts SET product_display='Sample Corrected Plan',version=version+1 "
            "WHERE id=%s",
            (sources["policy_id"],),
        )
        for day, snapshot, expected in (
            (date(2025, 6, 30), june, old),
            (date(2025, 7, 1), july, new),
        ):
            read = CalculationRepository._calculation_rules(
                connection,
                scope,
                rider,
                cutoff,
                family_member_id=job.family_member_id,
                event_date=day,
                terms_selections=snapshot.terms_selections,
                rule_version_ids=tuple(rule.id for rule in snapshot),
            )
            assert [rule.id for rule in read] == [expected]
            assert read.status_for(expected) == "MATCH"
        assert not CalculationRepository._calculation_rules(
            connection,
            scope,
            rider,
            cutoff - timedelta(days=1),
            family_member_id=job.family_member_id,
            event_date=date(2025, 7, 1),
            terms_selections=july.terms_selections,
            rule_version_ids=tuple(rule.id for rule in july),
        )
        assert not CalculationRepository._calculation_rules(
            connection,
            scope,
            rider,
            cutoff,
            family_member_id=uuid4(),
            event_date=date(2025, 7, 1),
            terms_selections=july.terms_selections,
            rule_version_ids=tuple(rule.id for rule in july),
        )


def test_calculation_preserves_unknown_terms_without_producing_an_amount(
    changes_database: Any,
) -> None:
    url, job = changes_database
    sources = _sources(url, job, missing_date=True)
    rider = sources["Sample Rider"]
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        connection.execute(
            "UPDATE riders SET benefit_type='fixed',version=version+1 WHERE id=%s", (rider,)
        )
        _calculation_rule(connection, job.household_space_id, rider, sources["EDITION-A"])
    assert TermsChangeProjector(url).refresh_pending() == 1
    scope = HouseholdScope(job.household_space_id)
    repository = DecisionRepository(url)
    event = repository.create_medical_event(
        scope,
        family_member_id=job.family_member_id,
        mode="post_treatment",
        situation="Synthetic event",
        event_date=date(2025, 7, 1),
        visit_date=None,
        facts={},
    )
    result = repository.analyze_medical_event(scope, event.id)
    values = CalculationRepository(url).calculate_event(
        scope, event.id, decision_run_id=result.run_id
    )
    assert len(values) == 1
    assert values[0]["status"] == "unknown"
    assert values[0]["hold_reason_codes"] == ("TERMS_APPLICABILITY_UNRESOLVED",)
    assert values[0]["confirmed"] is None


def test_rule_published_during_analysis_cannot_replace_the_captured_formula(
    changes_database: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url, job = changes_database
    sources = _sources(url, job)
    rider = sources["Sample Rider"]
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        connection.execute(
            "UPDATE riders SET benefit_type='fixed',version=version+1 WHERE id=%s", (rider,)
        )
        old = _calculation_rule(connection, job.household_space_id, rider, sources["EDITION-A"])
    assert TermsChangeProjector(url).refresh_pending() == 1
    scope = HouseholdScope(job.household_space_id)
    repository = DecisionRepository(url)
    event = repository.create_medical_event(
        scope,
        family_member_id=job.family_member_id,
        mode="post_treatment",
        situation="Synthetic event",
        event_date=date(2025, 6, 30),
        visit_date=None,
        facts={},
    )
    persist = repository._persist_result
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as publisher:
        # The write precedes analysis, but its commit follows the repeatable-read snapshot.
        newer = publisher.execute(
            "SELECT to_jsonb(v) AS value FROM coverage_rule_versions v WHERE id=%s", (old,)
        ).fetchone()["value"]
        newer.update(id=str(uuid4()), version_number=2, published_at=datetime.now(UTC).isoformat())
        newer["expression_json"]["calculation"]["args"][1]["value"] = 2
        publisher.execute(
            "INSERT INTO coverage_rule_versions "
            "SELECT (jsonb_populate_record(NULL::coverage_rule_versions,%s)).*",
            (Jsonb(newer),),
        )
        publisher.execute(
            "INSERT INTO coverage_rule_evidence(coverage_rule_version_id,evidence_id) "
            "SELECT %s,evidence_id FROM coverage_rule_evidence WHERE coverage_rule_version_id=%s",
            (UUID(newer["id"]), old),
        )
        publisher.execute(
            "UPDATE coverage_rules SET version=2 WHERE id=%s", (UUID(newer["coverage_rule_id"]),)
        )

        def publish_between_read_and_persist(connection: Any, requested: Any, result: Any) -> None:
            publisher.commit()
            persist(connection, requested, result)

        monkeypatch.setattr(repository, "_persist_result", publish_between_read_and_persist)
        result = repository.analyze_medical_event(scope, event.id)
    values = CalculationRepository(url).calculate_event(
        scope, event.id, decision_run_id=result.run_id
    )
    assert len(values) == 1 and values[0]["rule_version_id"] == old
    assert result.source_rule_version_ids == (old,)
    assert repository.get_decision_result(
        scope, event.id, event.version
    ).source_rule_version_ids == (old,)
