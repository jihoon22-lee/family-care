"""Actual receipt CRUD and RR snapshots remain scoped and source-version bound."""

from uuid import uuid4

import psycopg
import pytest
from familycare_api.decisions.calculation_repository import CalculationRepository
from familycare_api.decisions.calculation_schemas import (
    ReceiptLineCreateRequest,
    ReceiptLineUpdateRequest,
)
from familycare_api.decisions.calculation_service import CalculationService
from familycare_api.decisions.schemas import MedicalEventUpdateRequest
from familycare_api.guidance.expenses import read_expenses
from psycopg import IsolationLevel
from psycopg.rows import dict_row

from apps.api.tests.test_decision_integration import (
    _create_event,
    _psycopg_url,
    _reset_database,
    _service,
)
from apps.api.tests.test_decision_integration import database_url as database_url
from apps.api.tests.test_decision_integration import seed as seed

pytestmark = pytest.mark.integration


@pytest.fixture()
def receipts(request):
    url = request.getfixturevalue("database_url")
    seeded = request.getfixturevalue("seed")
    event = _create_event(_service(url, seeded.scope_a), seeded.member_a)
    service = CalculationService(seeded.scope_a, CalculationRepository(url))
    try:
        yield url, seeded, event, service
    finally:
        _reset_database(url)


def create(service, event_id, amount, *, coverage="covered", confirmation="user", currency="KRW"):
    return service.create_receipt_line(
        event_id,
        ReceiptLineCreateRequest(
            category="inpatient",
            coverage_category=coverage,
            amount=amount,
            currency=currency,
            confirmation_level=confirmation,
        ),
    )


def connect(url):
    connection = psycopg.connect(_psycopg_url(url), row_factory=dict_row, autocommit=True)
    connection.isolation_level = IsolationLevel.REPEATABLE_READ
    return connection


def test_actual_analysis_snapshots_costs_and_marks_receipt_edits_stale(receipts):
    url, seeded, event, service = receipts
    original_line = create(service, event.id, "50000")
    create(service, event.id, "20000", coverage="unknown")
    decisions = _service(url, seeded.scope_a)
    original = decisions.analyze_medical_event(event.id)
    guidance = original.local_guidance
    assert guidance is not None
    assert guidance.expenses.currencies[0].covered.known_cost == "50000"
    assert guidance.expenses.currencies[0].coverage_review.known_cost == "20000"
    assert decisions.get_decision_result(event.id, event.version).local_guidance_stale is False
    service.update_receipt_line(
        event.id,
        original_line.line_id,
        ReceiptLineUpdateRequest(expected_version=1, amount="60000"),
    )
    historical = decisions.get_decision_result(event.id, event.version)
    assert historical.local_guidance_stale is True
    assert historical.local_guidance == guidance
    fresh = decisions.analyze_medical_event(event.id).local_guidance
    assert fresh.expenses.currencies[0].covered.known_cost == "60000"
    assert fresh.versions.status_digest != guidance.versions.status_digest
    assert guidance.expenses.currencies[0].covered.known_cost == "50000"


def test_real_receipt_rows_preserve_partitions_and_multiple_currencies(receipts):
    url, seeded, event, service = receipts
    created = [
        create(service, event.id, "50000"),
        create(service, event.id, "20000", coverage="possible_excluded"),
        create(service, event.id, "5000", coverage="excluded"),
        create(service, event.id, "10000", confirmation="ai_structured"),
        create(service, event.id, "10", currency="USD"),
    ]
    with connect(url) as connection, connection.transaction():
        result = read_expenses(connection, seeded.scope_a, event.id, event.version)
    assert result.status == "AVAILABLE" and result.review_required
    krw, usd = result.currencies
    assert krw.currency == "KRW" and usd.currency == "USD"
    assert [
        krw.covered.total_cost,
        krw.coverage_review.total_cost,
        krw.excluded.total_cost,
        krw.unconfirmed.total_cost,
    ] == [50000, 20000, 5000, 10000]
    assert usd.covered.total_cost == 10
    assert {line.line_id: line.version for line in result.lines} == {
        line.line_id: 1 for line in created
    }
    assert {line.source_refs[0].source_id for line in result.lines} == {
        line.line_id for line in created
    }


def test_same_rr_keeps_original_receipt_version_while_new_reads_see_crud(receipts):
    url, seeded, event, service = receipts
    with connect(url) as connection, connection.transaction():
        empty = read_expenses(connection, seeded.scope_a, event.id, event.version)
    assert empty.status == "EMPTY"
    line = create(service, event.id, "0")
    with connect(url) as connection:
        with connection.transaction():
            original = read_expenses(connection, seeded.scope_a, event.id, event.version)
            assert original.currencies[0].covered.total_cost == 0
            updated = service.update_receipt_line(
                event.id,
                line.line_id,
                ReceiptLineUpdateRequest(expected_version=line.version, amount="50000"),
            )
            assert updated.version == 2
            assert read_expenses(connection, seeded.scope_a, event.id, event.version) == original
        with connection.transaction():
            fresh = read_expenses(connection, seeded.scope_a, event.id, event.version)
            assert fresh.lines[0].version == 2 and fresh.currencies[0].covered.total_cost == 50000
            assert fresh.digest_sha256 != original.digest_sha256
        service.delete_receipt_line(event.id, line.line_id, expected_version=2)
        with connection.transaction():
            deleted = read_expenses(connection, seeded.scope_a, event.id, event.version)
            assert deleted.status == "EMPTY" and deleted.digest_sha256 != fresh.digest_sha256
            stored = connection.execute(
                "SELECT version,deleted_at FROM receipt_lines WHERE id=%s", (line.line_id,)
            ).fetchone()
            assert stored["version"] == 3 and stored["deleted_at"] is not None
    assert original.lines[0].version == 1 and original.lines[0].amount == 0


def test_event_version_binding_never_reinterprets_old_history_as_current(receipts):
    url, seeded, event, service = receipts
    create(service, event.id, "50000")
    with connect(url) as connection, connection.transaction():
        original = read_expenses(connection, seeded.scope_a, event.id, event.version)
    updated = _service(url, seeded.scope_a).update_medical_event(
        event.id,
        MedicalEventUpdateRequest(
            expected_version=event.version,
            structured_facts=[{"field_id": "admission", "value": True}],
        ),
    )
    with connect(url) as connection, connection.transaction():
        history = connection.execute(
            "SELECT * FROM medical_event_fact_versions WHERE medical_event_id=%s ORDER BY version",
            (event.id,),
        ).fetchall()
        stale = read_expenses(connection, seeded.scope_a, event.id, event.version)
        current = read_expenses(connection, seeded.scope_a, event.id, updated.version)
        assert stale.status == "UNAVAILABLE" and stale.reason_codes == (
            "EXPENSE_EVENT_VERSION_MISMATCH",
        )
        assert (
            current.event_version == updated.version
            and current.digest_sha256 != original.digest_sha256
        )
        assert (
            history
            and connection.execute(
                "SELECT * FROM medical_event_fact_versions WHERE medical_event_id=%s "
                "ORDER BY version",
                (event.id,),
            ).fetchall()
            == history
        )
    assert original.event_version == event.version and original.lines[0].amount == 50000


def test_other_household_event_and_malformed_cross_scope_rows_never_leak(receipts):
    url, seeded, event, service = receipts
    create(service, event.id, "50000")
    other_event = _create_event(_service(url, seeded.scope_b), seeded.member_b)
    same_household_event = _create_event(_service(url, seeded.scope_a), seeded.other_member_a)
    with connect(url) as connection, connection.transaction():
        connection.execute(
            "INSERT INTO receipt_lines(id,household_space_id,medical_event_id,category,"
            "coverage_category,amount,currency,confirmation_level) "
            "VALUES(%s,%s,%s,'inpatient','covered',999,'KRW','user')",
            (uuid4(), seeded.scope_b.household_space_id, event.id),
        )
        assert (
            read_expenses(connection, seeded.scope_b, event.id, event.version).status
            == "UNAVAILABLE"
        )
        assert (
            read_expenses(connection, seeded.scope_a, other_event.id, other_event.version).status
            == "UNAVAILABLE"
        )
        assert (
            read_expenses(
                connection, seeded.scope_a, same_household_event.id, same_household_event.version
            ).status
            == "EMPTY"
        )
        own = read_expenses(connection, seeded.scope_a, event.id, event.version)
        assert own.currencies[0].covered.total_cost == 50000 and len(own.lines) == 1


def test_receipt_query_failure_rolls_back_savepoint_and_keeps_callers_rr_usable(receipts):
    url, seeded, event, service = receipts
    create(service, event.id, "50000")
    with connect(url) as connection, connection.transaction():
        original = read_expenses(connection, seeded.scope_a, event.id, event.version)

        class BrokenReceiptQuery:
            def transaction(self):
                return connection.transaction()

            def execute(self, query, params):
                return (
                    connection.execute("SELECT 1/0")
                    if "expenses-lines" in query
                    else connection.execute(query, params)
                )

        failed = read_expenses(BrokenReceiptQuery(), seeded.scope_a, event.id, event.version)
        assert failed.status == "UNAVAILABLE" and failed.reason_codes == (
            "EXPENSE_SOURCE_UNAVAILABLE",
        )
        assert (
            connection.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
            == "repeatable read"
        )
        assert read_expenses(connection, seeded.scope_a, event.id, event.version) == original
