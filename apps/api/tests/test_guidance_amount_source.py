"""Amount authority follows field proof, current ledger values and actual approvers."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.guidance.amount_source import read_operational_amount_source

from apps.api.tests.test_range_enrollment_integration import (
    enrollment_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)


def uid(number):
    return UUID(int=number, version=4)


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class SourceDatabase:
    def __init__(self):
        self.scope = HouseholdScope(uid(1))
        self.rider = {
            "id": uid(2),
            "household_space_id": uid(1),
            "policy_contract_id": uid(3),
            "version": 1,
            "insured_amount": Decimal("317.00"),
            "currency": "KRW",
            "display_name": "Sample Rider",
            "source_evidence_id": uid(9),
        }
        text = "Sample Rider fixed sum assured: 317 KRW"
        citation = {
            "evidence_id": str(uid(9)),
            "document_version_id": str(uid(6)),
            "extraction_id": str(uid(7)),
            "node_id": "synthetic-amount-node",
            "page": 1,
            "start": 0,
            "end": len(text),
            "primary": True,
            "source_role": "policy",
        }
        values = {"rider_name": "Sample Rider", "sum_assured": 317, "currency": "KRW"}
        self.publications = [
            {
                "candidate_version_id": uid(4),
                "source_candidate_version_id": uid(4),
                "household_space_id": uid(1),
                "policy_contract_id": uid(3),
                "rider_id": uid(2),
                "ledger_version": 1,
                "field_values": values,
                "authority": "PROGRAM_VERIFIED",
                "candidate_status": "AI_VERIFIED",
                "candidate_kind": "rider",
                "candidate_household": uid(1),
                "aggregate_id": uid(3),
                "published_at": datetime(2026, 1, 1, tzinfo=UTC),
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                "candidate_deleted": None,
                "actor_id": None,
                "actor_household": None,
                "parent_version_id": None,
                "lineage_valid": True,
                "source_kind": "rider",
                "source_household": uid(1),
                "source_review_item_id": uid(12),
                "review_item_id": uid(12),
                "provider_candidate_id": uid(5),
                "source_refs": [citation],
                "association_json": {"state": "RESOLVED", "family_member_id": str(uid(10))},
                "family_member_id": uid(10),
                "subject_present": True,
                "document_version_id": uid(6),
                "extraction_id": uid(7),
                "generation_id": uid(8),
                "content_sha256": "a" * 64,
                "identity_sha256": "b" * 64,
                "job_household": uid(1),
                "generation_household": uid(1),
                "generation_document": uid(6),
                "generation_extraction": uid(7),
                "source_deleted": None,
                "document_kind": "policy",
                "extraction_status": "succeeded",
                "cancelled": False,
                "range_state": "COMPLETE",
                "envelope_json": {
                    "evidence": [{**citation, "bbox": [0.0, 0.0, 500.0, 20.0], "text": text}]
                },
                "result_json": {
                    "program_validation_version": "range-grounding-v2",
                    "result": {
                        "candidates": [
                            {
                                "candidate_id": str(uid(5)),
                                "candidate_kind": "rider",
                                "status": "AI_VERIFIED",
                                "fields": [
                                    {"field_id": key, "value": value, "evidence_ids": [str(uid(9))]}
                                    for key, value in values.items()
                                ],
                            }
                        ]
                    },
                },
            }
        ]
        self.fields = [
            {"candidate_version_id": uid(4), "field_id": key, "value": value}
            for key, value in values.items()
        ]
        self.evidence = [
            {
                "candidate_version_id": uid(4),
                "field_id": key,
                "evidence_id": uid(9),
                "document_version_id": uid(6),
                "physical_page": 1,
                "evidence_document": uid(6),
                "extraction_id": uid(7),
                "household_space_id": uid(1),
                "content_sha256": "a" * 64,
                "document_hash": "a" * 64,
                "extraction_document": uid(6),
                "extraction_status": "succeeded",
                "document_kind": "policy",
                "deleted_at": None,
                "page_count": 1,
                "width_points": Decimal(600),
                "height_points": Decimal(800),
                "evidence_page": 1,
                "review_state": "NEEDS_REVIEW",
                "x0": Decimal(0),
                "y0": Decimal(0),
                "x1": Decimal(500),
                "y1": Decimal(20),
            }
            for key in values
        ]
        self.projection = {
            "lineage": {
                "document_version_id": str(uid(6)),
                "extraction_id": str(uid(7)),
                "content_sha256": "a" * 64,
            },
            "nodes": [
                {
                    "node_id": "synthetic-amount-node",
                    "page_number": 1,
                    "kind": "TEXT_LINE",
                    "text": text,
                    "bbox": [0.0, 0.0, 500.0, 20.0],
                    "source_layer": "native",
                }
            ],
        }

    def execute(self, query, params=()):
        if "amount-ledger" in query:
            return Rows([self.rider] if params == (uid(2), uid(1)) else [])
        if "amount-publications" in query:
            return Rows(self.publications)
        if "amount-fields" in query:
            return Rows([r for r in self.fields if r["candidate_version_id"] in params[1]])
        if "amount-evidence" in query:
            return Rows([r for r in self.evidence if r["candidate_version_id"] in params[1]])
        if "document_structure_projection" in query:
            return Rows([{"structure": self.projection}])
        raise AssertionError("unexpected synthetic query")

    def read(self):
        return read_operational_amount_source(self, self.scope, self.rider["id"])


@pytest.mark.parametrize("revision", ["range-grounding-v2", "range-grounding-v3"])
def test_program_amount_and_currency_replay_original_field_meaning(revision):
    database = SourceDatabase()
    database.publications[0]["result_json"]["program_validation_version"] = revision
    result = database.read()
    assert result.amount == 317 and result.currency == "KRW"
    assert result.amount_decision == result.currency_decision == "MATCH"
    assert result.amount_authority == result.currency_authority == "PROGRAM_VERIFIED"
    assert result.amount_evidence[0].evidence_id == uid(9)
    assert result.amount_evidence[0].review_state == "NEEDS_REVIEW"
    assert "Sample Rider" not in repr(result)
    assert result.digest_sha256 == database.read().digest_sha256


def test_general_ledger_evidence_is_not_direct_amount_authority():
    database = SourceDatabase()
    database.publications = []
    result = database.read()
    assert result.amount_decision == result.currency_decision == "UNKNOWN"
    assert result.amount is None and result.currency is None


@pytest.mark.parametrize("fault", ["amount", "currency"])
@pytest.mark.parametrize("revision", ["range-grounding-v2", "range-grounding-v3"])
def test_forged_retained_program_values_do_not_replace_original_proof(fault, revision):
    database = SourceDatabase()
    database.publications[0]["result_json"]["program_validation_version"] = revision
    key, value = ("sum_assured", 999) if fault == "amount" else ("currency", "USD")
    database.rider["insured_amount" if fault == "amount" else "currency"] = (
        Decimal(999) if fault == "amount" else value
    )
    database.publications[0]["field_values"][key] = value
    next(row for row in database.fields if row["field_id"] == key)["value"] = value
    next(
        row
        for row in database.publications[0]["result_json"]["result"]["candidates"][0]["fields"]
        if row["field_id"] == key
    )["value"] = value
    result = database.read()
    assert (result.amount_decision if fault == "amount" else result.currency_decision) == "UNKNOWN"
    assert (result.currency_decision if fault == "amount" else result.amount_decision) == "MATCH"


@pytest.mark.parametrize(
    "fault",
    [
        "ledger_version",
        "publication_scope",
        "source_scope",
        "cancelled",
        "source_address",
        "unit_missing",
        "neighbor",
        "result_revision",
        "field_evidence",
        "actor",
    ],
)
def test_missing_or_wrong_proof_never_grants_amount_authority(fault):
    database = SourceDatabase()
    publication = database.publications[0]
    if fault == "ledger_version":
        database.rider["version"] = 2
    elif fault == "publication_scope":
        publication["rider_id"] = uid(99)
    elif fault == "source_scope":
        publication["job_household"] = uid(99)
    elif fault == "cancelled":
        publication["cancelled"] = True
    elif fault == "source_address":
        publication["source_refs"][0]["end"] -= 1
    elif fault == "result_revision":
        publication["result_json"]["program_validation_version"] = "synthetic-unverified"
    elif fault == "field_evidence":
        database.evidence = [r for r in database.evidence if r["field_id"] != "sum_assured"]
    elif fault == "actor":
        publication.update(
            authority="USER_CONFIRMED",
            candidate_status="USER_CONFIRMED",
            actor_id=uid(98),
            actor_household=None,
            parent_version_id=uid(4),
        )
    else:
        text = (
            "Sample Rider\nAnother Rider sum assured: 317 KRW"
            if fault == "neighbor"
            else "Sample Rider sum assured: 317"
        )
        database.projection["nodes"][0]["text"] = text
        publication["source_refs"][0]["end"] = len(text)
        publication["envelope_json"]["evidence"][0]["end"] = len(text)
    assert database.read().amount_decision == "UNKNOWN"


def test_approved_manual_correction_uses_actual_actor_and_preserves_original_proof():
    database = SourceDatabase()
    pub = database.publications[0]
    pub.update(
        candidate_version_id=uid(15),
        authority="USER_CONFIRMED",
        candidate_status="USER_CONFIRMED",
        actor_id=uid(11),
        actor_household=uid(1),
        parent_version_id=uid(14),
        ledger_version=2,
    )
    pub["field_values"] = {**pub["field_values"], "sum_assured": 619}
    database.rider.update(insured_amount=Decimal(619), version=2)
    database.fields += [
        {
            **r,
            "candidate_version_id": uid(15),
            "value": 619 if r["field_id"] == "sum_assured" else r["value"],
        }
        for r in database.fields
    ]
    database.evidence += [
        {**r, "candidate_version_id": uid(15), "review_state": "USER_CONFIRMED"}
        for r in database.evidence
    ]
    result = database.read()
    assert result.amount == 619 and result.amount_authority == "USER_CONFIRMED"
    assert result.currency == "KRW"
    pub["actor_household"] = uid(99)
    assert database.read().amount_decision == "UNKNOWN"


def test_unpublished_draft_does_not_invalidate_selected_ledger_proof():
    database = SourceDatabase()
    first = database.read()
    database.fields.append(
        {"candidate_version_id": uid(77), "field_id": "sum_assured", "value": 999}
    )
    assert database.read() == first


def test_digest_changes_with_ledger_or_proof_inputs_and_is_order_independent():
    database = SourceDatabase()
    first = database.read().digest_sha256
    database.fields.reverse()
    database.evidence.reverse()
    assert database.read().digest_sha256 == first
    database.rider["insured_amount"] = Decimal(400)
    assert database.read().digest_sha256 != first


def test_foreign_rider_scope_returns_unknown_without_source_access():
    database = SourceDatabase()
    result = read_operational_amount_source(database, HouseholdScope(uid(99)), uid(2))
    assert result.amount_decision == result.currency_decision == "UNKNOWN"


@pytest.mark.integration
def test_published_amount_survives_draft_and_uses_real_approval(request):
    import psycopg
    from familycare_api.policies.candidate_models import CandidateCorrectionRequest
    from familycare_api.policies.candidate_repository import CandidateRepository
    from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
    from psycopg.rows import dict_row

    from apps.api.tests.test_range_enrollment_integration import _psycopg_url, _retain_contract

    url, job = request.getfixturevalue("enrollment_database")
    _retain_contract(url, job, rider=True)
    assert RangeEnrollmentProjector(url).project_pending() == 2
    scope = HouseholdScope(job.household_space_id)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        rider_id = connection.execute(
            "SELECT id FROM riders WHERE household_space_id=%s", (job.household_space_id,)
        ).fetchone()["id"]
        first = read_operational_amount_source(connection, scope, rider_id)
        assert first.amount == 317 and first.currency == "KRW"
        assert first.amount_authority == "PROGRAM_VERIFIED"
        actor = connection.execute(
            "INSERT INTO app_users(household_space_id,username,display_name,password_hash) "
            "VALUES (%s,'synthetic-amount-reviewer','Admin A','$argon2id$synthetic') RETURNING id",
            (job.household_space_id,),
        ).fetchone()["id"]
    try:
        repository = CandidateRepository(url)
        candidate = next(
            c
            for c in repository.list_review_items(scope, status="AI_VERIFIED")
            if c.candidate_kind == "rider"
        )
        corrected = repository.correct_field(
            scope,
            request=CandidateCorrectionRequest(
                expected_version=candidate.expected_version,
                field_id="sum_assured",
                value=619,
                evidence_id=candidate.evidence[0].evidence_id,
            ),
            actor_id=actor,
            review_item_id=candidate.review_item_id,
        )
        with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
            draft = read_operational_amount_source(connection, scope, rider_id)
            assert draft.amount == 317 and draft.amount_authority == "PROGRAM_VERIFIED"
        repository.transition(
            scope,
            candidate.review_item_id,
            expected_version=corrected.expected_version,
            status="USER_CONFIRMED",
            actor_id=actor,
        )
        with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
            approved = read_operational_amount_source(connection, scope, rider_id)
            assert approved.amount == 619 and approved.currency == "KRW"
            assert approved.amount_authority == "USER_CONFIRMED"
            assert approved.digest_sha256 != first.digest_sha256
    finally:
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute("DELETE FROM app_users WHERE id=%s", (actor,))


@pytest.mark.integration
def test_continued_table_field_proof_replays_original_header(request):
    import psycopg
    from psycopg.rows import dict_row

    from apps.api.tests.test_range_enrollment_integration import (
        _psycopg_url,
    )
    from apps.api.tests.test_range_enrollment_integration import (
        test_continued_table_amounts_reach_the_ledger_with_header_provenance as seed_table,
    )

    seed_table(request.getfixturevalue("enrollment_database"))
    url, job = request.getfixturevalue("enrollment_database")
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        riders = connection.execute(
            "SELECT id FROM riders WHERE household_space_id=%s", (job.household_space_id,)
        ).fetchall()
        sources = [
            read_operational_amount_source(
                connection, HouseholdScope(job.household_space_id), rider["id"]
            )
            for rider in riders
        ]
        assert {source.amount for source in sources} == {200000, 300000}
        assert all(
            source.currency == "KRW"
            and source.amount_authority == "PROGRAM_VERIFIED"
            and len(source.amount_evidence) >= 2
            for source in sources
        )


@pytest.mark.parametrize("field", ["sum_assured", "currency"])
def test_malformed_field_citation_keeps_other_independent_field(field):
    database = SourceDatabase()
    row = next(r for r in database.evidence if r["field_id"] == field)
    del row["extraction_id"]
    result = database.read()
    assert result.amount_decision == ("UNKNOWN" if field == "sum_assured" else "MATCH")
    assert result.currency_decision == ("UNKNOWN" if field == "currency" else "MATCH")
