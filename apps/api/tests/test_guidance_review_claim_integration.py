"""Only persisted, source-bound reviewed candidates can become a preparation snapshot."""

from copy import deepcopy
from uuid import uuid4

import psycopg
import pytest
from familycare_api.claims.errors import ClaimInvalid
from familycare_api.claims.repository import ClaimRepository
from familycare_api.claims.schemas import ClaimCaseResponse
from familycare_api.common.scope import HouseholdScope
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests.test_guidance_review_projection_integration import (
    _project,
    _psycopg_url,
    _read,
    _stage,
    changes_database,  # noqa: F401
    enrollment_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_guidance_review_projection_integration import (
    unreviewed_original as unreviewed_original,
)

pytestmark = pytest.mark.integration


def _create(sample, *, run_id=None, scope=None):
    return ClaimCaseResponse.model_validate(
        ClaimRepository(sample.url).create_guidance_claim_case(
            scope or sample.scope,
            sample.event.id,
            run_id=run_id or sample.original.run_id,
            expected_event_version=sample.event.version,
            coverage=sample.packet.coverage_ref,
            review_job_id=sample.job.id,
        )
    )


def test_missing_local_candidate_recovered_by_review_can_start_preparation(unreviewed_original):
    sample = unreviewed_original
    _stage(sample)
    assert _project(sample) == 1
    review = _read(sample)
    assert review.state == "partial" and not sample.original.local_guidance.candidates
    claim = _create(sample)
    assert claim.status == "preparing" and claim.paid_amount is None
    local = claim.snapshot.local_guidance
    assert local.candidate.estimate.amount == "300"
    assert local.run_id == sample.original.run_id
    assert local.review.review_job_id == sample.job.id
    assert local.review.original_decision_run_id == sample.original.run_id
    assert local.review.source_digest == review.result.source_digest
    assert len(local.review.result_digest) == 64
    assert _create(sample).id == claim.id
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        assert connection.execute("SELECT count(*) AS n FROM claim_history").fetchone()["n"] == 0
        assert connection.execute(
            "SELECT local_guidance_json FROM decision_runs WHERE id=%s", (sample.original.run_id,)
        ).fetchone()["local_guidance_json"] == sample.original.local_guidance.model_dump(
            mode="json"
        )
        snapshot = connection.execute(
            "SELECT * FROM claim_case_snapshots WHERE claim_case_id=%s", (claim.id,)
        ).fetchone()
        assert snapshot["review_job_id"] == sample.job.id
        for fault in ("amount", "provenance", "missing_review_id"):
            candidate = deepcopy(snapshot["candidate_snapshot_json"])
            if fault == "amount":
                candidate["local_guidance"]["candidate"]["estimate"]["amount"] = "999999"
            elif fault == "provenance":
                candidate["local_guidance"]["review"]["original_decision_run_id"] = str(uuid4())
            with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
                connection.execute(
                    "INSERT INTO claim_case_snapshots(id,claim_case_id,snapshot_version,"
                    "candidate_snapshot_json,rule_snapshot_json,policy_snapshot_json,"
                    "evidence_snapshot_json,calculation_snapshot_json,"
                    "snapshot_sha256,review_job_id) "
                    "VALUES(%s,%s,2,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        uuid4(),
                        claim.id,
                        Jsonb(candidate),
                        Jsonb(snapshot["rule_snapshot_json"]),
                        Jsonb(snapshot["policy_snapshot_json"]),
                        Jsonb(snapshot["evidence_snapshot_json"]),
                        Jsonb(snapshot["calculation_snapshot_json"]),
                        snapshot["snapshot_sha256"],
                        None if fault == "missing_review_id" else sample.job.id,
                    ),
                )
        with pytest.raises(psycopg.errors.RaiseException), connection.transaction():
            connection.execute(
                "UPDATE claim_case_snapshots SET review_job_id=NULL WHERE claim_case_id=%s",
                (claim.id,),
            )


def test_equivalent_new_run_uses_original_review_and_keeps_first_claim_snapshot(
    unreviewed_original,
):
    sample = unreviewed_original
    _stage(sample)
    assert _project(sample) == 1
    new_run = sample.service.analyze_medical_event(sample.event.id)
    assert new_run.run_id != sample.original.run_id
    claim = _create(sample, run_id=new_run.run_id)
    local = claim.snapshot.local_guidance
    assert local.run_id == new_run.run_id
    assert local.review.original_decision_run_id == sample.original.run_id
    original_request = _create(sample)
    assert original_request.id == claim.id
    assert original_request.snapshot.snapshot_sha256 == claim.snapshot.snapshot_sha256
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        assert connection.execute("SELECT count(*) FROM guidance_review_requests").fetchone() == (
            1,
        )
        assert connection.execute("SELECT count(*) FROM claim_case_snapshots").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM claim_history").fetchone() == (0,)


@pytest.mark.parametrize("fault", ["opinion_only", "stale", "other_household"])
def test_review_claim_rejects_unavailable_candidate_or_unbound_source(unreviewed_original, fault):
    sample = unreviewed_original
    _stage(sample, no_suggestions=fault == "opinion_only")
    assert _project(sample) == 1
    if fault == "stale":
        with psycopg.connect(_psycopg_url(sample.url)) as connection:
            connection.execute(
                "UPDATE medical_events SET version=version+1 WHERE id=%s", (sample.event.id,)
            )
    with pytest.raises(ClaimInvalid):
        _create(sample, scope=HouseholdScope(uuid4()) if fault == "other_household" else None)
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        assert connection.execute("SELECT count(*) FROM claim_cases").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM claim_history").fetchone() == (0,)
