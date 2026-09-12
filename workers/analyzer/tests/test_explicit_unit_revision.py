"""Explicit unit processing preserves old source contracts and refuses lossy downgrade."""

import pytest
from familycare_worker import retained_policy

from apps.api.tests.test_metadata_navigation_publication import _migrate
from workers.analyzer.tests.test_retained_field_proof_revision import _contracts
from workers.analyzer.tests.test_retained_policy_resubmission import (
    _assert_original_preserved,
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from workers.analyzer.tests.test_retained_policy_resubmission import (
    retained_source as retained_source,
)

pytestmark = pytest.mark.integration
PREVIOUS = "0080_scoped_policy_verifier"
REVISION = "0081_explicit_unit_draft"
PIPELINE = "retained-policy-association-v12"


def test_explicit_unit_migration_roundtrip_and_history_guard(retained_source):
    sample = retained_source
    household = sample.original.household_space_id
    try:
        assert _migrate(sample.url, "downgrade", PREVIOUS).returncode == 0
        before = _contracts(sample.url, household)
        assert PIPELINE not in before["source"] and PIPELINE not in before["guard"]
        assert _migrate(sample.url, "upgrade", REVISION).returncode == 0
        after = _contracts(sample.url, household)
        assert PIPELINE in after["source"] and PIPELINE in after["guard"]
        assert before["privacy"] == after["privacy"]
        assert _migrate(sample.url, "downgrade", PREVIOUS).returncode == 0
        assert _contracts(sample.url, household) == before
        assert _migrate(sample.url, "upgrade", REVISION).returncode == 0
        repository = retained_policy.RetainedPolicyRepository(sample.url)
        arguments = dict(
            household_space_id=household,
            source_job_id=sample.original.id,
            expected_generation_id=sample.generation,
            pipeline_revision=PIPELINE,
        )
        job = repository.enqueue(**arguments)
        assert repository.enqueue(**arguments).id == job.id
        assert _migrate(sample.url, "downgrade", PREVIOUS).returncode != 0
        assert _contracts(sample.url, household) == after
        _assert_original_preserved(sample)
    finally:
        assert _migrate(sample.url, "upgrade", "head").returncode == 0
