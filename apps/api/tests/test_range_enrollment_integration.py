"""Synthetic range analysis reaches the ledger without blanket user confirmation."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from workers.analyzer.tests.test_policy_range_repository import (
    WORKER,
    PolicyRangeRepository,
    _one_contract,
    _psycopg_url,
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)

pytestmark = pytest.mark.integration


def _retain_contract(
    url: str,
    job: Any,
    *,
    rider: bool = False,
    second_rider: bool = False,
    separate_benefit_evidence: bool = False,
    unenrolled: bool = False,
    omit_benefit_type: bool = False,
    review_rider: bool = False,
    certificate_title: bool = False,
) -> None:
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE family_members SET display_name = 'Family Member A' WHERE id = %s",
            (job.family_member_id,),
        )
        connection.execute(
            "UPDATE extraction_blocks SET text = %s WHERE reading_order = 0 "
            "AND page_id IN (SELECT id FROM extraction_pages WHERE extraction_id=%s)",
            (
                "보험증권 가입금액\n증권번호: synthetic-policy-001\n"
                "피보험자: Family Member A\nSample Insurer Sample Plan"
                + ("_보험증권" if certificate_title else "")
                + "\nSample Rider fixed sum assured: 317 KRW"
                + (" | 미가입" if unenrolled else "")
                + "\n"
                "Another Rider fixed sum assured: 619 KRW",
                job.extraction_id,
            ),
        )
    if separate_benefit_evidence:
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute(
                "UPDATE extraction_blocks SET text='Sample Rider fixed' WHERE reading_order=1 "
                "AND page_id IN (SELECT id FROM extraction_pages WHERE extraction_id=%s)",
                (job.extraction_id,),
            )
    ranges = PolicyRangeRepository(url)
    work = ranges.next(job, WORKER, sensitive_terms=("Family Member A",))
    batch, result = _one_contract(work)
    for name, amount in (
        [("Sample Rider", 317), ("Another Rider", 619)]
        if second_rider
        else [("Sample Rider", 317)]
        if rider
        else []
    ):
        source = batch.candidates[0].model_copy(
            update={
                "candidate_id": uuid4(),
                "candidate_kind": "rider",
                "fields": tuple(
                    batch.candidates[0]
                    .fields[0]
                    .model_copy(
                        update={
                            "field_id": key,
                            "value": value,
                        }
                    )
                    for key, value in {
                        "rider_name": name,
                        "rider_key": name.casefold().replace(" ", "-"),
                        "benefit_type": "fixed",
                        "sum_assured": amount,
                        "currency": "KRW",
                    }.items()
                ),
            }
        )
        batch = batch.model_copy(
            update={
                "candidates": (*batch.candidates, source),
                "ranges": (
                    batch.ranges[0].model_copy(
                        update={
                            "candidate_ids": (*batch.ranges[0].candidate_ids, source.candidate_id),
                        }
                    ),
                    *batch.ranges[1:],
                ),
            }
        )
        result = result.model_copy(
            update={
                "candidates": (
                    *result.candidates,
                    result.candidates[0].model_copy(
                        update={
                            "candidate_id": source.candidate_id,
                            "candidate_kind": "rider",
                            "fields": source.fields,
                        }
                    ),
                ),
            }
        )
    if separate_benefit_evidence:

        def bind_benefit(candidate: Any) -> Any:
            return candidate.model_copy(
                update={
                    "fields": tuple(
                        field.model_copy(
                            update={"evidence_ids": (work.envelope.primary_evidence_ids[1],)}
                        )
                        if field.field_id == "benefit_type"
                        else field
                        for field in candidate.fields
                    )
                }
            )

        batch = batch.model_copy(
            update={"candidates": tuple(bind_benefit(c) for c in batch.candidates)}
        )
        result = result.model_copy(
            update={"candidates": tuple(bind_benefit(c) for c in result.candidates)}
        )
    if omit_benefit_type:

        def without_type(candidate: Any) -> Any:
            return candidate.model_copy(
                update={
                    "fields": tuple(
                        field for field in candidate.fields if field.field_id != "benefit_type"
                    )
                }
            )

        batch = batch.model_copy(
            update={"candidates": tuple(without_type(c) for c in batch.candidates)}
        )
        result = result.model_copy(
            update={"candidates": tuple(without_type(c) for c in result.candidates)}
        )
    if review_rider:
        result = result.model_copy(
            update={
                "candidates": tuple(
                    c.model_copy(
                        update={"status": "NEEDS_REVIEW", "issue_codes": ("LOW_CONFIDENCE",)}
                    )
                    if c.candidate_kind == "rider"
                    else c
                    for c in result.candidates
                ),
            }
        )
    ranges.save(job, WORKER, work, batch, result)


@pytest.fixture()
def enrollment_database(request: pytest.FixtureRequest) -> Any:
    url, job = request.getfixturevalue("ranges_database")
    try:
        yield url, job
    finally:
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute("TRUNCATE range_enrollment_publications")
            connection.execute(
                "DELETE FROM policy_contracts WHERE household_space_id=%s",
                (job.household_space_id,),
            )
            connection.execute("TRUNCATE document_structure_generations CASCADE")
            connection.execute(
                "DELETE FROM analysis_candidate_versions WHERE household_space_id=%s",
                (job.household_space_id,),
            )
            connection.execute(
                "DELETE FROM evidence WHERE household_space_id=%s", (job.household_space_id,)
            )


@pytest.mark.parametrize("stale_member", [False, True])
def test_verified_range_projects_once_with_local_insured_proof(
    enrollment_database: Any, stale_member: bool
) -> None:
    from familycare_api.policies.range_enrollment import RangeEnrollmentProjector

    url, job = enrollment_database
    _retain_contract(url, job)
    if stale_member:
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute(
                "UPDATE family_members SET version = version + 1 WHERE id = %s",
                (job.family_member_id,),
            )
    projector = RangeEnrollmentProjector(url)
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(lambda _: projector.project_pending(), range(2)))
    assert sum(counts) == (0 if stale_member else 1)
    assert projector.project_pending() == 0
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        rows = connection.execute(
            "SELECT p.id, p.status, party.family_member_id, e.extraction_id "
            "FROM policy_contracts p JOIN policy_parties party "
            "ON party.policy_contract_id=p.id "
            "JOIN evidence e ON e.id=party.evidence_id WHERE p.household_space_id=%s",
            (job.household_space_id,),
        ).fetchall()
        assert len(rows) == (0 if stale_member else 1)
        if rows:
            assert rows[0]["id"] != job.policy_aggregate_id
            assert rows[0]["status"] == "unknown"
            assert rows[0]["family_member_id"] == job.family_member_id
            assert rows[0]["extraction_id"] == job.extraction_id


@pytest.mark.parametrize("direct_edit", [False, True])
def test_rider_correction_keeps_identity_and_respects_direct_ledger_edits(
    enrollment_database: Any,
    direct_edit: bool,
) -> None:
    from familycare_api.common.scope import HouseholdScope
    from familycare_api.policies.candidate_errors import InvalidCandidateCorrection
    from familycare_api.policies.candidate_models import CandidateCorrectionRequest
    from familycare_api.policies.candidate_repository import CandidateRepository
    from familycare_api.policies.range_enrollment import RangeEnrollmentProjector

    url, job = enrollment_database
    _retain_contract(url, job, rider=True)
    projector = RangeEnrollmentProjector(url)
    assert projector.project_pending() == 2
    repository = CandidateRepository(url)
    scope = HouseholdScope(job.household_space_id)
    items = repository.list_review_items(scope, status="AI_VERIFIED")
    rider = next(item for item in items if item.candidate_kind == "rider")
    actor = uuid4()
    corrected = repository.correct_field(
        scope,
        request=CandidateCorrectionRequest(
            expected_version=rider.expected_version,
            field_id="sum_assured",
            value=619,
            evidence_id=rider.evidence[0].evidence_id,
        ),
        actor_id=actor,
        review_item_id=rider.review_item_id,
    )
    assert projector.project_pending() == 0
    with psycopg.connect(_psycopg_url(url)) as connection:
        original = connection.execute(
            "SELECT id,insured_amount FROM riders WHERE household_space_id=%s",
            (job.household_space_id,),
        ).fetchone()
        assert original[1] == 317
        if direct_edit:
            connection.execute(
                "UPDATE riders SET insured_amount=811,version=version+1 "
                "WHERE household_space_id=%s",
                (job.household_space_id,),
            )
    arguments = dict(
        expected_version=corrected.expected_version, status="USER_CONFIRMED", actor_id=actor
    )
    if direct_edit:
        with pytest.raises(InvalidCandidateCorrection):
            repository.transition(scope, rider.review_item_id, **arguments)
    else:
        repository.transition(scope, rider.review_item_id, **arguments)
    assert projector.project_pending() == 0
    with psycopg.connect(_psycopg_url(url)) as connection:
        rows = connection.execute(
            "SELECT id,insured_amount FROM riders WHERE household_space_id=%s",
            (job.household_space_id,),
        ).fetchall()
        assert rows == [(original[0], 811 if direct_edit else 619)]
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute(
                "UPDATE range_enrollment_publications SET authority='USER_CONFIRMED'"
            )


def test_failure_rolls_back_ledger_party_and_publication_together(
    enrollment_database: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from familycare_api.policies.candidate_repository import CandidateRepository
    from familycare_api.policies.range_enrollment import RangeEnrollmentProjector

    url, job = enrollment_database
    _retain_contract(url, job)
    projector = RangeEnrollmentProjector(url)
    original = CandidateRepository._publish_projection

    def fail_after_projection(*args: Any, **kwargs: Any) -> bool:
        assert original(*args, **kwargs)
        raise RuntimeError("synthetic transaction failure")

    with monkeypatch.context() as patch:
        patch.setattr(CandidateRepository, "_publish_projection", fail_after_projection)
        with pytest.raises(RuntimeError, match="synthetic transaction failure"):
            projector.project_pending()
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM range_enrollment_publications"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM policy_contracts WHERE household_space_id=%s",
            (job.household_space_id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM policy_parties WHERE household_space_id=%s",
            (job.household_space_id,),
        ).fetchone() == (0,)
    assert projector.project_pending() == 1


def test_deferred_candidate_does_not_starve_a_later_valid_range(enrollment_database: Any) -> None:
    from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
    from familycare_worker.policy_jobs import PolicyStructuringJobQueue

    url, job = enrollment_database
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE extraction_blocks SET text='Sample Insurer Sample Plan' "
            "WHERE page_id IN (SELECT id FROM extraction_pages WHERE extraction_id=%s)",
            (job.extraction_id,),
        )
    _retain_contract(url, job)
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE analysis_candidate_fields SET value='null' "
            "WHERE field_id='insurer' AND candidate_version_id IN "
            "(SELECT id FROM analysis_candidate_versions WHERE structuring_job_id=%s)",
            (job.id,),
        )
    next_job = PolicyStructuringJobQueue(url).claim_next_job(WORKER)
    assert next_job is not None and next_job.id == job.id
    ranges = PolicyRangeRepository(url)
    work = ranges.next(next_job, WORKER, sensitive_terms=("Family Member A",))
    batch, result = _one_contract(work)
    ranges.save(next_job, WORKER, work, batch, result)
    projector = RangeEnrollmentProjector(url)
    assert projector.project_pending(limit=1) == 0
    assert projector.project_pending(limit=1) == 1
    assert projector.project_pending(limit=1) == 0


def test_distinct_riders_in_one_block_are_readable_without_duplicate_identity(
    enrollment_database: Any,
) -> None:
    from familycare_api.common.scope import HouseholdScope
    from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
    from familycare_api.policies.repository import PolicyLedgerRepository

    url, job = enrollment_database
    _retain_contract(url, job, rider=True, second_rider=True)
    assert RangeEnrollmentProjector(url).project_pending() == 3
    repository = PolicyLedgerRepository(url)
    policies = repository.list_policies(HouseholdScope(job.household_space_id))
    assert len(policies) == 1
    riders = repository.list_policy_riders(HouseholdScope(job.household_space_id), policies[0].id)
    assert len({rider.id for rider in riders}) == 2
    assert {rider.insured_amount for rider in riders} == {317, 619}


def test_same_product_in_two_contracts_of_one_pdf_remains_separate(
    enrollment_database: Any,
) -> None:
    from familycare_api.common.scope import HouseholdScope
    from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
    from familycare_api.policies.repository import PolicyLedgerRepository

    url, job = enrollment_database
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE family_members SET display_name='Family Member A' WHERE id=%s",
            (job.family_member_id,),
        )
        connection.execute(
            "DELETE FROM extraction_blocks WHERE reading_order>0 "
            "AND page_id IN (SELECT id FROM extraction_pages WHERE extraction_id=%s)",
            (job.extraction_id,),
        )
        connection.execute(
            "UPDATE document_versions SET page_count=2 WHERE id=%s", (job.document_version_id,)
        )
        first_page = connection.execute(
            "SELECT id FROM extraction_pages WHERE extraction_id=%s", (job.extraction_id,)
        ).fetchone()[0]
        second_page = connection.execute(
            "INSERT INTO extraction_pages(extraction_id,page_number,width_points,height_points,"
            "non_whitespace_chars,alphanumeric_ratio,replacement_character_ratio,"
            "maximum_repeated_character_run,classification) "
            "SELECT extraction_id,2,width_points,height_points,non_whitespace_chars,"
            "alphanumeric_ratio,replacement_character_ratio,maximum_repeated_character_run,"
            "classification FROM extraction_pages WHERE id=%s RETURNING id",
            (first_page,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO extraction_blocks(page_id,text,bbox,reading_order) "
            "VALUES (%s,'Synthetic','[10,10,400,20]',0)",
            (second_page,),
        )
        for index, page in enumerate((first_page, second_page), start=1):
            connection.execute(
                "UPDATE extraction_blocks SET text=%s WHERE page_id=%s",
                (
                    f"보험증권 가입금액\n증권번호: synthetic-policy-00{index}\n"
                    "피보험자: Family Member A\nSample Insurer Sample Plan",
                    page,
                ),
            )
    ranges = PolicyRangeRepository(url)
    work = ranges.next(job, WORKER, sensitive_terms=("Family Member A",))
    assert len(work.envelope.primary_evidence_ids) == 2
    batch, result = _one_contract(work)
    other = batch.candidates[0].model_copy(
        update={
            "candidate_id": uuid4(),
            "fields": tuple(
                field.model_copy(update={"evidence_ids": (work.envelope.primary_evidence_ids[1],)})
                for field in batch.candidates[0].fields
            ),
        }
    )
    batch = batch.model_copy(
        update={
            "candidates": (*batch.candidates, other),
            "ranges": (
                batch.ranges[0],
                batch.ranges[1].model_copy(
                    update={
                        "outcome": "CANDIDATES",
                        "candidate_ids": (other.candidate_id,),
                    }
                ),
            ),
        }
    )
    result = result.model_copy(
        update={
            "candidates": (
                *result.candidates,
                result.candidates[0].model_copy(
                    update={"candidate_id": other.candidate_id, "fields": other.fields}
                ),
            )
        }
    )
    ranges.save(job, WORKER, work, batch, result)
    assert RangeEnrollmentProjector(url).project_pending() == 2
    policies = PolicyLedgerRepository(url).list_policies(HouseholdScope(job.household_space_id))
    assert len({policy.id for policy in policies}) == 2
    assert {policy.product_display for policy in policies} == {"Sample Plan"}


@pytest.mark.parametrize("before_first_publication", [False, True])
def test_name_correction_using_another_source_keeps_the_original_rider(
    enrollment_database: Any,
    before_first_publication: bool,
) -> None:
    from familycare_api.common.scope import HouseholdScope
    from familycare_api.policies.candidate_models import CandidateCorrectionRequest
    from familycare_api.policies.candidate_repository import CandidateRepository
    from familycare_api.policies.range_enrollment import RangeEnrollmentProjector

    url, job = enrollment_database
    _retain_contract(
        url, job, rider=True, separate_benefit_evidence=True, review_rider=before_first_publication
    )
    assert RangeEnrollmentProjector(url).project_pending() == (1 if before_first_publication else 2)
    repository = CandidateRepository(url)
    scope = HouseholdScope(job.household_space_id)
    item = next(
        item
        for item in repository.list_review_items(
            scope, status="NEEDS_REVIEW" if before_first_publication else "AI_VERIFIED"
        )
        if item.candidate_kind == "rider"
    )
    other_source = next(
        field.evidence_ids[0] for field in item.fields if field.field_id == "benefit_type"
    )
    actor = uuid4()
    corrected = repository.correct_field(
        scope,
        request=CandidateCorrectionRequest(
            expected_version=item.expected_version,
            field_id="rider_name",
            value="Corrected Sample Rider",
            evidence_id=other_source,
        ),
        actor_id=actor,
        review_item_id=item.review_item_id,
    )
    with psycopg.connect(_psycopg_url(url)) as connection:
        original = connection.execute(
            "SELECT id FROM riders WHERE household_space_id=%s", (job.household_space_id,)
        ).fetchone()
    repository.transition(
        scope,
        item.review_item_id,
        expected_version=corrected.expected_version,
        status="USER_CONFIRMED",
        actor_id=actor,
    )
    with psycopg.connect(_psycopg_url(url)) as connection:
        rows = connection.execute(
            "SELECT id,display_name FROM riders WHERE household_space_id=%s",
            (job.household_space_id,),
        ).fetchall()
        assert len(rows) == 1 and rows[0][1] == "Corrected Sample Rider"
        if original is not None:
            assert rows[0][0] == original[0]


def test_identical_header_replay_preserves_original_correction_ownership(
    enrollment_database: Any,
) -> None:
    from familycare_api.common.scope import HouseholdScope
    from familycare_api.policies.candidate_models import CandidateCorrectionRequest
    from familycare_api.policies.candidate_repository import CandidateRepository
    from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
    from familycare_worker.policy_jobs import PolicyStructuringJobQueue

    url, job = enrollment_database
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE extraction_blocks SET text='Sample Insurer Sample Plan' "
            "WHERE page_id IN (SELECT id FROM extraction_pages WHERE extraction_id=%s)",
            (job.extraction_id,),
        )
    _retain_contract(url, job)
    repository = CandidateRepository(url)
    scope = HouseholdScope(job.household_space_id)
    original = repository.list_review_items(scope, status="AI_VERIFIED")[0]
    projector = RangeEnrollmentProjector(url)
    assert projector.project_pending() == 1
    next_job = PolicyStructuringJobQueue(url).claim_next_job(WORKER)
    assert next_job is not None and next_job.id == job.id
    ranges = PolicyRangeRepository(url)
    work = ranges.next(next_job, WORKER, sensitive_terms=("Family Member A",))
    batch, result = _one_contract(work)
    ranges.save(next_job, WORKER, work, batch, result)
    assert projector.project_pending() == 1
    actor = uuid4()
    corrected = repository.correct_field(
        scope,
        request=CandidateCorrectionRequest(
            expected_version=original.expected_version,
            field_id="product_name",
            value="Corrected Sample Plan",
            evidence_id=original.evidence[0].evidence_id,
        ),
        actor_id=actor,
        review_item_id=original.review_item_id,
    )
    repository.transition(
        scope,
        original.review_item_id,
        expected_version=corrected.expected_version,
        status="USER_CONFIRMED",
        actor_id=actor,
    )
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT product_display FROM policy_contracts WHERE household_space_id=%s",
            (job.household_space_id,),
        ).fetchall() == [("Corrected Sample Plan",)]


@pytest.mark.parametrize("amount", [1e16, 0.001])
def test_unrepresentable_amount_is_deferred_without_blocking_other_riders(
    enrollment_database: Any,
    amount: float,
) -> None:
    from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
    from psycopg.types.json import Jsonb

    url, job = enrollment_database
    _retain_contract(url, job, rider=True, second_rider=True)
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE analysis_candidate_fields f SET value=%s "
            "WHERE f.field_id='sum_assured' AND f.candidate_version_id IN "
            "(SELECT f2.candidate_version_id FROM analysis_candidate_fields f2 "
            "JOIN analysis_candidate_versions c ON c.id=f2.candidate_version_id "
            "WHERE f2.field_id='rider_name' AND f2.value='\"Sample Rider\"' "
            "AND c.structuring_job_id=%s)",
            (Jsonb(amount), job.id),
        )
    assert RangeEnrollmentProjector(url).project_pending() == 2
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT insured_amount FROM riders WHERE household_space_id=%s",
            (job.household_space_id,),
        ).fetchall() == [(619,)]
        assert connection.execute(
            "SELECT count(*) FROM range_enrollment_attempts WHERE outcome='DEFERRED'"
        ).fetchone() == (1,)


def test_candidate_database_failure_isolated_from_later_publication(
    enrollment_database: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from familycare_api.policies.candidate_repository import CandidateRepository
    from familycare_api.policies.range_enrollment import RangeEnrollmentProjector

    url, job = enrollment_database
    _retain_contract(url, job, rider=True, second_rider=True)
    with psycopg.connect(_psycopg_url(url)) as connection:
        failed_id = connection.execute(
            "SELECT f.candidate_version_id FROM analysis_candidate_fields f "
            "JOIN analysis_candidate_versions c ON c.id=f.candidate_version_id "
            "WHERE f.field_id='rider_name' AND f.value='\"Sample Rider\"' "
            "AND c.structuring_job_id=%s",
            (job.id,),
        ).fetchone()[0]
    original = CandidateRepository._publish_projection

    def fail_one(self: Any, connection: Any, household: Any, candidate_id: Any) -> bool:
        applied = original(self, connection, household, candidate_id)
        if candidate_id == failed_id:
            raise psycopg.DataError("synthetic rejected ledger value")
        return applied

    with monkeypatch.context() as patch:
        patch.setattr(CandidateRepository, "_publish_projection", fail_one)
        assert RangeEnrollmentProjector(url).project_pending() == 2
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM range_enrollment_publications WHERE candidate_version_id=%s",
            (failed_id,),
        ).fetchone() == (0,)
    assert RangeEnrollmentProjector(url).project_pending() == 1


@pytest.mark.parametrize("review_rider", [False, True])
def test_generic_confirmation_cannot_turn_unenrolled_source_into_a_rider(
    enrollment_database: Any,
    review_rider: bool,
) -> None:
    from familycare_api.common.scope import HouseholdScope
    from familycare_api.policies.candidate_errors import InvalidCandidateCorrection
    from familycare_api.policies.candidate_repository import CandidateRepository
    from familycare_api.policies.range_enrollment import RangeEnrollmentProjector

    url, job = enrollment_database
    _retain_contract(url, job, rider=True, unenrolled=True, review_rider=review_rider)
    assert RangeEnrollmentProjector(url).project_pending() == 1
    repository = CandidateRepository(url)
    scope = HouseholdScope(job.household_space_id)
    rider = next(
        item for item in repository.list_review_items(scope) if item.candidate_kind == "rider"
    )
    with pytest.raises(InvalidCandidateCorrection):
        repository.transition(
            scope,
            rider.review_item_id,
            expected_version=rider.expected_version,
            status="USER_CONFIRMED",
            actor_id=uuid4(),
        )
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM riders WHERE household_space_id=%s", (job.household_space_id,)
        ).fetchone() == (0,)


def test_enrollment_without_classification_retains_its_document_amount(
    enrollment_database: Any,
) -> None:
    from familycare_api.common.scope import HouseholdScope
    from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
    from familycare_api.policies.repository import PolicyLedgerRepository

    url, job = enrollment_database
    _retain_contract(url, job, rider=True, omit_benefit_type=True)
    assert RangeEnrollmentProjector(url).project_pending() == 2
    repository = PolicyLedgerRepository(url)
    scope = HouseholdScope(job.household_space_id)
    policy = repository.list_policies(scope)[0]
    rider = repository.list_policy_riders(scope, policy.id)[0]
    assert rider.benefit_type == "unknown" and rider.insured_amount == 317
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy.exc import DBAPIError

    config = Config(str(Path(__file__).resolve().parents[3] / "apps/api/alembic.ini"))
    with pytest.raises(DBAPIError, match="unclassified enrollment and candidate history"):
        command.downgrade(config, "0031_range_enrollment")
    assert repository.list_policy_riders(scope, policy.id)[0] == rider


def test_continued_table_amounts_reach_the_ledger_with_header_provenance(
    enrollment_database: Any,
) -> None:
    from familycare_api.common.scope import HouseholdScope
    from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
    from familycare_api.policies.repository import PolicyLedgerRepository
    from familycare_worker.ai.schemas import CandidateField
    from psycopg.types.json import Jsonb

    url, job = enrollment_database
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE family_members SET display_name='Family Member A' WHERE id=%s",
            (job.family_member_id,),
        )
        connection.execute(
            "DELETE FROM extraction_blocks WHERE page_id IN "
            "(SELECT id FROM extraction_pages WHERE extraction_id=%s)",
            (job.extraction_id,),
        )
        connection.execute(
            "UPDATE document_versions SET page_count=2 WHERE id=%s", (job.document_version_id,)
        )
        page1 = connection.execute(
            "SELECT id FROM extraction_pages WHERE extraction_id=%s", (job.extraction_id,)
        ).fetchone()[0]
        page2 = connection.execute(
            "INSERT INTO extraction_pages(extraction_id,page_number,width_points,height_points,"
            "non_whitespace_chars,alphanumeric_ratio,replacement_character_ratio,"
            "maximum_repeated_character_run,classification) "
            "SELECT extraction_id,2,width_points,height_points,non_whitespace_chars,"
            "alphanumeric_ratio,"
            "replacement_character_ratio,maximum_repeated_character_run,classification "
            "FROM extraction_pages WHERE id=%s RETURNING id",
            (page1,),
        ).fetchone()[0]
        for number, page in enumerate((page1, page2), start=1):
            text = (
                "보험증권 가입금액\n증권번호: synthetic-policy-001\n"
                "피보험자: Family Member A\nSample Insurer Sample Plan"
                if number == 1
                else "증권번호: synthetic-policy-001\n계속"
            )
            connection.execute(
                "INSERT INTO extraction_blocks(page_id,text,bbox,reading_order) "
                "VALUES (%s,%s,'[10,60,400,80]',0)",
                (page, text),
            )
            metadata = {"header_rows": [0]}
            if number == 2:
                metadata["continuation_of"] = {"page_number": 1, "table_index": 0}
            table = connection.execute(
                "INSERT INTO extraction_tables(page_id,bbox,metadata_json) "
                "VALUES (%s,'[10,100,310,140]',%s) RETURNING id",
                (page, Jsonb(metadata)),
            ).fetchone()[0]
            rows = [
                ["담보명", "가입금액(만원)", "보장구분"],
                [
                    "Sample Rider" if number == 1 else "Another Rider",
                    "20" if number == 1 else "30",
                    "정액",
                ],
            ]
            for row_index, cells in enumerate(rows):
                for column, value in enumerate(cells):
                    connection.execute(
                        "INSERT INTO extraction_cells(table_id,row_index,column_index,text,bbox) "
                        "VALUES (%s,%s,%s,%s,%s)",
                        (
                            table,
                            row_index,
                            column,
                            value,
                            Jsonb(
                                [
                                    10 + column * 100,
                                    100 + row_index * 20,
                                    110 + column * 100,
                                    120 + row_index * 20,
                                ]
                            ),
                        ),
                    )
    ranges = PolicyRangeRepository(url)
    work = ranges.next(job, WORKER, sensitive_terms=("Family Member A",))
    batch, result = _one_contract(work)
    candidates = list(batch.candidates)
    verified = list(result.candidates)
    dispositions = list(batch.ranges)
    for name, amount in (("Sample Rider", 200000), ("Another Rider", 300000)):
        row = next(item for item in work.envelope.evidence if item.primary and name in item.text)
        assert row.source_role == "policy"
        candidate = candidates[0].model_copy(
            update={
                "candidate_id": uuid4(),
                "candidate_kind": "rider",
                "fields": tuple(
                    CandidateField(field_id=key, value=value, evidence_ids=(row.evidence_id,))
                    for key, value in {
                        "rider_name": name,
                        "rider_key": name.lower().replace(" ", "-"),
                        "benefit_type": "fixed",
                        "sum_assured": amount,
                        "currency": "KRW",
                    }.items()
                ),
            }
        )
        candidates.append(candidate)
        verified.append(
            result.candidates[0].model_copy(
                update={
                    "candidate_id": candidate.candidate_id,
                    "candidate_kind": "rider",
                    "fields": candidate.fields,
                }
            )
        )
        index = work.envelope.primary_evidence_ids.index(row.evidence_id)
        dispositions[index] = dispositions[index].model_copy(
            update={
                "outcome": "CANDIDATES",
                "candidate_ids": (candidate.candidate_id,),
            }
        )
    ranges.save(
        job,
        WORKER,
        work,
        batch.model_copy(update={"candidates": tuple(candidates), "ranges": tuple(dispositions)}),
        result.model_copy(update={"candidates": tuple(verified)}),
    )
    assert RangeEnrollmentProjector(url).project_pending() == 3
    scope = HouseholdScope(job.household_space_id)
    repository = PolicyLedgerRepository(url)
    policies = repository.list_policies(scope)
    assert len(policies) == 1
    assert {r.insured_amount for r in repository.list_policy_riders(scope, policies[0].id)} == {
        200000,
        300000,
    }
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert (
            connection.execute(
                "SELECT min(refs) FROM (SELECT count(*) refs FROM analysis_candidate_evidence "
                "WHERE field_id='sum_assured' AND candidate_version_id IN "
                "(SELECT id FROM analysis_candidate_versions WHERE structuring_job_id=%s) "
                "GROUP BY candidate_version_id) counts",
                (job.id,),
            ).fetchone()[0]
            >= 2
        )
