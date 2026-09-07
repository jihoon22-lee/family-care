"""Source-bound identity links retain history and honor current user decisions."""

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.insurance_reconciliation.source_bindings import (
    KnowledgeSourceBindingRepository,
    KnowledgeSourceManifest,
)
from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
from psycopg.types.json import Jsonb

from apps.api.tests import test_insurance_reconciliation_migration_integration as seed
from apps.api.tests.test_native_range_enrollment_integration import (
    _psycopg_url,
    _retain_native,
    _store_words,
    _words,
    enrollment_database,  # noqa: F401
    native_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)

pytestmark = pytest.mark.integration


def test_complete_enrollment_and_canonical_identity_use_paged_storage(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    from familycare_api.insurance_reconciliation.canonical_repository import CanonicalLinkRepository
    from familycare_worker import document_structure_repository as storage

    monkeypatch.setattr(storage, "_INLINE_STRUCTURE_BYTES", 0)
    url, job, scope, coverage, rider = request.getfixturevalue("canonical_database")
    repository = CanonicalLinkRepository(url)
    assert repository.refresh(scope) == 1
    assert repository.read_current(scope)[0].rider_id == rider


@pytest.fixture()
def canonical_database(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Any:
    url, job = request.getfixturevalue("native_database")
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
    fixture_mode = getattr(request, "param", None)
    label_alias = fixture_mode == "label_alias"
    confirmation_mode = None if label_alias else fixture_mode
    _retain_native(
        url,
        job,
        review_rider=confirmation_mode is not None,
        candidate_name="Synthetic Misread Name" if confirmation_mode == "correct_name" else None,
    )
    assert RangeEnrollmentProjector(url).project_pending() == (
        1 if confirmation_mode is not None else 2
    )
    if confirmation_mode is not None:
        from familycare_api.policies.candidate_models import CandidateCorrectionRequest
        from familycare_api.policies.candidate_repository import CandidateRepository

        candidates = CandidateRepository(url)
        candidate_scope = HouseholdScope(job.household_space_id)
        item = next(
            item
            for item in candidates.list_review_items(candidate_scope, status="NEEDS_REVIEW")
            if item.candidate_kind == "rider"
        )
        actor = uuid4()
        if confirmation_mode != "confirm":
            field = next(field for field in item.fields if field.field_id == "rider_name")
            item = candidates.correct_field(
                candidate_scope,
                request=CandidateCorrectionRequest(
                    expected_version=item.expected_version,
                    field_id="rider_name",
                    value="Sample Rider"
                    if confirmation_mode == "correct_name"
                    else "Friendly Sample Coverage",
                    evidence_id=field.evidence_ids[0],
                ),
                actor_id=actor,
                review_item_id=item.review_item_id,
            )
        candidates.transition(
            candidate_scope,
            item.review_item_id,
            expected_version=item.expected_version,
            status="USER_CONFIRMED",
            actor_id=actor,
        )
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute("TRUNCATE private_knowledge_import_runs CASCADE")
        actor = connection.execute(
            "SELECT id FROM app_users WHERE household_space_id=%s LIMIT 1",
            (job.household_space_id,),
        ).fetchone()[0]
        for key, value in {
            "HOUSEHOLD_ID": job.household_space_id,
            "MEMBER_A_ID": job.family_member_id,
            "USER_ID": actor,
        }.items():
            monkeypatch.setattr(seed, key, value)
        seed._seed_knowledge(connection)
        rider = connection.execute(
            "SELECT rider_id FROM range_enrollment_publications WHERE rider_id IS NOT NULL"
        ).fetchone()[0]
        evidence = connection.execute(
            "SELECT source_evidence_id FROM riders WHERE id=%s", (rider,)
        ).fetchone()[0]
        version = connection.execute(
            "SELECT content_sha256,page_count FROM document_versions WHERE id=%s",
            (job.document_version_id,),
        ).fetchone()
        binding, coverage = uuid4(), uuid4()
        alias = "Synthetic Policy"
        alias_digest = hashlib.sha256(alias.encode()).hexdigest()
        connection.execute(
            "INSERT INTO private_knowledge_document_bindings(id,import_run_id,"
            "household_space_id,source_alias,source_alias_digest_sha256,binding_decision,"
            "binding_reason_code,content_digest_decision,page_count_decision,"
            "document_kind_decision,source_record_json,source_record_digest_sha256) VALUES "
            "(%s,%s,%s,%s,%s,'UNKNOWN','NO_EXACT_BINDING','UNKNOWN','UNKNOWN','UNKNOWN',"
            "'{}',%s)",
            (binding, seed.RUN_ID, job.household_space_id, alias, alias_digest, "9" * 64),
        )
        source = {
            "warnings": ["합성 원문 설명"],
            "name": "Sample Rider",
            "sum_assured_krw": 317,
            "certificate_review": {
                "name": "Sample Rider",
                "enrollment_decision": "MATCH",
                "component_class": "BENEFIT_COVERAGE",
                "evidence_locations": [{"document_alias": alias, "physical_page": 1, "line": 9000}],
            },
        }
        if label_alias:
            source.update(
                name="Sample Normalized Coverage",
                canonical_policy_id="synthetic-policy-001",
                canonical_rider_id="synthetic-coverage-001",
            )
            source["certificate_review"].update(
                canonical_policy_id="synthetic-policy-001",
                canonical_rider_id="synthetic-coverage-001",
            )
        digest = hashlib.sha256(
            json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        connection.execute(
            "INSERT INTO private_knowledge_coverages(id,import_run_id,household_space_id,"
            "knowledge_contract_id,source_coverage_key,display_name,component_role,"
            "component_classification,enrollment_decision,benefit_type,insured_amount,"
            "currency,renewal_state,operational_binding_reason_code,source_record_json,"
            "source_record_digest_sha256) VALUES (%s,%s,%s,%s,'synthetic-coverage-001',"
            "%s,'RIDER','BENEFIT_COVERAGE','MATCH','UNKNOWN',317,'KRW',"
            "'UNKNOWN','NO_EXACT_BINDING',%s,%s)",
            (
                coverage,
                seed.RUN_ID,
                job.household_space_id,
                seed.CONTRACT_ID,
                source["name"],
                Jsonb(source),
                digest,
            ),
        )
    scope = HouseholdScope(job.household_space_id)
    manifest = KnowledgeSourceManifest.model_validate(
        {
            "schema_version": "knowledge-source-bindings-v1",
            "import_run_id": seed.RUN_ID,
            "package_digest_sha256": "1" * 64,
            "entries": [
                {
                    "document_binding_id": binding,
                    "source_alias_digest_sha256": alias_digest,
                    "document_version_id": job.document_version_id,
                    "evidence_id": evidence,
                    "content_sha256": version[0],
                    "page_count": version[1],
                    "document_kind": "policy",
                    "expected_current_binding_id": None,
                }
            ],
        }
    )
    KnowledgeSourceBindingRepository(url).apply_manifest(scope, manifest)
    try:
        yield url, job, scope, coverage, rider
    finally:
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute("TRUNCATE private_knowledge_import_runs CASCADE")


def test_canonical_writer_preserves_sources_and_revalidates_current_identity(
    canonical_database: Any,
) -> None:
    from familycare_api.insurance_reconciliation.canonical_repository import CanonicalLinkRepository

    url, job, scope, coverage, rider = canonical_database
    repository = CanonicalLinkRepository(url)
    with psycopg.connect(_psycopg_url(url)) as connection:
        before = connection.execute(
            "SELECT to_jsonb(c) FROM private_knowledge_coverages c WHERE id=%s", (coverage,)
        ).fetchone()
    assert repository.refresh(scope) == 1
    assert repository.refresh(scope) == 0
    current = repository.read_current(scope)
    assert len(current) == 1
    assert current[0].knowledge_coverage_id == coverage and current[0].rider_id == rider
    assert current[0].proofs[0]["private_evidence_location"]["line"] == 9000
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert (
            connection.execute(
                "SELECT to_jsonb(c) FROM private_knowledge_coverages c WHERE id=%s", (coverage,)
            ).fetchone()
            == before
        )
        connection.execute(
            "UPDATE riders SET insured_amount=619,version=version+1 WHERE id=%s", (rider,)
        )
    assert repository.read_current(scope) == ()
    assert repository.refresh(scope) == 1
    assert repository.read_current(scope)[0].ledger_version == 2
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM private_knowledge_canonical_links"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT insured_amount FROM riders WHERE id=%s", (rider,)
        ).fetchone() == (619,)


def test_inventory_budget_measures_needed_page_nodes_not_duplicate_full_sources(
    canonical_database: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from familycare_api.insurance_reconciliation import canonical_repository as canonical

    url, job, scope, coverage, rider = canonical_database
    with psycopg.connect(_psycopg_url(url)) as connection:
        total, projected = connection.execute(
            "SELECT octet_length(structure_json::text), octet_length(jsonb_build_object("
            "'lineage',structure_json->'lineage','nodes',structure_json->'nodes')::text) "
            "FROM document_structure_generations WHERE document_version_id=%s",
            (job.document_version_id,),
        ).fetchone()
    assert projected + 10 < total
    monkeypatch.setattr(canonical, "_MAX_INVENTORY_BYTES", projected + 10)
    repository = canonical.CanonicalLinkRepository(url)
    assert repository.refresh(scope) == 1
    assert repository.read_current(scope)[0].rider_id == rider
    # Insufficient space for even the complete source page cannot justify a link.
    monkeypatch.setattr(canonical, "_MAX_INVENTORY_BYTES", projected - 1)
    assert repository.read_current(scope) == ()


def test_page_inventory_retains_context_and_isolates_an_oversized_other_page(
    canonical_database: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from familycare_api.insurance_reconciliation import canonical_repository as canonical
    from psycopg.rows import dict_row

    url, job, scope, coverage, rider = canonical_database
    # Exercise the storage projection with wholly synthetic extra nodes. These
    # are inventory fixtures, not enrollment publications or verified source data.
    header = {"node_id": "synthetic-context", "page_number": 2, "text": "Rider name"}
    bulk = {"node_id": "synthetic-other-page", "page_number": 2, "text": "Sample " * 20000}
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        original = connection.execute(
            "SELECT * FROM document_structure_generations WHERE document_version_id=%s",
            (job.document_version_id,),
        ).fetchone()
        structure = original["structure_json"]
        original_ids = {node["node_id"] for node in structure["nodes"]}
        structure["nodes"][0]["context_node_ids"] = [header["node_id"]]
        structure["nodes"].extend([header, bulk])
        second = uuid4()
        connection.execute(
            "INSERT INTO document_structure_generations(id,household_space_id,family_member_id,"
            "batch_item_id,document_version_id,extraction_id,identity_sha256,structure_version,"
            "structure_json,plan_json,range_plan_complete) SELECT %s,household_space_id,"
            "family_member_id,batch_item_id,document_version_id,extraction_id,%s,structure_version,"
            "%s,plan_json,range_plan_complete FROM document_structure_generations WHERE id=%s",
            (second, "8" * 64, Jsonb(structure), original["id"]),
        )
        monkeypatch.setattr(canonical, "_MAX_INVENTORY_BYTES", 100000)
        inventory = canonical._SourceInventory(connection, scope)
        assert inventory.page(structure["lineage"]["content_sha256"], 2) == {}
        pages = inventory.page(structure["lineage"]["content_sha256"], 1)
        assert set(pages) == {original["id"], second}
        assert set(pages[second]) == {"lineage", "nodes"}
        assert {node["node_id"] for node in pages[second]["nodes"]} == original_ids | {
            header["node_id"]
        }
        assert pages[second]["lineage"] == structure["lineage"]
        assert (
            canonical._SourceInventory(connection, HouseholdScope(uuid4())).page(
                structure["lineage"]["content_sha256"], 1
            )
            == {}
        )


@pytest.mark.parametrize(
    "change",
    [
        "user_reject",
        "deleted_party",
        "deleted_member",
        "deleted_document",
        "source_hash",
        "foreign_scope",
        "duplicate_coverage",
    ],
)
def test_canonical_rechecks_scope_source_and_user_overrides(
    canonical_database: Any, change: str
) -> None:
    from familycare_api.insurance_reconciliation.canonical_repository import CanonicalLinkRepository

    url, job, scope, coverage, rider = canonical_database
    repository = CanonicalLinkRepository(url)
    assert repository.refresh(scope) == 1
    with psycopg.connect(_psycopg_url(url)) as connection:
        if change == "user_reject":
            seed._insert_link(
                connection,
                link_id=uuid4(),
                actor_id=seed.USER_ID,
                decision="NO_MATCH",
                policy_id=None,
            )
        elif change == "deleted_party":
            connection.execute(
                "UPDATE policy_parties SET deleted_at=clock_timestamp() "
                "WHERE household_space_id=%s",
                (job.household_space_id,),
            )
        elif change == "deleted_member":
            connection.execute(
                "UPDATE family_members SET deleted_at=clock_timestamp() WHERE id=%s",
                (job.family_member_id,),
            )
        elif change == "deleted_document":
            connection.execute(
                "UPDATE documents SET deleted_at=clock_timestamp() WHERE id=(SELECT "
                "document_id FROM document_versions WHERE id=%s)",
                (job.document_version_id,),
            )
        elif change == "source_hash":
            connection.execute(
                "UPDATE document_versions SET content_sha256=%s WHERE id=%s",
                ("f" * 64, job.document_version_id),
            )
        elif change == "duplicate_coverage":
            connection.execute(
                "INSERT INTO private_knowledge_coverages SELECT "
                "(jsonb_populate_record(NULL::private_knowledge_coverages,"
                "to_jsonb(c)||jsonb_build_object('id',%s::uuid,'source_coverage_key',"
                "'synthetic-duplicate'))).* FROM private_knowledge_coverages c WHERE id=%s",
                (uuid4(), coverage),
            )
        else:
            scope = HouseholdScope(uuid4())
    assert repository.read_current(scope) == ()
    assert repository.refresh(scope) == 0


def test_unpublished_second_native_occurrence_prevents_cached_identity(
    canonical_database: Any,
) -> None:
    from familycare_api.insurance_reconciliation.canonical_repository import CanonicalLinkRepository
    from familycare_worker.policy_range_repository import PolicyRangeRepository

    from apps.api.tests.test_native_range_enrollment_integration import WORKER, _reextract

    url, job, scope, coverage, rider = canonical_database
    repository = CanonicalLinkRepository(url)
    assert repository.refresh(scope) == 1
    newer = _reextract(url, job)
    _store_words(
        url,
        newer,
        _words(
            [
                "Policy certificate",
                "Policy number: synthetic-policy-001",
                "Insured: Family Member A",
                "Sample Insurer Sample Plan",
                "Sample Rider sum assured: 317 KRW",
                "Sample Rider sum assured: 619 KRW",
            ]
        ),
    )
    PolicyRangeRepository(url).next(newer, WORKER, sensitive_terms=("Family Member A",))
    assert repository.read_current(scope) == ()
    assert repository.refresh(scope) == 0


def test_changed_publication_evidence_is_not_current_proof(canonical_database: Any) -> None:
    from familycare_api.insurance_reconciliation.canonical_repository import CanonicalLinkRepository

    url, job, scope, coverage, rider = canonical_database
    repository = CanonicalLinkRepository(url)
    assert repository.refresh(scope) == 1
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE evidence SET content_sha256=%s WHERE id=(SELECT source_evidence_id "
            "FROM riders WHERE id=%s)",
            ("f" * 64, rider),
        )
    assert repository.read_current(scope) == ()
    assert repository.refresh(scope) == 0


def test_program_identity_is_visible_in_member_reconciliation(canonical_database: Any) -> None:
    from familycare_api.insurance_reconciliation.canonical_repository import CanonicalLinkRepository
    from familycare_api.insurance_reconciliation.repository import InsuranceReconciliationRepository
    from familycare_api.insurance_reconciliation.schemas import (
        MemberInsuranceReconciliationResponse,
    )

    url, job, scope, coverage, rider = canonical_database
    CanonicalLinkRepository(url).refresh(scope)
    value = InsuranceReconciliationRepository(url).get_member(scope, job.family_member_id)
    assert value is not None
    projection = value.contracts[0]
    assert projection.operational_link.authority == "PROGRAM_VERIFIED_SOURCE_IDENTITY"
    assert projection.operational_link.decision == "MATCH"
    assert projection.operational_link.id is None
    assert value.summary.orphan_operational_contracts == 0
    assert (
        MemberInsuranceReconciliationResponse.from_domain(value)
        .contracts[0]
        .operational_link.authority
        == "PROGRAM_VERIFIED_SOURCE_IDENTITY"
    )


def test_concurrent_refresh_is_idempotent_and_history_cannot_be_changed(
    canonical_database: Any,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from familycare_api.insurance_reconciliation.canonical_repository import CanonicalLinkRepository

    url, job, scope, coverage, rider = canonical_database
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(lambda _: CanonicalLinkRepository(url).refresh(scope), range(2))
        )
    assert sorted(results) == [0, 1]
    with psycopg.connect(_psycopg_url(url)) as connection:
        for sql in (
            "DELETE FROM private_knowledge_canonical_links",
            "UPDATE private_knowledge_canonical_links SET field_value_conflict=true",
        ):
            with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
                connection.execute(sql)
        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            connection.execute(
                "INSERT INTO private_knowledge_canonical_links SELECT "
                "(jsonb_populate_record(NULL::private_knowledge_canonical_links,"
                "to_jsonb(c)||jsonb_build_object('id',%s::uuid,'household_space_id',"
                "%s::uuid,'fingerprint',%s::text))).* FROM private_knowledge_canonical_links c",
                (uuid4(), uuid4(), "f" * 64),
            )
    assert len(CanonicalLinkRepository(url).read_current(scope)) == 1


def test_unrelated_evidence_cannot_replace_enrolled_source(canonical_database: Any) -> None:
    from familycare_api.insurance_reconciliation.canonical_repository import CanonicalLinkRepository

    url, job, scope, coverage, rider = canonical_database
    repository = CanonicalLinkRepository(url)
    assert repository.refresh(scope) == 1
    with psycopg.connect(_psycopg_url(url)) as connection:
        evidence = connection.execute(
            "INSERT INTO evidence(household_space_id,document_version_id,extraction_id,"
            "content_sha256,physical_page,review_state) SELECT household_space_id,"
            "document_version_id,extraction_id,content_sha256,physical_page,review_state "
            "FROM evidence WHERE id=(SELECT source_evidence_id FROM riders WHERE id=%s) "
            "RETURNING id",
            (rider,),
        ).fetchone()[0]
        connection.execute(
            "UPDATE riders SET source_evidence_id=%s,version=version+1 WHERE id=%s",
            (evidence, rider),
        )
    assert repository.refresh(scope) == 0
    assert repository.read_current(scope) == ()


def test_detailed_coverage_exposes_verified_identity_and_current_field_conflicts(
    canonical_database: Any,
) -> None:
    from familycare_api.insurance_reconciliation.canonical_repository import CanonicalLinkRepository
    from familycare_api.private_knowledge.query_repository import (
        PostgresPrivateKnowledgeQueryRepository,
    )

    url, job, scope, coverage, rider = canonical_database
    repository = CanonicalLinkRepository(url)
    repository.refresh(scope)
    query = PostgresPrivateKnowledgeQueryRepository(url)
    detail = query.get_contract(scope, seed.CONTRACT_ID, section_limit=20, section_after=None)
    assert detail is not None
    identity = detail.coverages[0].canonical_identity
    assert identity is not None and identity.ref.coverage_id == rider
    assert identity.source_refs[0].coverage_id == coverage
    assert identity.field_conflicts == ()
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE riders SET insured_amount=619,version=version+1 WHERE id=%s", (rider,)
        )
    stale = query.get_contract(scope, seed.CONTRACT_ID, section_limit=20, section_after=None)
    assert stale.coverages[0].canonical_identity is None
    repository.refresh(scope)
    current = query.get_contract(scope, seed.CONTRACT_ID, section_limit=20, section_after=None)
    assert current.coverages[0].canonical_identity.field_conflicts == ("insured_amount",)
    assert current.coverages[0].insured_amount == 317


def test_live_guidance_retains_canonical_identity_and_old_result_after_correction(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import date
    from types import SimpleNamespace

    from familycare_api.decisions.repository import DecisionRepository
    from familycare_api.decisions.service import DecisionService
    from familycare_api.insurance_reconciliation.canonical_repository import CanonicalLinkRepository
    from psycopg.rows import dict_row

    from apps.api.tests.test_private_knowledge_decision_integration import (
        ACTOR_ID,
        _seed_private_publication,
    )

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
        manifest = KnowledgeSourceManifest.model_validate(
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
        )
        KnowledgeSourceBindingRepository(url).apply_manifest(scope, manifest)
        canonical = CanonicalLinkRepository(url)
        assert canonical.refresh(scope) == 1
        service = DecisionService(scope, DecisionRepository(url))
        event = service.create_medical_event(
            family_member_id=job.family_member_id,
            mode="post_treatment",
            situation="Synthetic sample category phrase event.",
            event_date=date(2025, 6, 15),
            visit_date=date(2025, 6, 16),
            facts={"MedicalEvent.classification": "sample_category"},
            confirmation={"MedicalEvent.classification": "user"},
        )
        first = service.analyze_medical_event(event.id)
        assert first.local_guidance is not None, first.source_failure_codes
        candidate = first.local_guidance.candidates[0]
        assert candidate.canonical_identity is not None
        assert candidate.ref.kind == "OPERATIONAL_RIDER"
        assert candidate.estimate.amount == "1"
        assert len(first.local_guidance.candidates) == 1

        def unavailable_identity(connection, scope):
            connection.execute("SELECT synthetic_unavailable_identity_column")

        with monkeypatch.context() as patch:
            patch.setattr(CanonicalLinkRepository, "read_in_transaction", unavailable_identity)
            fallback = service.analyze_medical_event(event.id)
        assert fallback.local_guidance.candidates[0].estimate.amount == "1"
        assert fallback.local_guidance.candidates[0].canonical_identity is None
        assert "CANONICAL_IDENTITY_UNAVAILABLE" in fallback.source_failure_codes
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute(
                "UPDATE riders SET insured_amount=619,version=version+1 WHERE id=%s",
                (candidate.ref.coverage_id,),
            )
        assert service.get_decision_result(event.id, event.version).stale
        assert canonical.refresh(scope) == 1
        second = service.analyze_medical_event(event.id)
        assert second.local_guidance.candidates[0].estimate.kind == "FORMULA"
        assert second.local_guidance.candidates[0].condition_result == "MATCH"
        assert second.local_guidance.candidates[0].canonical_identity.field_conflicts == (
            "insured_amount",
        )
        assert service.get_decision_result(event.id, event.version).run_id == second.run_id
        with psycopg.connect(_psycopg_url(url)) as connection:
            old = connection.execute(
                "SELECT local_guidance_json FROM decision_runs WHERE id=%s", (first.run_id,)
            ).fetchone()[0]
        assert old == first.local_guidance.model_dump(mode="json")
    finally:
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute("TRUNCATE medical_events,private_knowledge_import_runs CASCADE")
            connection.execute("DELETE FROM app_users WHERE id=%s", (ACTOR_ID,))


def test_identity_lookup_failure_does_not_hide_existing_catalog(
    canonical_database: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from familycare_api.insurance_reconciliation.canonical_repository import CanonicalLinkRepository
    from familycare_api.insurance_reconciliation.repository import InsuranceReconciliationRepository
    from familycare_api.private_knowledge.query_repository import (
        PostgresPrivateKnowledgeQueryRepository,
    )

    url, job, scope, coverage, rider = canonical_database

    def broken(connection, scope):
        connection.execute("SELECT synthetic_unavailable_identity_column")

    monkeypatch.setattr(CanonicalLinkRepository, "read_in_transaction", broken)
    result = PostgresPrivateKnowledgeQueryRepository(url).get_contract(
        scope, seed.CONTRACT_ID, section_limit=20, section_after=None
    )
    assert result is not None and result.coverages[0].id == coverage
    assert result.coverages[0].canonical_identity is None
    summary = InsuranceReconciliationRepository(url).get_member(scope, job.family_member_id)
    assert summary is not None and summary.summary.total_contracts == 1


@pytest.mark.parametrize("canonical_database", ["confirm", "correct_name", "rename"], indirect=True)
def test_user_first_enrollment_gets_independent_native_identity_proof(
    canonical_database: Any,
) -> None:
    from familycare_api.insurance_reconciliation.canonical_repository import CanonicalLinkRepository

    url, job, scope, coverage, rider = canonical_database
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT authority FROM range_enrollment_publications WHERE rider_id=%s", (rider,)
        ).fetchall() == [("USER_CONFIRMED",)]
    repository = CanonicalLinkRepository(url)
    assert repository.refresh(scope) == 1
    current = repository.read_current(scope)
    assert len(current) == 1 and current[0].rider_id == rider
    assert current[0].proofs[0]["publication_authority"] == "USER_CONFIRMED"
    assert current[0].identity().authority == "PROGRAM_VERIFIED_SOURCE_IDENTITY"
    with psycopg.connect(_psycopg_url(url)) as connection:
        for field, value in (
            ("publication_authority", "PROGRAM_VERIFIED"),
            ("name_source_candidate_version_id", str(uuid4())),
        ):
            proofs = json.loads(json.dumps(current[0].proofs))
            proofs[0][field] = value
            with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
                connection.execute(
                    "INSERT INTO private_knowledge_canonical_links SELECT "
                    "(jsonb_populate_record(NULL::private_knowledge_canonical_links, "
                    "to_jsonb(link)||jsonb_build_object('id',%s::uuid,'fingerprint',%s::text, "
                    "'proofs',%s::jsonb))).* FROM private_knowledge_canonical_links link "
                    "WHERE link.knowledge_coverage_id=%s",
                    (uuid4(), "e" * 64, Jsonb(proofs), coverage),
                )
        assert connection.execute(
            "SELECT authority FROM range_enrollment_publications WHERE rider_id=%s", (rider,)
        ).fetchall() == [("USER_CONFIRMED",)]


@pytest.mark.parametrize("canonical_database", ["confirm"], indirect=True)
def test_user_identity_history_prevents_unsupported_downgrade(canonical_database: Any) -> None:
    from alembic import command
    from alembic.config import Config
    from familycare_api.insurance_reconciliation.canonical_repository import CanonicalLinkRepository
    from sqlalchemy.exc import DBAPIError

    url, job, scope, coverage, rider = canonical_database
    assert CanonicalLinkRepository(url).refresh(scope) == 1
    config = Config(Path(__file__).resolve().parents[3] / "apps/api/alembic.ini")
    with psycopg.connect(_psycopg_url(url)) as connection:
        previous_schema = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    with pytest.raises(DBAPIError, match="user publication identity history must be retained"):
        command.downgrade(config, "0034_canonical_links")
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()
            == previous_schema
        )
    assert len(CanonicalLinkRepository(url).read_current(scope)) == 1


@pytest.mark.parametrize("canonical_database", ["label_alias"], indirect=True)
def test_declared_canonical_label_uses_the_original_reviewed_name(canonical_database: Any) -> None:
    from familycare_api.insurance_reconciliation.canonical_repository import CanonicalLinkRepository

    url, job, scope, coverage, rider = canonical_database
    repository = CanonicalLinkRepository(url)
    assert repository.refresh(scope) == 1
    current = repository.read_current(scope)
    assert current[0].rider_id == rider
    assert current[0].field_conflicts == ("display_name",)


@pytest.mark.parametrize("canonical_database", [None, "confirm"], indirect=True)
def test_pending_correction_keeps_last_published_identity(canonical_database: Any) -> None:
    from familycare_api.insurance_reconciliation.canonical_repository import CanonicalLinkRepository
    from familycare_api.policies.candidate_models import CandidateCorrectionRequest
    from familycare_api.policies.candidate_repository import CandidateRepository

    url, job, scope, coverage, rider = canonical_database
    repository = CanonicalLinkRepository(url)
    assert repository.refresh(scope) == 1
    original = repository.read_current(scope)
    status = (
        "USER_CONFIRMED"
        if original[0].proofs[0]["publication_authority"] == "USER_CONFIRMED"
        else "AI_VERIFIED"
    )
    candidates = CandidateRepository(url)
    item = next(
        item
        for item in candidates.list_review_items(scope, status=status)
        if item.candidate_kind == "rider"
    )
    field = next(field for field in item.fields if field.field_id == "sum_assured")
    candidates.correct_field(
        scope,
        request=CandidateCorrectionRequest(
            expected_version=item.expected_version,
            field_id="sum_assured",
            value=619,
            evidence_id=field.evidence_ids[0],
        ),
        actor_id=uuid4(),
        review_item_id=item.review_item_id,
    )
    assert repository.read_current(scope) == original
    assert repository.refresh(scope) == 0
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT insured_amount FROM riders WHERE id=%s", (rider,)
        ).fetchone() == (317,)
