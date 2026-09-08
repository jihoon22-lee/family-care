"""A scoped original-Clause change reaches both editions and no adjacent article."""

from datetime import date
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from familycare_api.clauses.source_repository import ClauseSourceProjector
from familycare_api.clauses.terms_change_repository import TermsChangeProjector, read_event_terms
from familycare_api.clauses.terms_change_selection import TermsSelectionScope
from psycopg.rows import dict_row

from apps.api.tests.test_clause_source_publication import _BODY_7, _BODY_8, _TERMS, _clause
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


def _native_link(
    connection: Any, household: UUID, rider: UUID, clause: UUID, edition: UUID
) -> UUID:
    link, candidate = uuid4(), uuid4()
    rows = connection.execute(
        "SELECT e.* FROM evidence e WHERE e.id IN("
        "SELECT source_evidence_id FROM riders WHERE id=%s UNION "
        "SELECT evidence_id FROM clause_evidence WHERE clause_id=%s) ORDER BY e.id",
        (rider, clause),
    ).fetchall()
    _insert_candidate(
        connection,
        candidate_id=candidate,
        review_item_id=uuid4(),
        household_id=household,
        candidate_kind="rider_clause",
        aggregate_id=link,
        evidence=tuple(
            ("clause_id", r["document_version_id"], r["id"], r["physical_page"]) for r in rows
        ),
    )
    connection.execute(
        "UPDATE analysis_candidate_evidence c SET x0=e.x0,y0=e.y0,x1=e.x1,y1=e.y1 "
        "FROM evidence e WHERE c.evidence_id=e.id AND c.candidate_version_id=%s",
        (candidate,),
    )
    connection.execute(
        "INSERT INTO rider_clause_links(id,household_space_id,rider_id,terms_edition_id,clause_id,"
        "candidate_version_id,review_state,applicability_reason_code) "
        "VALUES(%s,%s,%s,%s,%s,%s,'NEEDS_REVIEW','TERMS_EDITION_NOT_APPLICABLE')",
        (link, household, rider, edition, clause, candidate),
    )
    for row in rows:
        connection.execute(
            "INSERT INTO rider_clause_link_evidence VALUES(%s,%s)", (link, row["id"])
        )
    return link


def _paired_sources(url: str, job: Any, *, change_scope: str = "조항") -> dict[str, Any]:
    sources = _sources(
        url,
        job,
        terms_body=_TERMS,
        change_scope=change_scope,
        change_fields=("대상조항: 제7조",) if change_scope == "조항" else (),
    )
    clauses = {}
    links = {}
    for edition in ("EDITION-A", "EDITION-B"):
        for number, body in ((7, _BODY_7), (8, _BODY_8)):
            clause = _clause(url, job, sources[edition], body=body, label=f"제{number}조")
            clauses[edition, number] = clause
            with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
                links[edition, number] = _native_link(
                    connection,
                    job.household_space_id,
                    sources["Sample Rider"],
                    clause,
                    sources[edition],
                )
    assert ClauseSourceProjector(url).refresh_pending(limit=25) == 4
    return {**sources, "clauses": clauses, "links": links}


def test_clause_change_reaches_both_original_sides_only(changes_database: Any) -> None:
    url, job = changes_database
    sources = _paired_sources(url, job)
    assert TermsChangeProjector(url).refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        relation = connection.execute("SELECT * FROM current_policy_terms_changes").fetchone()
        assert relation["status"] == "MATCH"
        assert relation["previous_clause_id"] == sources["clauses"]["EDITION-A", 7]
        assert relation["new_clause_id"] == sources["clauses"]["EDITION-B", 7]
        for number in (7, 8):
            for edition in ("EDITION-A", "EDITION-B"):
                scope = TermsSelectionScope(
                    job.household_space_id,
                    sources["policy_id"],
                    job.family_member_id,
                    sources["Sample Rider"],
                    sources["clauses"][edition, number],
                )
                for day in (date(2025, 6, 30), date(2025, 7, 1)):
                    selection = read_event_terms(connection, scope, day)
                    states = {e.edition_id: e.status for e in selection.editions}
                    changed = number == 7 and day.month == 7
                    assert states[sources["EDITION-A"]] == ("NO_MATCH" if changed else "MATCH")
                    assert states[sources["EDITION-B"]] == ("MATCH" if changed else "NO_MATCH")
                    assert bool(selection.applied_relation_ids) is changed


def test_rider_change_does_not_depend_on_unrelated_clause_discovery(changes_database: Any) -> None:
    url, job = changes_database
    sources = _sources(url, job, terms_body=_TERMS)
    assert TermsChangeProjector(url).refresh_pending() == 1
    _clause(url, job, sources["EDITION-A"])
    assert ClauseSourceProjector(url).refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM current_policy_terms_changes").fetchone()[
                "n"
            ]
            == 1
        )
        scope = TermsSelectionScope(
            job.household_space_id,
            sources["policy_id"],
            job.family_member_id,
            sources["Sample Rider"],
        )
        selected = read_event_terms(connection, scope, date(2025, 7, 1))
        assert (
            next(e.status for e in selected.editions if e.edition_id == sources["EDITION-B"])
            == "MATCH"
        )


def _native_rule(connection: Any, household: UUID, link: UUID) -> tuple[UUID, UUID]:
    from psycopg.types.json import Jsonb

    rule, version, candidate = uuid4(), uuid4(), uuid4()
    evidence = connection.execute(
        "SELECT e.* FROM rider_clause_link_evidence l JOIN evidence e ON e.id=l.evidence_id "
        "WHERE l.rider_clause_link_id=%s ORDER BY e.id",
        (link,),
    ).fetchall()
    _insert_candidate(
        connection,
        candidate_id=candidate,
        review_item_id=uuid4(),
        household_id=household,
        candidate_kind="coverage_rule",
        aggregate_id=rule,
        evidence=tuple(
            ("rule_kind", e["document_version_id"], e["id"], e["physical_page"]) for e in evidence
        ),
    )
    connection.execute(
        "UPDATE analysis_candidate_evidence c SET x0=e.x0,y0=e.y0,x1=e.x1,y1=e.y1 "
        "FROM evidence e WHERE c.evidence_id=e.id AND c.candidate_version_id=%s",
        (candidate,),
    )
    connection.execute(
        "INSERT INTO coverage_rules(id,household_space_id,rider_clause_link_id,rule_key) "
        "VALUES(%s,%s,%s,'synthetic-clause-change')",
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
        "evidence_ids": [str(e["id"]) for e in evidence],
    }
    connection.execute(
        "INSERT INTO "
        "coverage_rule_versions(id,coverage_rule_id,candidate_version_id,version_number,"
        "schema_version,rule_kind,required,input_field_paths,expression_json,result_reason_code,"
        "review_state,executable,generator_version,verifier_version) "
        "VALUES(%s,%s,%s,1,'coverage-rule-v1','temporal',true,%s,%s,'SYNTHETIC_TEMPORAL_MATCH',"
        "'AI_VERIFIED',false,'synthetic-v1','synthetic-v1')",
        (version, rule, candidate, Jsonb(document["input_field_paths"]), Jsonb(document)),
    )
    for item in evidence:
        connection.execute(
            "INSERT INTO coverage_rule_evidence VALUES(%s,%s)", (version, item["id"])
        )
    return rule, version


@pytest.mark.parametrize("change_scope", ["조항", "특약"])
def test_changed_scope_controls_confirmation_and_publication(
    changes_database: Any, change_scope: str
) -> None:
    from familycare_api.clauses.errors import CoverageRuleInvalid, RiderClauseLinkInvalid
    from familycare_api.clauses.repository import CoverageRuleRepository, RiderClauseLinkRepository
    from familycare_api.common.scope import HouseholdScope

    url, job = changes_database
    sources = _paired_sources(url, job, change_scope=change_scope)
    scope = HouseholdScope(job.household_space_id)
    links = RiderClauseLinkRepository(url)
    rules = CoverageRuleRepository(url)
    drafts = {}
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        for number in (7, 8):
            drafts[number] = _native_rule(
                connection, job.household_space_id, sources["links"]["EDITION-B", number]
            )
    for number in (7, 8):
        with pytest.raises(RiderClauseLinkInvalid):
            links.confirm(scope, sources["links"]["EDITION-B", number], expected_version=1)
    assert TermsChangeProjector(url).refresh_pending() == 1
    confirmed = links.confirm(scope, sources["links"]["EDITION-B", 7], expected_version=2)
    assert confirmed.review_state == "USER_CONFIRMED"
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM current_policy_terms_changes").fetchone()[
                "n"
            ]
            == 1
        )
    published = rules.publish(scope, *drafts[7], expected_version=1)
    assert published.executable and published.version_number == 2
    if change_scope == "조항":
        with pytest.raises(RiderClauseLinkInvalid):
            links.confirm(scope, sources["links"]["EDITION-B", 8], expected_version=2)
        with pytest.raises(CoverageRuleInvalid):
            rules.publish(scope, *drafts[8], expected_version=1)
    else:
        links.confirm(scope, sources["links"]["EDITION-B", 8], expected_version=2)
        assert rules.publish(scope, *drafts[8], expected_version=1).executable


def test_changed_clause_evidence_keeps_known_targets_uncertain_after_refresh(
    changes_database: Any,
) -> None:
    url, job = changes_database
    sources = _paired_sources(url, job)
    assert TermsChangeProjector(url).refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        original = connection.execute("SELECT * FROM current_policy_terms_changes").fetchone()
        connection.execute(
            "UPDATE evidence SET review_state='NEEDS_REVIEW' WHERE id IN("
            "SELECT evidence_id FROM clause_evidence WHERE clause_id=%s)",
            (sources["clauses"]["EDITION-A", 7],),
        )
    assert ClauseSourceProjector(url).refresh_pending() == 1
    assert TermsChangeProjector(url).refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        current = connection.execute("SELECT * FROM current_policy_terms_changes").fetchone()
        assert current["status"] == "UNKNOWN"
        assert current["previous_clause_id"] == original["previous_clause_id"]
        assert current["new_clause_id"] == original["new_clause_id"]
        for edition in ("EDITION-A", "EDITION-B"):
            scope = TermsSelectionScope(
                job.household_space_id,
                sources["policy_id"],
                job.family_member_id,
                sources["Sample Rider"],
                sources["clauses"][edition, 7],
            )
            selected = read_event_terms(connection, scope, date(2025, 7, 1))
            assert {e.status for e in selected.editions} == {"UNKNOWN"}
            assert selected.uncertain_relation_ids


def test_ambiguous_clause_ids_remain_uncertain_for_each_matching_original(
    changes_database: Any,
) -> None:
    url, job = changes_database
    sources = _paired_sources(url, job)
    duplicates = []
    for edition in ("EDITION-A", "EDITION-B"):
        duplicate = _clause(url, job, sources[edition])
        duplicates.append((edition, duplicate))
        with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
            _native_link(
                connection,
                job.household_space_id,
                sources["Sample Rider"],
                duplicate,
                sources[edition],
            )
    assert ClauseSourceProjector(url).refresh_pending() == 2
    assert TermsChangeProjector(url).refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        row = connection.execute("SELECT * FROM current_policy_terms_changes").fetchone()
        assert row["status"] == "UNKNOWN" and not row["scope_resolved"]
        for _edition, clause in [
            *duplicates,
            *((edition, sources["clauses"][edition, 7]) for edition in ("EDITION-A", "EDITION-B")),
        ]:
            scope = TermsSelectionScope(
                job.household_space_id,
                sources["policy_id"],
                job.family_member_id,
                sources["Sample Rider"],
                clause,
            )
            selection = read_event_terms(connection, scope, date(2025, 7, 1))
            assert {e.status for e in selection.editions} == {"UNKNOWN"}
            assert selection.uncertain_relation_ids
        for edition in ("EDITION-A", "EDITION-B"):
            scope = TermsSelectionScope(
                job.household_space_id,
                sources["policy_id"],
                job.family_member_id,
                sources["Sample Rider"],
                sources["clauses"][edition, 8],
            )
            selection = read_event_terms(connection, scope, date(2025, 7, 1))
            assert not selection.uncertain_relation_ids


def test_chained_clause_changes_retain_the_original_predecessor_for_every_side(
    changes_database: Any,
) -> None:
    from familycare_api.clauses.component_editions import ComponentTermsProjector
    from familycare_api.clauses.terms_applicability_repository import TermsApplicabilityProjector

    from apps.api.tests.test_terms_change_integration import _add_document

    url, job = changes_database
    sources = _paired_sources(url, job)
    _add_document(
        url,
        job,
        "보험약관\n보험사: Sample Insurer\n상품명: Sample Plan\n상품코드: SAMPLE-P\n"
        f"약관코드: TERMS-C\n판본코드: EDITION-C\n{_TERMS}",
        kind="terms",
        digest="e" * 64,
    )
    assert ComponentTermsProjector(url).project_pending() == 1
    assert TermsApplicabilityProjector(url).refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        edition_c = connection.execute(
            "SELECT id FROM terms_editions WHERE content_sha256=%s", ("e" * 64,)
        ).fetchone()["id"]
    clause_c = _clause(url, job, edition_c)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        link_c = _native_link(
            connection, job.household_space_id, sources["Sample Rider"], clause_c, edition_c
        )
    assert ClauseSourceProjector(url).refresh_pending() == 1
    _add_document(
        url,
        job,
        "\n".join(
            (
                "계약변경서",
                "계약번호: synthetic-policy-001",
                "피보험자: Family Member A",
                "보험사: Sample Insurer",
                "변경구분: 조건변경",
                "변경방식: 교체",
                "적용범위: 조항",
                "대상특약명: Sample Rider",
                "대상조항: 제7조",
                "변경전약관코드: TERMS-B",
                "변경전판본코드: EDITION-B",
                "변경후약관코드: TERMS-C",
                "변경후판본코드: EDITION-C",
                "변경적용일: 2025-10-01",
            )
        ),
        kind="amendment",
        digest="f" * 64,
    )
    assert TermsChangeProjector(url).refresh_pending() == 2
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        for clause in (
            sources["clauses"]["EDITION-A", 7],
            sources["clauses"]["EDITION-B", 7],
            clause_c,
        ):
            scope = TermsSelectionScope(
                job.household_space_id,
                sources["policy_id"],
                job.family_member_id,
                sources["Sample Rider"],
                clause,
            )
            for day, expected in (
                (date(2025, 6, 30), sources["EDITION-A"]),
                (date(2025, 7, 1), sources["EDITION-B"]),
                (date(2025, 10, 1), edition_c),
            ):
                selection = read_event_terms(connection, scope, day)
                assert {e.edition_id for e in selection.editions if e.status == "MATCH"} == {
                    expected
                }
                assert not selection.uncertain_relation_ids

    from familycare_api.clauses.repository import CoverageRuleRepository, RiderClauseLinkRepository
    from familycare_api.common.scope import HouseholdScope
    from familycare_api.decisions.repository import DecisionRepository

    household = HouseholdScope(job.household_space_id)
    for link in (sources["links"]["EDITION-A", 7], sources["links"]["EDITION-B", 7], link_c):
        with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
            draft = _native_rule(connection, job.household_space_id, link)
        RiderClauseLinkRepository(url).confirm(household, link, expected_version=1)
        CoverageRuleRepository(url).publish(household, *draft, expected_version=1)
    repository = DecisionRepository(url)
    for day in (date(2025, 7, 1), date(2025, 10, 1)):
        event = repository.create_medical_event(
            household,
            family_member_id=job.family_member_id,
            mode="post_treatment",
            situation="Synthetic chained change event",
            event_date=day,
            visit_date=None,
            facts={},
        )
        result = repository.analyze_medical_event(household, event.id)
        assert len(
            next(
                s for s in result.terms_selections if s.scope.clause_id == clause_c
            ).applied_relation_ids
        ) == (1 if day.month == 7 else 2)
        assert (
            repository.get_decision_result(household, event.id, event.version).terms_selections
            == result.terms_selections
        )


def test_conflicting_new_labels_do_not_reuse_the_generic_old_label(changes_database: Any) -> None:
    url, job = changes_database
    sources = _sources(
        url,
        job,
        terms_body=_TERMS,
        change_scope="조항",
        change_fields=("대상조항: 제7조", "변경후조항: 제9조", "변경후조항: 제11조"),
    )
    clauses = {}
    for edition in ("EDITION-A", "EDITION-B"):
        clauses[edition] = _clause(url, job, sources[edition])
        with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
            _native_link(
                connection,
                job.household_space_id,
                sources["Sample Rider"],
                clauses[edition],
                sources[edition],
            )
    assert ClauseSourceProjector(url).refresh_pending() == 2
    assert TermsChangeProjector(url).refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        row = connection.execute("SELECT * FROM current_policy_terms_changes").fetchone()
        assert row["status"] == "UNKNOWN" and row["previous_clause_id"] == clauses["EDITION-A"]
        assert row["new_clause_id"] is None
        scope = TermsSelectionScope(
            job.household_space_id,
            sources["policy_id"],
            job.family_member_id,
            sources["Sample Rider"],
            clauses["EDITION-B"],
        )
        selected = read_event_terms(connection, scope, date(2025, 7, 1))
        assert not selected.uncertain_relation_ids


def test_forged_pair_cannot_substitute_another_original_clause(changes_database: Any) -> None:
    import json

    from familycare_api.clauses.errors import RiderClauseLinkInvalid
    from familycare_api.clauses.repository import RiderClauseLinkRepository
    from familycare_api.common.scope import HouseholdScope
    from psycopg.types.json import Jsonb

    url, job = changes_database
    sources = _paired_sources(url, job)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        component = connection.execute(
            "SELECT id FROM insurance_document_components WHERE role='amendment'"
        ).fetchone()["id"]
        with connection.transaction(force_rollback=True):
            assert TermsChangeProjector(url)._refresh(connection, component, job.household_space_id)
            row = connection.execute("SELECT * FROM policy_terms_changes").fetchone()
        row["clause_id"] = sources["clauses"]["EDITION-A", 8]
        for side, edition in (("previous", "EDITION-A"), ("new", "EDITION-B")):
            row[f"{side}_clause_id"] = sources["clauses"][edition, 8]
            row[f"{side}_clause_source_id"] = connection.execute(
                "SELECT id FROM current_clause_source_assessments WHERE clause_id=%s",
                (row[f"{side}_clause_id"],),
            ).fetchone()["id"]
        connection.execute(
            "INSERT INTO policy_terms_changes SELECT * FROM "
            "jsonb_populate_record(NULL::policy_terms_changes,"
            "jsonb_set(%s::jsonb,'{input_context}',terms_change_input_context(%s,%s,'CLAUSE')))",
            (
                Jsonb(row, dumps=lambda data: json.dumps(data, default=str)),
                component,
                job.household_space_id,
            ),
        )
    with pytest.raises(RiderClauseLinkInvalid):
        RiderClauseLinkRepository(url).confirm(
            HouseholdScope(job.household_space_id),
            sources["links"]["EDITION-B", 8],
            expected_version=1,
        )
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        for number in (7, 8):
            scope = TermsSelectionScope(
                job.household_space_id,
                sources["policy_id"],
                job.family_member_id,
                sources["Sample Rider"],
                sources["clauses"]["EDITION-B", number],
            )
            selection = read_event_terms(connection, scope, date(2025, 7, 1))
            if number == 8:
                assert not selection.applied_relation_ids and not selection.uncertain_relation_ids
            else:
                assert selection.uncertain_relation_ids and not selection.applied_relation_ids


def test_real_event_analysis_retains_both_clause_scope_snapshots(changes_database: Any) -> None:
    from familycare_api.clauses.repository import CoverageRuleRepository, RiderClauseLinkRepository
    from familycare_api.common.scope import HouseholdScope
    from familycare_api.decisions.repository import DecisionRepository

    url, job = changes_database
    sources = _paired_sources(url, job)
    scope = HouseholdScope(job.household_space_id)
    drafts = {}
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        for edition in ("EDITION-A", "EDITION-B"):
            drafts[edition] = _native_rule(
                connection, job.household_space_id, sources["links"][edition, 7]
            )
    assert TermsChangeProjector(url).refresh_pending() == 1
    for edition in ("EDITION-A", "EDITION-B"):
        RiderClauseLinkRepository(url).confirm(
            scope, sources["links"][edition, 7], expected_version=1
        )
        CoverageRuleRepository(url).publish(scope, *drafts[edition], expected_version=1)
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
    import json
    from copy import deepcopy
    from dataclasses import asdict

    from psycopg.types.json import Jsonb

    read = repository.executable_for_rider(
        scope,
        sources["Sample Rider"],
        family_member_id=job.family_member_id,
        event_date=event.event_date,
    )
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert connection.execute(
            "SELECT decision_terms_snapshot_valid(%s,%s,%s,%s) AS valid",
            (
                Jsonb(
                    [asdict(s) for s in read.terms_selections],
                    dumps=lambda data: json.dumps(data, default=str),
                ),
                job.household_space_id,
                event.id,
                event.version,
            ),
        ).fetchone()["valid"]
        original = next(asdict(s) for s in read.terms_selections if s.scope.clause_id is not None)
        for mutation in ("unrelated_clause", "missing_relation", "invalid_shape"):
            malformed = deepcopy(original)
            if mutation == "unrelated_clause":
                malformed["scope"]["clause_id"] = sources["clauses"]["EDITION-A", 8]
            elif mutation == "missing_relation":
                malformed["scope_relation_ids"] = (uuid4(),)
            else:
                malformed["scope_relation_ids"] = "synthetic-invalid-array"
            assert not connection.execute(
                "SELECT decision_terms_snapshot_valid(%s,%s,%s,%s) AS valid",
                (
                    Jsonb([malformed], dumps=lambda data: json.dumps(data, default=str)),
                    job.household_space_id,
                    event.id,
                    event.version,
                ),
            ).fetchone()["valid"]
    result = repository.analyze_medical_event(scope, event.id)
    assert {s.scope.clause_id for s in result.terms_selections if s.applied_relation_ids} == {
        sources["clauses"]["EDITION-A", 7],
        sources["clauses"]["EDITION-B", 7],
    }
    assert (
        repository.get_decision_result(scope, event.id, event.version).terms_selections
        == result.terms_selections
    )


def test_parent_article_change_does_not_leave_child_rule_confirmed(changes_database: Any) -> None:
    from familycare_api.clauses.normalization import normalize_clause_text
    from familycare_api.clauses.repository import ClauseRepository
    from familycare_api.common.scope import HouseholdScope

    url, job = changes_database
    sources = _paired_sources(url, job)
    parent = sources["clauses"]["EDITION-A", 7]
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        evidence = connection.execute(
            "SELECT evidence_id FROM clause_evidence WHERE clause_id=%s", (parent,)
        ).fetchone()["evidence_id"]
    child = ClauseRepository(url).create(
        HouseholdScope(job.household_space_id),
        terms_edition_id=sources["EDITION-A"],
        parent_clause_id=parent,
        clause_type="paragraph",
        label="제1항",
        normalized_title="합성 항",
        normalized_text=normalize_clause_text(_BODY_7),
        physical_page_start=1,
        physical_page_end=1,
        evidence_ids=(evidence,),
    )
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        _native_link(
            connection,
            job.household_space_id,
            sources["Sample Rider"],
            child.id,
            sources["EDITION-A"],
        )
    assert TermsChangeProjector(url).refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        scope = TermsSelectionScope(
            job.household_space_id,
            sources["policy_id"],
            job.family_member_id,
            sources["Sample Rider"],
            child.id,
        )
        before = read_event_terms(connection, scope, date(2025, 6, 30))
        after = read_event_terms(connection, scope, date(2025, 7, 1))
        assert (
            next(e.status for e in before.editions if e.edition_id == sources["EDITION-A"])
            == "MATCH"
        )
        assert {e.status for e in after.editions} == {"UNKNOWN"}
        assert after.uncertain_relation_ids and not after.applied_relation_ids
