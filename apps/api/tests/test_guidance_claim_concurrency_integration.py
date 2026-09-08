"""Real PostgreSQL races and rollback for saved, source-bound guidance claims."""

import os
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import psycopg
import pytest
from familycare_api.claims.errors import ClaimRepositoryUnavailable
from familycare_api.claims.repository import ClaimRepository
from familycare_api.claims.schemas import ClaimCaseResponse
from familycare_api.common.coverage_identity import CanonicalCoverageRef
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.repository import DecisionRepository
from familycare_api.decisions.service import DecisionService
from familycare_api.insurance_reconciliation.canonical_repository import CanonicalLinkRepository
from familycare_api.insurance_reconciliation.source_bindings import (
    KnowledgeSourceBindingRepository,
    KnowledgeSourceManifest,
)
from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
from psycopg.rows import dict_row

from apps.api.tests.test_decision_integration import (
    _create_event,
    _psycopg_url,
    _reset_database,
    _seed,
)
from apps.api.tests.test_native_range_enrollment_integration import (
    _retain_native,
    _store_words,
    _words,
    enrollment_database,  # noqa: F401
    native_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_private_knowledge_decision_integration import (
    ACTOR_ID,
    _seed_private_publication,
)

pytestmark = pytest.mark.integration


@dataclass(frozen=True, repr=False)
class SavedGuidanceSource:
    url: str
    scope: HouseholdScope
    event_id: UUID
    run_id: UUID
    ref: CanonicalCoverageRef
    snapshot: dict[str, Any]

    def create(self) -> ClaimCaseResponse:
        return ClaimCaseResponse.model_validate(
            ClaimRepository(self.url).create_guidance_claim_case(
                self.scope,
                self.event_id,
                run_id=self.run_id,
                expected_event_version=1,
                coverage=self.ref,
            )
        )


@pytest.fixture(params=["operational", "private"])
def saved_guidance_source(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[SavedGuidanceSource]:
    # The root pytest guard validates this explicit disposable URL before fixtures run.
    url = os.environ["FAMILYCARE_TEST_DATABASE_URL"]
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("PGOPTIONS", "-c statement_timeout=15000 -c lock_timeout=10000")
    _reset_database(url)
    try:
        seeded = _seed(url)
        service = DecisionService(seeded.scope_a, DecisionRepository(url))
        if request.param == "private":
            _seed_private_publication(url, seeded, tmp_path)
            event = service.create_medical_event(
                family_member_id=seeded.member_a,
                mode="post_treatment",
                situation="Synthetic sample category phrase event.",
                event_date=date(2026, 6, 15),
                facts={"MedicalEvent.classification": "sample_category"},
                confirmation={"MedicalEvent.classification": "user"},
            )
        else:
            event = _create_event(service, seeded.member_a)
        result = service.analyze_medical_event(event.id)
        assert result.local_guidance is not None
        candidate = next(
            candidate
            for candidate in result.local_guidance.candidates
            if (
                candidate.ref.kind == "PRIVATE_KNOWLEDGE_COVERAGE"
                if request.param == "private"
                else candidate.ref.coverage_id == seeded.good_rider_id
            )
        )
        yield SavedGuidanceSource(
            url,
            seeded.scope_a,
            event.id,
            result.run_id,
            candidate.ref,
            result.local_guidance.model_dump(mode="json"),
        )
    finally:
        _reset_database(url)


def _assert_persisted_counts(source: SavedGuidanceSource, count: int) -> None:
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM claim_cases WHERE medical_event_id=%s", (source.event_id,)
        ).fetchone() == (count,)
        for table in ("claim_case_snapshots", "claim_status_events"):
            assert connection.execute(
                f"SELECT count(*) FROM {table} s JOIN claim_cases c ON c.id=s.claim_case_id "
                "WHERE c.medical_event_id=%s",
                (source.event_id,),
            ).fetchone() == (count,)
        assert connection.execute(
            "SELECT count(*) FROM claim_history WHERE medical_event_id=%s", (source.event_id,)
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT local_guidance_json FROM decision_runs WHERE id=%s", (source.run_id,)
        ).fetchone() == (source.snapshot,)


def test_concurrent_identical_guidance_requests_create_one_complete_claim(
    saved_guidance_source: SavedGuidanceSource,
) -> None:
    source = saved_guidance_source
    ready = Barrier(3)

    def create() -> ClaimCaseResponse:
        ready.wait(timeout=10)
        return source.create()

    # Both real application transactions must reach the held event lock before release.
    # Merely submitting two fast tasks would not establish an overlapping race.
    with psycopg.connect(_psycopg_url(source.url)) as blocker:
        blocker.execute("SELECT id FROM medical_events WHERE id=%s FOR UPDATE", (source.event_id,))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create) for _ in range(2)]
            ready.wait(timeout=10)
            try:
                with psycopg.connect(_psycopg_url(source.url), autocommit=True) as observer:
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        waiting = observer.execute(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE datname=current_database() AND pid<>pg_backend_pid() "
                            "AND wait_event_type='Lock' AND query LIKE %s",
                            ("%SELECT event.*%",),
                        ).fetchone()[0]
                        if waiting == 2:
                            break
                        time.sleep(0.01)
                    else:
                        pytest.fail("both claim transactions did not reach the event lock")
            finally:
                blocker.commit()
            claims = [future.result(timeout=20) for future in futures]
    assert claims[0].id == claims[1].id
    assert claims[0].snapshot.snapshot_sha256 == claims[1].snapshot.snapshot_sha256
    assert all(claim.status == "preparing" and claim.paid_amount is None for claim in claims)
    assert claims[0].snapshot.local_guidance is not None
    assert claims[0].snapshot.local_guidance.candidate.ref == source.ref
    _assert_persisted_counts(source, 1)


def test_snapshot_insert_failure_rolls_back_claim_and_status_before_retry(
    saved_guidance_source: SavedGuidanceSource,
) -> None:
    source = saved_guidance_source
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        connection.execute("""
            CREATE FUNCTION synthetic_fail_guidance_claim_snapshot() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
              IF NEW.candidate_snapshot_json ? 'local_guidance' THEN
                RAISE EXCEPTION 'SYNTHETIC_SNAPSHOT_WRITE_FAILURE' USING ERRCODE='P0001';
              END IF;
              RETURN NEW;
            END $$;
            CREATE TRIGGER synthetic_fail_guidance_claim_snapshot
              BEFORE INSERT ON claim_case_snapshots FOR EACH ROW
              EXECUTE FUNCTION synthetic_fail_guidance_claim_snapshot();
        """)
    try:
        with pytest.raises(ClaimRepositoryUnavailable):
            source.create()
        _assert_persisted_counts(source, 0)
    finally:
        with psycopg.connect(_psycopg_url(source.url)) as connection:
            connection.execute(
                "DROP TRIGGER synthetic_fail_guidance_claim_snapshot ON claim_case_snapshots;"
                "DROP FUNCTION synthetic_fail_guidance_claim_snapshot()"
            )
    assert source.create().status == "preparing"
    _assert_persisted_counts(source, 1)


def test_legacy_and_guidance_requests_reuse_private_claim_after_verified_alias_link(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reuse the real native enrollment + published private package fixture sequence
    # from test_canonical_links_integration; never fabricate a verified link row.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    url, job = request.getfixturevalue("native_database")
    scope = HouseholdScope(job.household_space_id)
    _store_words(
        url,
        job,
        _words(
            [
                "Policy certificate",
                "Policy number: synthetic-policy-001",
                "Insured: Family Member A",
                "Sample Insurer Sample Plan",
                "Sample Hospital Benefit sum assured: 10000 KRW",
            ]
        ),
    )
    _retain_native(url, job, name="Sample Hospital Benefit", amount=10000)
    assert RangeEnrollmentProjector(url).project_pending() == 2
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute("TRUNCATE medical_events,private_knowledge_import_runs CASCADE")
        connection.execute("DELETE FROM app_users WHERE id=%s", (ACTOR_ID,))
    try:
        run, _ = _seed_private_publication(
            url, SimpleNamespace(scope_a=scope, member_a=job.family_member_id), tmp_path
        )
        service = DecisionService(scope, DecisionRepository(url))
        event = service.create_medical_event(
            family_member_id=job.family_member_id,
            mode="post_treatment",
            situation="Synthetic sample category phrase event.",
            event_date=date(2025, 6, 15),
            facts={"MedicalEvent.classification": "sample_category"},
            confirmation={"MedicalEvent.classification": "user"},
        )
        first = service.analyze_medical_event(event.id)
        assert first.local_guidance is not None
        private = first.local_guidance.candidates[0]
        assert private.ref.kind == "PRIVATE_KNOWLEDGE_COVERAGE"
        saved = SavedGuidanceSource(
            url,
            scope,
            event.id,
            first.run_id,
            private.ref,
            first.local_guidance.model_dump(mode="json"),
        )
        original = saved.create()
        with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
            binding = connection.execute(
                "SELECT id,source_alias_digest_sha256 FROM private_knowledge_document_bindings "
                "WHERE import_run_id=%s AND source_alias='synthetic-certificate-source'",
                (run,),
            ).fetchone()
            version = connection.execute(
                "SELECT content_sha256,page_count FROM document_versions WHERE id=%s",
                (job.document_version_id,),
            ).fetchone()
            evidence = connection.execute(
                "SELECT id FROM evidence WHERE extraction_id=%s LIMIT 1", (job.extraction_id,)
            ).fetchone()["id"]
            digest = connection.execute(
                "SELECT package_digest_sha256 FROM private_knowledge_import_runs WHERE id=%s",
                (run,),
            ).fetchone()["package_digest_sha256"]
        KnowledgeSourceBindingRepository(url).apply_manifest(
            scope,
            KnowledgeSourceManifest.model_validate(
                {
                    "schema_version": "knowledge-source-bindings-v1",
                    "import_run_id": run,
                    "package_digest_sha256": digest,
                    "entries": [
                        {
                            "document_binding_id": binding["id"],
                            "source_alias_digest_sha256": binding["source_alias_digest_sha256"],
                            "document_version_id": job.document_version_id,
                            "evidence_id": evidence,
                            **version,
                            "document_kind": "policy",
                            "expected_current_binding_id": None,
                        }
                    ],
                }
            ),
        )
        canonical = CanonicalLinkRepository(url)
        assert canonical.refresh(scope) == 1
        linked = service.analyze_medical_event(event.id)
        assert linked.local_guidance is not None
        selected = linked.local_guidance.candidates[0]
        assert selected.canonical_identity is not None
        assert selected.ref.kind == "OPERATIONAL_RIDER"
        assert private.ref in selected.canonical_identity.source_refs
        claims = ClaimRepository(url)
        modern = claims.create_guidance_claim_case(
            scope, event.id, run_id=linked.run_id, expected_event_version=1, coverage=selected.ref
        )
        legacy = claims.create_claim_case(scope, event.id, rider_id=selected.ref.coverage_id)
        assert modern["id"] == legacy["id"] == original.id
        assert modern["snapshot"]["snapshot_sha256"] == original.snapshot.snapshot_sha256
        assert legacy["snapshot"]["snapshot_sha256"] == original.snapshot.snapshot_sha256
        _assert_persisted_counts(saved, 1)
    finally:
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute("TRUNCATE medical_events,private_knowledge_import_runs CASCADE")
            connection.execute("DELETE FROM app_users WHERE id=%s", (ACTOR_ID,))
