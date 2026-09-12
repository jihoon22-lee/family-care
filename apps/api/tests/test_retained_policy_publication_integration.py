"""Historical v5 publication preserves source generations and user-owned enrollment."""

from uuid import uuid4

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.policies.candidate_models import CandidateCorrectionRequest
from familycare_api.policies.candidate_repository import CandidateRepository
from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
from familycare_api.policies.repository import PolicyLedgerRepository

from apps.api.tests.test_native_range_enrollment_integration import (
    WORKER,
    _psycopg_url,
    _retain_native,
    _store_words,
    _words,
    enrollment_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_native_range_enrollment_integration import (
    native_database as native_database,
)

pytestmark = pytest.mark.integration
V5 = "retained-policy-association-v5"


@pytest.fixture(autouse=True)
def historical_v5_producer(monkeypatch):
    from familycare_worker import retained_policy

    monkeypatch.setattr(retained_policy, "RETAINED_POLICY_PIPELINE_REVISION", V5)


def _initial(sample):
    url, job = sample
    _store_words(
        url,
        job,
        _words(
            [
                "Policy certificate",
                "Policy number: synthetic-policy-001",
                "Insured: Family Member A",
                "Sample Insurer Sample Plan",
                "Sample Rider sum assured: 317 KRW",
            ]
        ),
    )
    work = _retain_native(url, job)
    assert RangeEnrollmentProjector(url).project_pending() == 2
    return work


def _reprocess(sample, generation):
    from familycare_worker.retained_policy import RetainedPolicyJobQueue, RetainedPolicyRepository

    url, original = sample
    new = RetainedPolicyRepository(url).enqueue(
        household_space_id=original.household_space_id,
        source_job_id=original.id,
        expected_generation_id=generation,
        pipeline_revision=V5,
    )
    job = RetainedPolicyJobQueue(
        url, household_space_id=original.household_space_id, job_id=new.id, pipeline_revision=V5
    ).claim_next_job(WORKER)
    assert job is not None
    work = _retain_native(url, job)
    assert work.generation_id == generation
    return job


@pytest.mark.parametrize("stale", [False, True])
def test_new_publication_requires_pinned_current_generation(native_database, stale):
    url, original = native_database
    first = _initial(native_database)
    job = _reprocess(native_database, first.generation_id)
    if stale:
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute(
                "UPDATE document_structure_generations SET is_current=false WHERE id=%s",
                (first.generation_id,),
            )
    assert RangeEnrollmentProjector(url).project_pending() == (0 if stale else 2)
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM range_enrollment_publications p "
            "JOIN policy_range_candidate_sources s "
            "ON s.candidate_version_id=p.source_candidate_version_id "
            "WHERE s.job_id=%s",
            (job.id,),
        ).fetchone() == (0 if stale else 2,)
        assert connection.execute(
            "SELECT count(*) FROM policy_contracts WHERE household_space_id=%s",
            (original.household_space_id,),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM riders WHERE household_space_id=%s",
            (original.household_space_id,),
        ).fetchone() == (1,)


@pytest.mark.parametrize("change", ["direct_edit", "user_correction"])
def test_retained_resubmission_preserves_existing_correction_and_ledger(native_database, change):
    url, original = native_database
    first = _initial(native_database)
    scope = HouseholdScope(original.household_space_id)
    ledger = PolicyLedgerRepository(url)
    policy = ledger.list_policies(scope)[0]
    rider = ledger.list_policy_riders(scope, policy.id)[0]
    if change == "direct_edit":
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute(
                "UPDATE riders SET insured_amount=619,version=version+1 WHERE id=%s", (rider.id,)
            )
    else:
        repository = CandidateRepository(url)
        item = next(
            item
            for item in repository.list_review_items(scope, status="AI_VERIFIED")
            if item.candidate_kind == "rider"
        )
        name = next(field for field in item.fields if field.field_id == "rider_name")
        actor = uuid4()
        corrected = repository.correct_field(
            scope,
            request=CandidateCorrectionRequest(
                expected_version=item.expected_version,
                field_id="rider_name",
                value="Corrected Sample Rider",
                evidence_id=name.evidence_ids[0],
            ),
            actor_id=actor,
            review_item_id=item.review_item_id,
        )
        repository.transition(
            scope,
            item.review_item_id,
            expected_version=corrected.expected_version,
            status="USER_CONFIRMED",
            actor_id=actor,
        )
    expected = ledger.list_policy_riders(scope, policy.id)
    with psycopg.connect(_psycopg_url(url)) as connection:
        retained = connection.execute(
            "SELECT to_jsonb(c) FROM analysis_candidate_versions c WHERE structuring_job_id=%s "
            "OR review_item_id IN (SELECT review_item_id FROM analysis_candidate_versions "
            "WHERE structuring_job_id=%s) ORDER BY id",
            (original.id, original.id),
        ).fetchall()
    _reprocess(native_database, first.generation_id)
    assert RangeEnrollmentProjector(url).project_pending() == 1
    assert ledger.list_policy_riders(scope, policy.id) == expected
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert (
            connection.execute(
                "SELECT to_jsonb(c) FROM analysis_candidate_versions c WHERE structuring_job_id=%s "
                "OR review_item_id IN (SELECT review_item_id FROM analysis_candidate_versions "
                "WHERE structuring_job_id=%s) ORDER BY id",
                (original.id, original.id),
            ).fetchall()
            == retained
        )
