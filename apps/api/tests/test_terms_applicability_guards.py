"""Synthetic source, user-history and retry guards for terms applicability."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from familycare_api.clauses import terms_applicability_repository
from familycare_api.common.scope import HouseholdScope
from familycare_api.insurance_documents.repository import InsuranceDocumentRepository
from familycare_api.policies.domain import CreatePolicyParty
from familycare_api.policies.repository import PolicyLedgerRepository
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests import test_insurance_document_inventory_integration as inventory_seed
from apps.api.tests import test_terms_applicability_integration as applicability_seed
from apps.api.tests.test_rider_clause_rules_integration import database_url as database_url

pytestmark = pytest.mark.integration


def _assessments(url: str) -> list[dict[str, Any]]:
    with psycopg.connect(inventory_seed._psycopg_url(url), row_factory=dict_row) as connection:
        rows = connection.execute(
            "SELECT to_jsonb(assessment) AS record FROM policy_terms_applicability assessment "
            "ORDER BY created_at,id"
        ).fetchall()
    return [row["record"] for row in rows]


def _applies(
    url: str, edition_id: UUID, *, household_id: UUID = inventory_seed.HOUSEHOLD_ID
) -> bool:
    with psycopg.connect(inventory_seed._psycopg_url(url), row_factory=dict_row) as connection:
        row = connection.execute(
            "SELECT policy_terms_edition_applies(%s,%s,%s) AS applies",
            (inventory_seed.POLICY_ID, edition_id, household_id),
        ).fetchone()
    assert row is not None
    return bool(row["applies"])


def _matched(url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    sources = applicability_seed._sources(url)
    assert terms_applicability_repository.TermsApplicabilityProjector(url).refresh_pending() == 1
    rows = _assessments(url)
    assert len(rows) == 1 and rows[0]["status"] == "MATCH"
    assert _applies(url, sources["edition_id"])
    return sources, rows[0]


def _assert_retained(url: str, original: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _assessments(url)
    assert [row for row in rows if row["id"] == original["id"]] == [original]
    return rows


def _set_history(url: str, set_id: UUID) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with psycopg.connect(inventory_seed._psycopg_url(url), row_factory=dict_row) as connection:
        document_set = connection.execute(
            "SELECT to_jsonb(document_set) AS record FROM insurance_document_sets document_set "
            "WHERE id=%s",
            (set_id,),
        ).fetchone()
        items = connection.execute(
            "SELECT to_jsonb(item) AS record FROM insurance_document_set_items item "
            "WHERE insurance_document_set_id=%s ORDER BY id",
            (set_id,),
        ).fetchall()
    assert document_set is not None
    return document_set["record"], [row["record"] for row in items]


def test_parallel_refresh_records_one_assessment(database_url: str) -> None:
    sources = applicability_seed._sources(database_url)
    start = Barrier(2)

    def refresh() -> int:
        start.wait(timeout=10)
        return terms_applicability_repository.TermsApplicabilityProjector(
            database_url
        ).refresh_pending()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(refresh)
        second = executor.submit(refresh)
        results = first.result(timeout=60), second.result(timeout=60)
    assert sum(results) == 1
    rows = _assessments(database_url)
    assert len(rows) == 1 and rows[0]["status"] == "MATCH"
    assert _applies(database_url, sources["edition_id"])
    assert (
        terms_applicability_repository.TermsApplicabilityProjector(database_url).refresh_pending()
        == 0
    )
    assert _assessments(database_url) == rows


def test_assessment_updates_and_deletes_cannot_rewrite_history(database_url: str) -> None:
    sources, original = _matched(database_url)
    with psycopg.connect(inventory_seed._psycopg_url(database_url)) as connection:
        for statement in (
            "UPDATE policy_terms_applicability SET status='UNKNOWN',matched_by=NULL WHERE id=%s",
            "DELETE FROM policy_terms_applicability WHERE id=%s",
        ):
            with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
                connection.execute(statement, (original["id"],))
    assert _assessments(database_url) == [original]
    assert _applies(database_url, sources["edition_id"])


def test_assessment_rejects_wrong_source_digest_and_member_scope(database_url: str) -> None:
    sources, original = _matched(database_url)
    assert not _applies(database_url, sources["edition_id"], household_id=uuid4())
    changes = (
        {"policy_publication_id": original["terms_publication_id"]},
        {"input_digest": "0" * 64},
        {"family_member_id": str(inventory_seed.MEMBER_B_ID)},
    )
    with psycopg.connect(inventory_seed._psycopg_url(database_url)) as connection:
        assert (
            connection.execute(
                "SELECT policy_terms_input_context(%s,%s,%s)",
                (inventory_seed.POLICY_ID, inventory_seed.MEMBER_B_ID, inventory_seed.HOUSEHOLD_ID),
            ).fetchone()[0]
            is None
        )
        for change in changes:
            forged = deepcopy(original)
            forged.update(change)
            forged["id"] = str(uuid4())
            # A CheckViolation proves the source guard rejected the row rather than
            # relying on the existing assessment's unique key or a missing FK target.
            with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
                connection.execute(
                    "INSERT INTO policy_terms_applicability "
                    "SELECT * FROM jsonb_populate_record(NULL::policy_terms_applicability,%s)",
                    (Jsonb(forged),),
                )
    assert _assessments(database_url) == [original]
    assert _applies(database_url, sources["edition_id"])


@pytest.mark.parametrize("change", ["rejected", "deleted"])
def test_component_exclusion_immediately_invalidates_current_without_rewriting_history(
    database_url: str, change: str
) -> None:
    sources, original = _matched(database_url)
    with psycopg.connect(inventory_seed._psycopg_url(database_url)) as connection:
        if change == "rejected":
            connection.execute(
                "UPDATE insurance_document_components SET review_state='REJECTED',"
                "version=version+1 WHERE id=%s",
                (sources["terms_component_id"],),
            )
        else:
            connection.execute(
                "UPDATE insurance_document_components SET deleted_at=clock_timestamp(),"
                "version=version+1 WHERE id=%s",
                (sources["terms_component_id"],),
            )
    assert not _applies(database_url, sources["edition_id"])
    assert _assessments(database_url) == [original]
    terms_applicability_repository.TermsApplicabilityProjector(database_url).refresh_pending()
    assert not _applies(database_url, sources["edition_id"])
    assert _assessments(database_url) == [original]


def test_policy_identity_correction_invalidates_but_status_and_coverage_end_do_not(
    database_url: str,
) -> None:
    sources, original = _matched(database_url)
    scope = HouseholdScope(inventory_seed.HOUSEHOLD_ID)
    ledger = PolicyLedgerRepository(database_url)
    policy = ledger.get_policy(scope, inventory_seed.POLICY_ID)
    assert policy is not None
    changed = ledger.update_policy(
        scope,
        policy.id,
        expected_version=policy.version,
        status="cancelled",
        status_evidence=policy.source_evidence,
        coverage_end_date=date(2025, 6, 30),
        change_coverage_end_date=True,
    )
    assert changed.version == policy.version + 1
    assert _applies(database_url, sources["edition_id"])
    projector = terms_applicability_repository.TermsApplicabilityProjector(database_url)
    assert projector.refresh_pending() == 0
    assert _assessments(database_url) == [original]
    with psycopg.connect(inventory_seed._psycopg_url(database_url)) as connection:
        connection.execute(
            "UPDATE policy_contracts SET product_display='Sample User Correction',"
            "product_key='sample-user-correction',version=version+1 WHERE id=%s",
            (policy.id,),
        )
    assert not _applies(database_url, sources["edition_id"])
    assert projector.refresh_pending() == 1
    rows = _assert_retained(database_url, original)
    assert len(rows) == 2
    current = next(row for row in rows if row["id"] != original["id"])
    assert current["status"] == "UNKNOWN"
    assert "POLICY_SOURCE_CORRECTED" in current["reason_codes"]
    assert not _applies(database_url, sources["edition_id"])


def test_another_active_contract_on_the_same_source_page_blocks_unique_binding(
    database_url: str,
) -> None:
    sources, original = _matched(database_url)
    scope = HouseholdScope(inventory_seed.HOUSEHOLD_ID)
    ledger = PolicyLedgerRepository(database_url)
    policy = ledger.get_policy(scope, inventory_seed.POLICY_ID)
    assert policy is not None
    other = ledger.create_policy(
        scope,
        source_document_version_id=policy.source_document_version_id,
        source_evidence=policy.source_evidence,
        insurer_display=policy.insurer_display,
        insurer_key=policy.insurer_key,
        product_display="Sample Other Contract",
        product_key="sample-other-contract",
        contract_date=None,
        coverage_start_date=None,
        coverage_end_date=None,
        status="unknown",
        status_evidence=None,
        parties=(
            CreatePolicyParty(
                family_member_id=inventory_seed.MEMBER_A_ID,
                role="primary_insured",
                effective_from=None,
                effective_to=None,
                evidence=policy.source_evidence,
            ),
        ),
    )
    assert other.id != policy.id
    assert not _applies(database_url, sources["edition_id"])
    terms_applicability_repository.TermsApplicabilityProjector(database_url).refresh_pending()
    assert not _applies(database_url, sources["edition_id"])
    assert _assessments(database_url) == [original]


@pytest.mark.parametrize("history", ["USER_CONFIRMED", "REJECTED", "detached", "deleted_set"])
def test_manual_terms_history_prevents_automatic_selection_without_losing_the_decision(
    database_url: str, history: str
) -> None:
    sources, original = _matched(database_url)
    scope = HouseholdScope(inventory_seed.HOUSEHOLD_ID)
    inventory = InsuranceDocumentRepository(database_url)
    document_set = inventory.create_document_set(
        scope,
        actor_id=inventory_seed.USER_ID,
        member_id=inventory_seed.MEMBER_A_ID,
        policy_contract_id=inventory_seed.POLICY_ID,
        insurer_display=None,
        product_display=None,
        display_label="Sample Manual Terms Selection",
    )
    assert document_set is not None
    item = inventory.attach_set_item(
        scope,
        actor_id=inventory_seed.USER_ID,
        document_set_id=document_set.id,
        insurance_document_component_id=sources["terms_component_id"],
        match_state="REJECTED" if history == "REJECTED" else "USER_CONFIRMED",
        evidence_id=None,
        expected_set_version=document_set.version,
    )
    assert item is not None
    if history == "detached":
        assert inventory.detach_set_item(scope, item_id=item.id, expected_version=item.version)
    elif history == "deleted_set":
        assert inventory.soft_delete_document_set(
            scope, document_set_id=document_set.id, expected_version=document_set.version + 1
        )
    expected_history = _set_history(database_url, document_set.id)
    assert not _applies(database_url, sources["edition_id"])
    projector = terms_applicability_repository.TermsApplicabilityProjector(database_url)
    assert projector.refresh_pending() == 1
    rows = _assert_retained(database_url, original)
    assert len(rows) == 2
    current = next(row for row in rows if row["id"] != original["id"])
    if history == "USER_CONFIRMED":
        assert current["status"] == "MATCH"
        assert current["selection_state"] == "USER_SELECTED"
        assert _applies(database_url, sources["edition_id"])
    else:
        assert current["status"] == "UNKNOWN"
        assert current["selection_state"] == "USER_OWNED"
        assert "USER_DOCUMENT_DECISION_EXISTS" in current["reason_codes"]
        assert not _applies(database_url, sources["edition_id"])
    assert _set_history(database_url, document_set.id) == expected_history
    assert projector.refresh_pending() == 0
    assert _set_history(database_url, document_set.id) == expected_history


def test_storage_failure_rolls_back_assessment_and_refresh_marker_before_retry(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = applicability_seed._sources(database_url)
    projector_type = terms_applicability_repository.TermsApplicabilityProjector

    def fail_touch(connection: Any, policy: Any, digest: Any) -> None:
        del policy, digest
        assert (
            connection.execute("SELECT count(*) AS n FROM policy_terms_applicability").fetchone()[
                "n"
            ]
            == 1
        )
        raise psycopg.OperationalError("SYNTHETIC_STORAGE_FAILURE")

    with monkeypatch.context() as failure:
        failure.setattr(projector_type, "_touch", staticmethod(fail_touch))
        assert projector_type(database_url).refresh_pending() == 0
    assert _assessments(database_url) == []
    assert not _applies(database_url, sources["edition_id"])
    with psycopg.connect(inventory_seed._psycopg_url(database_url)) as connection:
        assert (
            connection.execute(
                "SELECT input_digest IS NULL AND retry_after>clock_timestamp() "
                "FROM policy_terms_refresh_checks"
            ).fetchone()[0]
            is True
        )
        connection.execute("UPDATE policy_terms_refresh_checks SET retry_after=NULL")
    assert projector_type(database_url).refresh_pending() == 1
    rows = _assessments(database_url)
    assert len(rows) == 1 and rows[0]["status"] == "MATCH"
    assert _applies(database_url, sources["edition_id"])
    assert projector_type(database_url).refresh_pending() == 0
    assert _assessments(database_url) == rows


def test_policy_evidence_outside_its_page_cannot_keep_applicability_current(
    database_url: str,
) -> None:
    sources, original = _matched(database_url)
    with psycopg.connect(inventory_seed._psycopg_url(database_url)) as connection:
        connection.execute(
            "UPDATE evidence SET x0=1,y0=1,x1=1000,y1=1000 WHERE id=%s",
            (inventory_seed.EVIDENCE_ID,),
        )
    assert not _applies(database_url, sources["edition_id"])
    assert _assessments(database_url) == [original]


def test_explicit_ledger_contract_date_disagreement_requires_new_source_resolution(
    database_url: str,
) -> None:
    sources, original = _matched(database_url)
    with psycopg.connect(inventory_seed._psycopg_url(database_url)) as connection:
        connection.execute(
            "UPDATE policy_contracts SET contract_date='2026-02-01',version=version+1 WHERE id=%s",
            (inventory_seed.POLICY_ID,),
        )
    assert not _applies(database_url, sources["edition_id"])
    assert (
        terms_applicability_repository.TermsApplicabilityProjector(database_url).refresh_pending()
        == 1
    )
    rows = _assert_retained(database_url, original)
    current = next(row for row in rows if row["id"] != original["id"])
    assert current["status"] == "UNKNOWN"
    assert "POLICY_SOURCE_CORRECTED" in current["reason_codes"]
    assert not _applies(database_url, sources["edition_id"])


def test_explicit_ledger_date_conflict_is_not_ignored_when_cover_omits_the_date(
    database_url: str,
) -> None:
    sources = applicability_seed._sources(database_url, references=True, printed_period=True)
    with psycopg.connect(inventory_seed._psycopg_url(database_url)) as connection:
        connection.execute(
            "UPDATE policy_contracts SET contract_date='2026-02-01' WHERE id=%s",
            (inventory_seed.POLICY_ID,),
        )
    assert (
        terms_applicability_repository.TermsApplicabilityProjector(database_url).refresh_pending()
        == 1
    )
    assessment = _assessments(database_url)[0]
    assert assessment["status"] == "UNKNOWN"
    assert "LEDGER_CONTRACT_DATE_CONFLICT" in assessment["reason_codes"]
    assert not _applies(database_url, sources["edition_id"])
