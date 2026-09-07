"""Exact native word positions survive extraction changes without duplicate enrollment."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
from familycare_api.policies.repository import PolicyLedgerRepository
from familycare_worker.ai.range_structurer import PolicyRangeBatch
from familycare_worker.ai.schemas import (
    CandidateField,
    CandidatePipelineResult,
    PolicyCandidate,
    StructurerCandidate,
)
from familycare_worker.policy_jobs import PolicyStructuringJobQueue
from familycare_worker.policy_range_repository import PolicyRangeRepository
from psycopg.types.json import Jsonb

from apps.api.tests.test_range_enrollment_integration import (
    WORKER,
    _psycopg_url,
    enrollment_database,  # noqa: F401 -- shared synthetic fixture
    ranges_database,  # noqa: F401 -- shared synthetic fixture
    seeded_policy_database,  # noqa: F401 -- shared synthetic fixture
    structure_database,  # noqa: F401
)
from workers.analyzer.tests.test_document_text_lines import _words
from workers.analyzer.tests.test_policy_range_repository import _no_facts

pytestmark = pytest.mark.integration


@pytest.fixture()
def native_database(request: pytest.FixtureRequest) -> Iterator[Any]:
    url, job = request.getfixturevalue("enrollment_database")
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
                "DELETE FROM policy_structuring_jobs WHERE household_space_id=%s AND "
                "pipeline_version='synthetic-native-reextract'",
                (job.household_space_id,),
            )
            connection.execute(
                "DELETE FROM document_batch_items WHERE display_label='Synthetic Native Reanalysis'"
            )
            connection.execute(
                "DELETE FROM evidence WHERE household_space_id=%s AND extraction_id IN (SELECT "
                "id FROM extractions WHERE extractor_version='synthetic-native-reextract')",
                (job.household_space_id,),
            )
            connection.execute(
                "DELETE FROM extractions WHERE document_version_id IN (SELECT v.id FROM "
                "document_versions v JOIN documents d ON d.id=v.document_id WHERE "
                "v.id=%s OR d.source_key LIKE %s) AND "
                "extractor_version='synthetic-native-reextract'",
                (job.document_version_id, f"synthetic-native-reimport/{job.household_space_id}/%"),
            )
            connection.execute(
                "DELETE FROM documents WHERE source_key LIKE %s",
                (f"synthetic-native-reimport/{job.household_space_id}/%",),
            )
            connection.execute(
                "DELETE FROM document_batches WHERE family_member_id IN (SELECT id FROM "
                "family_members WHERE household_space_id=%s AND "
                "internal_alias='synthetic-reassigned-member')",
                (job.household_space_id,),
            )
            connection.execute(
                "DELETE FROM family_members WHERE household_space_id=%s AND "
                "internal_alias='synthetic-reassigned-member'",
                (job.household_space_id,),
            )


def _store_words(url: str, job: Any, blocks: list[dict[str, Any]]) -> None:
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE family_members SET display_name='Family Member A' WHERE id=%s",
            (job.family_member_id,),
        )
        page = connection.execute(
            "SELECT id FROM extraction_pages WHERE extraction_id=%s", (job.extraction_id,)
        ).fetchone()[0]
        connection.execute("DELETE FROM extraction_blocks WHERE page_id=%s", (page,))
        for block in blocks:
            connection.execute(
                "INSERT INTO extraction_blocks(page_id,text,bbox,reading_order) VALUES "
                "(%s,%s,%s,%s)",
                (page, block["text"], Jsonb(block["bbox"]), block["reading_order"]),
            )


def _retain_native(
    url: str,
    job: Any,
    *,
    name: str = "Sample Rider",
    amount: int = 317,
    position: int = 0,
    candidate_name: str | None = None,
    review_rider: bool = False,
) -> Any:
    ranges = PolicyRangeRepository(url)
    work = ranges.next(job, WORKER, sensitive_terms=("Family Member A",))
    candidates = []
    assignments = {}
    for kind, label, values in (
        (
            "policy_contract",
            "Sample Insurer",
            {"insurer": "Sample Insurer", "product_name": "Sample Plan"},
        ),
        (
            "rider",
            name,
            {
                "rider_name": candidate_name or name,
                "rider_key": name.lower().replace(" ", "-"),
                "benefit_type": "unknown",
                "sum_assured": amount,
                "currency": "KRW",
            },
        ),
    ):
        evidence = [item for item in work.envelope.evidence if item.primary and label in item.text][
            position if kind == "rider" else 0
        ]
        candidate = StructurerCandidate(
            schema_version="1",
            candidate_id=uuid4(),
            candidate_kind=kind,
            fields=tuple(
                CandidateField(field_id=key, value=value, evidence_ids=(evidence.evidence_id,))
                for key, value in values.items()
            ),
        )
        candidates.append(candidate)
        assignments[
            work.envelope.primary_chunk_ids[
                work.envelope.primary_evidence_ids.index(evidence.evidence_id)
            ]
        ] = candidate.candidate_id
    batch = PolicyRangeBatch(
        schema_version="3",
        candidates=tuple(candidates),
        ranges=tuple(
            disposition.model_copy(
                update={
                    "outcome": "CANDIDATES",
                    "candidate_ids": (assignments[disposition.chunk_id],),
                }
            )
            if disposition.chunk_id in assignments
            else disposition
            for disposition in _no_facts(work).ranges
        ),
    )
    result = CandidatePipelineResult(
        classification="SUCCESS",
        candidates=tuple(
            PolicyCandidate(
                candidate_id=c.candidate_id,
                candidate_kind=c.candidate_kind,
                fields=c.fields,
                status="NEEDS_REVIEW"
                if review_rider and c.candidate_kind == "rider"
                else "AI_VERIFIED",
                issue_codes=("LOW_CONFIDENCE",)
                if review_rider and c.candidate_kind == "rider"
                else (),
                provider_request_ids=("synthetic-structure", "synthetic-verify"),
            )
            for c in candidates
        ),
    )
    ranges.save(job, WORKER, work, batch, result)
    return work


def _reextract(url: str, job: Any, *, reimport: bool = False) -> Any:
    extraction, item, next_job = uuid4(), uuid4(), uuid4()
    with psycopg.connect(_psycopg_url(url)) as connection:
        document_version = job.document_version_id
        document = connection.execute(
            "SELECT document_id FROM document_versions WHERE id=%s", (document_version,)
        ).fetchone()[0]
        if reimport:
            document, document_version = uuid4(), uuid4()
            connection.execute(
                "INSERT INTO documents(id,source_key,document_kind,status) "
                "VALUES (%s,%s,'policy','ready')",
                (document, f"synthetic-native-reimport/{job.household_space_id}/{document}"),
            )
            connection.execute(
                "INSERT INTO document_versions(id,document_id,version_number,content_sha256,"
                "byte_size,page_count) SELECT %s,%s,1,content_sha256,byte_size,page_count "
                "FROM document_versions WHERE id=%s",
                (document_version, document, job.document_version_id),
            )
        connection.execute(
            "INSERT INTO "
            "extractions(id,document_version_id,extractor_name,extractor_version,extractor_conf"
            "ig_hash,quality_rule_version,status,succeeded_at) SELECT "
            "%s,%s,extractor_name,'synthetic-native-reextract',%s,quality_rule"
            "_version,'succeeded',clock_timestamp() FROM extractions WHERE id=%s",
            (extraction, document_version, uuid4().hex * 2, job.extraction_id),
        )
        page = connection.execute(
            "INSERT INTO "
            "extraction_pages(extraction_id,page_number,width_points,height_points,non_whitespa"
            "ce_chars,alphanumeric_ratio,replacement_character_ratio,maximum_repeated_character"
            "_run,classification) SELECT "
            "%s,page_number,width_points,height_points,non_whitespace_chars,alphanumeric_ratio,"
            "replacement_character_ratio,maximum_repeated_character_run,classification FROM "
            "extraction_pages WHERE extraction_id=%s RETURNING id",
            (extraction, job.extraction_id),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO extraction_blocks(page_id,text,bbox,reading_order) SELECT "
            "%s,text,bbox,reading_order FROM extraction_blocks WHERE page_id IN (SELECT id "
            "FROM extraction_pages WHERE extraction_id=%s)",
            (page, job.extraction_id),
        )
        connection.execute(
            "INSERT INTO "
            "evidence(household_space_id,document_version_id,extraction_id,content_sha256,physi"
            "cal_page,review_state) SELECT "
            "household_space_id,%s,%s,content_sha256,1,'NEEDS_REVIEW' FROM "
            "evidence WHERE extraction_id=%s LIMIT 1",
            (document_version, extraction, job.extraction_id),
        )
        connection.execute(
            "INSERT INTO "
            "document_batch_items(id,batch_id,document_id,source_id,source_key,display_label,do"
            "cument_kind,state,available_at,completed_at) SELECT "
            "%s,batch_id,%s,%s,source_key,'Synthetic Native "
            "Reanalysis',document_kind,state,available_at,completed_at FROM "
            "document_batch_items WHERE id=%s",
            (item, document, uuid4().hex * 2, job.batch_item_id),
        )
        connection.execute(
            "INSERT INTO "
            "policy_structuring_jobs(id,household_space_id,batch_item_id,family_member_id,docum"
            "ent_version_id,extraction_id,state,pipeline_version,lease_owner,lease_expires_at,h"
            "eartbeat_at,attempts) VALUES "
            "(%s,%s,%s,%s,%s,%s,'running','synthetic-native-reextract',%s,clock_timestamp()+int"
            "erval '180 seconds',clock_timestamp(),1)",
            (
                next_job,
                job.household_space_id,
                item,
                job.family_member_id,
                document_version,
                extraction,
                WORKER,
            ),
        )
    return PolicyStructuringJobQueue(url).get_job(next_job)


@pytest.mark.parametrize(
    "new_row,proof_change",
    [
        (False, None),
        (True, None),
        (True, "unbound_evidence"),
        (True, "content_hash"),
        (True, "member"),
        (True, "deleted_member"),
        (True, "terms_link"),
    ],
)
def test_reimport_same_bytes_keeps_one_contract_and_reads_each_proven_rider(
    native_database: Any, new_row: bool, proof_change: str | None
) -> None:
    url, job = native_database
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
                "Another Rider sum assured: 619 KRW",
            ]
        ),
    )
    _retain_native(url, job)
    assert RangeEnrollmentProjector(url).project_pending() == 2
    scope = HouseholdScope(job.household_space_id)
    ledger = PolicyLedgerRepository(url)
    original = ledger.list_policies(scope)[0]
    first_rider = ledger.list_policy_riders(scope, original.id)[0]
    second = _reextract(url, job, reimport=True)
    assert second.document_version_id != job.document_version_id
    _retain_native(
        url,
        second,
        name="Another Rider" if new_row else "Sample Rider",
        amount=619 if new_row else 317,
    )
    assert RangeEnrollmentProjector(url).project_pending() == 2
    assert ledger.list_policies(scope) == [original]
    riders = ledger.list_policy_riders(scope, original.id)
    assert first_rider in riders
    assert len(riders) == (2 if new_row else 1)
    if new_row:
        added = next(r for r in riders if r.id != first_rider.id)
        assert added.source_evidence.document_version_id == second.document_version_id
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT count(DISTINCT s.job_id) FROM range_enrollment_publications p "
            "JOIN policy_range_candidate_sources s ON "
            "s.candidate_version_id=p.source_candidate_version_id WHERE p.policy_contract_id=%s",
            (original.id,),
        ).fetchone() == (2,)
    if proof_change == "terms_link":
        _confirm_reimport_terms_link(url, scope, original, added)
    elif proof_change is not None:
        from familycare_api.policies.errors import PolicyRepositoryUnavailable

        with psycopg.connect(_psycopg_url(url)) as connection:
            if proof_change == "unbound_evidence":
                unbound = connection.execute(
                    "INSERT INTO evidence(household_space_id,document_version_id,extraction_id,"
                    "content_sha256,physical_page,review_state) SELECT household_space_id,"
                    "document_version_id,extraction_id,content_sha256,physical_page,'AI_VERIFIED' "
                    "FROM evidence WHERE id=%s RETURNING id",
                    (added.source_evidence.evidence_id,),
                ).fetchone()[0]
                connection.execute(
                    "UPDATE riders SET source_evidence_id=%s WHERE id=%s", (unbound, added.id)
                )
            elif proof_change == "content_hash":
                connection.execute(
                    "UPDATE document_versions SET content_sha256=%s WHERE id=%s",
                    ("d" * 64, second.document_version_id),
                )
            elif proof_change == "deleted_member":
                connection.execute(
                    "UPDATE policy_parties SET deleted_at=clock_timestamp() "
                    "WHERE policy_contract_id=%s",
                    (original.id,),
                )
            else:
                connection.execute(
                    "DELETE FROM policy_parties WHERE policy_contract_id=%s", (original.id,)
                )
        with pytest.raises(PolicyRepositoryUnavailable):
            ledger.list_policy_riders(scope, original.id)


def _confirm_reimport_terms_link(url: str, scope: HouseholdScope, policy: Any, rider: Any) -> None:
    from datetime import date

    from familycare_api.clauses.repository import RiderClauseLinkRepository
    from psycopg.rows import dict_row

    from apps.api.tests.test_rider_clause_rules_integration import (
        _insert_candidate,
        _insert_document,
        _insert_evidence,
    )

    doc, version, extraction, evidence, edition, clause, candidate, link = [
        uuid4() for _ in range(8)
    ]
    try:
        with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
            _insert_document(
                connection,
                document_id=doc,
                document_version_id=version,
                extraction_id=extraction,
                source_key="synthetic-alias-terms/" + str(doc),
                document_kind="terms",
                content_sha256="b" * 64,
                pages=(1,),
            )
            _insert_evidence(
                connection,
                evidence_id=evidence,
                household_id=scope.household_space_id,
                document_version_id=version,
                extraction_id=extraction,
                content_sha256="b" * 64,
                page=1,
            )
            connection.execute(
                "UPDATE policy_contracts SET contract_date=%s WHERE id=%s",
                (date(2025, 1, 1), policy.id),
            )
            connection.execute(
                "INSERT INTO terms_editions(id,household_space_id,document_version_id,"
                "insurer_display,insurer_key,product_display,product_key,content_sha256,"
                "normalization_version) "
                "VALUES (%s,%s,%s,'Sample Insurer',%s,'Sample Plan',%s,%s,'unicode-nfc-v1')",
                (
                    edition,
                    scope.household_space_id,
                    version,
                    policy.insurer_key,
                    policy.product_key,
                    "b" * 64,
                ),
            )
            connection.execute(
                "INSERT INTO clauses(id,household_space_id,terms_edition_id,clause_type,label,"
                "normalized_title,normalized_text,physical_page_start,physical_page_end,"
                "normalization_version) "
                "VALUES (%s,%s,%s,'article','Article A','synthetic eligibility',"
                "'Synthetic clause body',1,1,'unicode-nfc-v1')",
                (clause, scope.household_space_id, edition),
            )
            connection.execute(
                "INSERT INTO clause_evidence(clause_id,evidence_id) VALUES (%s,%s)",
                (clause, evidence),
            )
            _insert_candidate(
                connection,
                candidate_id=candidate,
                review_item_id=uuid4(),
                household_id=scope.household_space_id,
                candidate_kind="rider_clause",
                aggregate_id=link,
                evidence=(
                    (
                        "rider_id",
                        rider.source_evidence.document_version_id,
                        rider.source_evidence.evidence_id,
                        rider.source_evidence.physical_page,
                    ),
                    ("clause_id", version, evidence, 1),
                ),
            )
            connection.execute(
                "INSERT INTO rider_clause_links(id,household_space_id,rider_id,terms_edition_id,"
                "clause_id,candidate_version_id,review_state,applicability_reason_code) "
                "VALUES (%s,%s,%s,%s,%s,%s,'AI_VERIFIED','APPLICABLE')",
                (link, scope.household_space_id, rider.id, edition, clause, candidate),
            )
            connection.execute(
                "INSERT INTO rider_clause_link_evidence(rider_clause_link_id,evidence_id) "
                "VALUES (%s,%s),(%s,%s)",
                (link, rider.source_evidence.evidence_id, link, evidence),
            )
        confirmed = RiderClauseLinkRepository(url).confirm(scope, link, expected_version=1)
        assert confirmed.review_state == "USER_CONFIRMED"
        assert {e.evidence_id for e in confirmed.evidence} == {
            rider.source_evidence.evidence_id,
            evidence,
        }
    finally:
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute(
                "DELETE FROM rider_clause_link_evidence WHERE rider_clause_link_id=%s", (link,)
            )
            connection.execute("DELETE FROM rider_clause_links WHERE id=%s", (link,))
            connection.execute("DELETE FROM clause_evidence WHERE clause_id=%s", (clause,))
            connection.execute("DELETE FROM clauses WHERE id=%s", (clause,))
            connection.execute("DELETE FROM terms_editions WHERE id=%s", (edition,))
            connection.execute("DELETE FROM analysis_candidate_versions WHERE id=%s", (candidate,))
            connection.execute("DELETE FROM evidence WHERE id=%s", (evidence,))
            connection.execute("DELETE FROM documents WHERE id=%s", (doc,))


def test_contract_correction_on_reimport_preserves_original_source_and_party(
    native_database: Any,
) -> None:
    from familycare_api.policies.candidate_models import CandidateCorrectionRequest
    from familycare_api.policies.candidate_repository import CandidateRepository

    url, job = native_database
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
    _retain_native(url, job)
    assert RangeEnrollmentProjector(url).project_pending() == 2
    scope = HouseholdScope(job.household_space_id)
    ledger = PolicyLedgerRepository(url)
    original = ledger.list_policies(scope)[0]
    second = _reextract(url, job, reimport=True)
    _retain_native(url, second)
    assert RangeEnrollmentProjector(url).project_pending() == 2
    repository = CandidateRepository(url)
    item = next(
        item
        for item in repository.list_review_items(scope, status="AI_VERIFIED")
        if item.candidate_kind == "policy_contract"
        and item.evidence[0].document_version_id == second.document_version_id
    )
    field = next(field for field in item.fields if field.field_id == "product_name")
    actor = uuid4()
    corrected = repository.correct_field(
        scope,
        request=CandidateCorrectionRequest(
            expected_version=item.expected_version,
            field_id="product_name",
            value="Corrected Sample Plan",
            evidence_id=field.evidence_ids[0],
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
    current = ledger.list_policies(scope)
    assert len(current) == 1 and current[0].id == original.id
    assert current[0].product_display == "Corrected Sample Plan"
    assert current[0].source_evidence == original.source_evidence
    assert ledger.list_policy_riders(scope, original.id)


@pytest.mark.parametrize("scenario", ["changed_content", "legacy_conflict"])
def test_reimport_does_not_merge_different_bytes_or_ambiguous_legacy_contracts(
    native_database: Any, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    from uuid import UUID

    from familycare_api.policies import range_enrollment

    url, job = native_database
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
    _retain_native(url, job)
    assert RangeEnrollmentProjector(url).project_pending() == 2
    second = _reextract(url, job, reimport=True)
    if scenario == "changed_content":
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute(
                "UPDATE document_versions SET content_sha256=%s WHERE id=%s",
                ("d" * 64, second.document_version_id),
            )
            connection.execute(
                "UPDATE evidence SET content_sha256=%s WHERE document_version_id=%s",
                ("d" * 64, second.document_version_id),
            )
        _retain_native(url, second)
        assert RangeEnrollmentProjector(url).project_pending() == 2
    else:
        # Reproduce the previous publication policy without rewriting immutable evidence.
        with monkeypatch.context() as patch:
            patch.setattr(
                range_enrollment,
                "_policy_identity",
                lambda connection, source: UUID(source["association_json"]["contract_scope_id"]),
            )
            _retain_native(url, second)
            assert RangeEnrollmentProjector(url).project_pending() == 2
    ledger = PolicyLedgerRepository(url)
    scope = HouseholdScope(job.household_space_id)
    originals = ledger.list_policies(scope)
    assert len(originals) == 2
    if scenario == "legacy_conflict":
        third = _reextract(url, job, reimport=True)
        _retain_native(url, third)
        assert RangeEnrollmentProjector(url).project_pending() == 0
        assert ledger.list_policies(scope) == originals


def test_unresolved_old_locator_does_not_block_another_contract_in_same_document(
    native_database: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from familycare_api.policies import range_enrollment

    url, job = native_database
    lines = [
        "Policy certificate",
        "Policy number: synthetic-policy-001",
        "Insured: Family Member A",
        "Sample Insurer Sample Plan",
        "Sample Rider sum assured: 317 KRW",
    ]
    _store_words(url, job, _words(lines))
    first = _retain_native(url, job)
    assert RangeEnrollmentProjector(url).project_pending() == 2
    second = _reextract(url, job)
    lines[1] = "Policy number: synthetic-policy-002"
    _store_words(url, second, _words(lines))
    _retain_native(url, second)
    original_locator = range_enrollment.contract_source_locator

    def legacy_locator(structure: Any, association: Any) -> Any:
        # Older retained structure can lack a currently supported locator.
        if structure["lineage"]["extraction_id"] == str(job.extraction_id):
            return None
        return original_locator(structure, association)

    assert first.generation_id is not None
    monkeypatch.setattr(range_enrollment, "contract_source_locator", legacy_locator)
    assert RangeEnrollmentProjector(url).project_pending() == 2
    assert (
        len(PolicyLedgerRepository(url).list_policies(HouseholdScope(job.household_space_id))) == 2
    )


def test_same_pdf_new_extraction_reuses_one_rider_and_preserves_both_sources(
    native_database: Any,
) -> None:
    url, job = native_database
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
    first = _retain_native(url, job)
    assert RangeEnrollmentProjector(url).project_pending() == 2
    scope = HouseholdScope(job.household_space_id)
    ledger = PolicyLedgerRepository(url)
    policy = ledger.list_policies(scope)[0]
    original = ledger.list_policy_riders(scope, policy.id)[0]
    second_job = _reextract(url, job)
    second = _retain_native(url, second_job)
    assert first.generation_id != second.generation_id
    assert RangeEnrollmentProjector(url).project_pending() == 2
    assert ledger.list_policy_riders(scope, policy.id) == [original]
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT count(DISTINCT s.job_id) FROM range_enrollment_publications p JOIN "
            "policy_range_candidate_sources s ON "
            "s.candidate_version_id=p.source_candidate_version_id WHERE p.rider_id=%s",
            (original.id,),
        ).fetchone() == (2,)


@pytest.mark.parametrize("change", ["direct_edit", "user_correction"])
@pytest.mark.parametrize("reimport", [False, True])
def test_reextraction_preserves_existing_user_changes(
    native_database: Any, change: str, reimport: bool
) -> None:
    from familycare_api.policies.candidate_models import CandidateCorrectionRequest
    from familycare_api.policies.candidate_repository import CandidateRepository

    url, job = native_database
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
    _retain_native(url, job)
    assert RangeEnrollmentProjector(url).project_pending() == 2
    scope = HouseholdScope(job.household_space_id)
    ledger = PolicyLedgerRepository(url)
    policy = ledger.list_policies(scope)[0]
    original = ledger.list_policy_riders(scope, policy.id)[0]
    if change == "direct_edit":
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute(
                "UPDATE riders SET insured_amount=619,version=version+1 WHERE id=%s", (original.id,)
            )
    else:
        repository = CandidateRepository(url)
        item = next(
            item
            for item in repository.list_review_items(scope, status="AI_VERIFIED")
            if item.candidate_kind == "rider"
        )
        actor = uuid4()
        name = next(field for field in item.fields if field.field_id == "rider_name")
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
    second = _reextract(url, job, reimport=reimport)
    _retain_native(url, second)
    assert RangeEnrollmentProjector(url).project_pending() == 1
    assert ledger.list_policy_riders(scope, policy.id) == expected


def test_same_name_at_distinct_physical_rows_remains_two_enrollments(native_database: Any) -> None:
    url, job = native_database
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
                "Sample Rider sum assured: 619 KRW",
            ]
        ),
    )
    _retain_native(url, job)
    assert RangeEnrollmentProjector(url).project_pending() == 2
    second = _reextract(url, job)
    _retain_native(url, second, position=1, amount=619)
    assert RangeEnrollmentProjector(url).project_pending() == 2
    ledger = PolicyLedgerRepository(url)
    scope = HouseholdScope(job.household_space_id)
    policy = ledger.list_policies(scope)[0]
    riders = ledger.list_policy_riders(scope, policy.id)
    assert len(riders) == 2 and {r.insured_amount for r in riders} == {317, 619}


def test_actual_pdf_extraction_storage_and_enrollment_use_the_same_word_lineage(
    native_database: Any, tmp_path: Path
) -> None:
    from familycare_worker.pdf.extractor import ExtractionSettings, PdfPlumberExtractor
    from familycare_worker.pdf.intake import open_source, validate_pdf
    from reportlab.pdfgen.canvas import Canvas

    url, job = native_database
    path = tmp_path / "synthetic-native-policy.pdf"
    canvas = Canvas(str(path), invariant=1)
    for index, line in enumerate(
        [
            "Policy certificate",
            "Policy number: synthetic-policy-001",
            "Insured: Family Member A",
            "Sample Insurer Sample Plan",
            "Sample Rider sum assured: 317 KRW",
        ]
    ):
        canvas.drawString(72, 720 - index * 20, line)
    canvas.save()
    with open_source(tmp_path, path.name) as opened:
        validated = validate_pdf(opened)
        extracted = PdfPlumberExtractor().extract(
            opened.fd,
            ExtractionSettings(
                document_version_id=str(job.document_version_id),
                content_sha256=validated.content_sha256,
                extractor_config_hash="c" * 64,
                quality_rule_version="quality-v1",
                table_strategy="lines",
            ),
        )
    page = extracted["pages"][0]
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE document_versions SET content_sha256=%s WHERE id=%s",
            (validated.content_sha256, job.document_version_id),
        )
        connection.execute(
            "UPDATE evidence SET content_sha256=%s WHERE document_version_id=%s",
            (validated.content_sha256, job.document_version_id),
        )
        connection.execute(
            "UPDATE extractions SET "
            "extractor_name=%s,extractor_version=%s,extractor_config_hash=%s WHERE id=%s",
            (
                extracted["extractor_name"],
                extracted["extractor_version"],
                extracted["extractor_config_hash"],
                job.extraction_id,
            ),
        )
        connection.execute(
            "UPDATE extraction_pages SET width_points=%s,height_points=%s WHERE extraction_id=%s",
            (page["width_points"], page["height_points"], job.extraction_id),
        )
    _store_words(url, job, page["blocks"])
    work = _retain_native(url, job)
    assert RangeEnrollmentProjector(url).project_pending() == 2
    scope = HouseholdScope(job.household_space_id)
    ledger = PolicyLedgerRepository(url)
    policy = ledger.list_policies(scope)[0]
    rider = ledger.list_policy_riders(scope, policy.id)[0]
    assert rider.insured_amount == 317 and rider.benefit_type == "unknown"
    with psycopg.connect(_psycopg_url(url)) as connection:
        structure = connection.execute(
            "SELECT structure_json FROM document_structure_generations WHERE id=%s",
            (work.generation_id,),
        ).fetchone()[0]
        assert structure["lineage"]["content_sha256"] == validated.content_sha256
        originals = {
            node["node_id"]: node for node in structure["nodes"] if node["kind"] == "BLOCK"
        }
        assert len(originals) == len(page["blocks"])
        assert all(
            span["block_node_id"] in originals
            for node in structure["nodes"]
            for span in node["source_spans"]
        )


def _add_native_table_view(url: str, job: Any) -> None:
    with psycopg.connect(_psycopg_url(url)) as connection:
        page = connection.execute(
            "SELECT id FROM extraction_pages WHERE extraction_id=%s", (job.extraction_id,)
        ).fetchone()[0]
        table = connection.execute(
            "INSERT INTO extraction_tables(page_id,bbox,metadata_json) VALUES (%s,%s,%s) "
            "RETURNING id",
            (page, Jsonb([10, 80, 180, 105]), Jsonb({"header_rows": [0]})),
        ).fetchone()[0]
        for row, column, text, box in (
            (0, 0, "Rider name", [10, 80, 59, 90]),
            (0, 1, "sum assured (KRW)", [60, 80, 180, 90]),
            (1, 0, "Sample Rider", [10, 95, 70, 105]),
            (1, 1, "317 KRW", [130, 95, 180, 105]),
        ):
            connection.execute(
                "INSERT INTO extraction_cells(table_id,row_index,column_index,text,bbox) "
                "VALUES (%s,%s,%s,%s,%s)",
                (table, row, column, text, Jsonb(box)),
            )


@pytest.mark.parametrize("first_table", [False, True])
def test_text_line_and_table_views_share_the_original_physical_enrollment(
    native_database: Any, first_table: bool
) -> None:
    url, job = native_database
    _store_words(
        url,
        job,
        _words(
            [
                "Policy certificate",
                "Policy number: synthetic-policy-001",
                "Insured: Family Member A",
                "Sample Insurer Sample Plan",
                "Rider name sum assured (KRW)",
                "Sample Rider sum assured: 317 KRW",
            ]
        ),
    )
    if first_table:
        _add_native_table_view(url, job)
    first = _retain_native(url, job)
    assert RangeEnrollmentProjector(url).project_pending() == 2
    second_job = _reextract(url, job)
    if not first_table:
        _add_native_table_view(url, second_job)
    second = _retain_native(url, second_job)
    assert RangeEnrollmentProjector(url).project_pending() == 2
    ledger = PolicyLedgerRepository(url)
    scope = HouseholdScope(job.household_space_id)
    policy = ledger.list_policies(scope)[0]
    riders = ledger.list_policy_riders(scope, policy.id)
    assert len(riders) == 1
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT count(DISTINCT source_candidate_version_id) FROM "
            "range_enrollment_publications WHERE rider_id=%s",
            (riders[0].id,),
        ).fetchone() == (2,)
        structures = [
            row[0]
            for row in connection.execute(
                "SELECT structure_json FROM document_structure_generations WHERE id=ANY(%s)",
                ([first.generation_id, second.generation_id],),
            )
        ]
        assert len(structures) == 2
        assert (
            sum(any(node["kind"] == "TABLE_ROW" for node in value["nodes"]) for value in structures)
            == 1
        )


@pytest.mark.parametrize("reimport", [False, True])
def test_existing_contract_is_not_rebound_to_another_member_after_name_changes(
    native_database: Any,
    reimport: bool,
) -> None:
    url, job = native_database
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
    _retain_native(url, job)
    assert RangeEnrollmentProjector(url).project_pending() == 2
    second = _reextract(url, job, reimport=reimport)
    member, batch = uuid4(), uuid4()
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE family_members SET display_name='Former Family Member' WHERE id=%s",
            (job.family_member_id,),
        )
        connection.execute(
            "INSERT INTO family_members(id,household_space_id,display_name,internal_alias) "
            "VALUES (%s,%s,'Family Member A','synthetic-reassigned-member')",
            (member, job.household_space_id),
        )
        connection.execute(
            "INSERT INTO "
            "document_batches(id,household_space_id,family_member_id,created_by,state) SELECT "
            "%s,household_space_id,%s,created_by,'created' FROM document_batches WHERE "
            "id=(SELECT batch_id FROM document_batch_items WHERE id=%s)",
            (batch, member, job.batch_item_id),
        )
        connection.execute(
            "UPDATE document_batch_items SET batch_id=%s WHERE id=%s", (batch, second.batch_item_id)
        )
        connection.execute(
            "UPDATE policy_structuring_jobs SET family_member_id=%s WHERE id=%s",
            (member, second.id),
        )
    second = PolicyStructuringJobQueue(url).get_job(second.id)
    try:
        _retain_native(url, second)
        assert RangeEnrollmentProjector(url).project_pending() == 0
        with psycopg.connect(_psycopg_url(url)) as connection:
            assert connection.execute(
                "SELECT DISTINCT family_member_id FROM policy_parties WHERE household_space_id=%s",
                (job.household_space_id,),
            ).fetchall() == [(job.family_member_id,)]
    finally:
        with psycopg.connect(_psycopg_url(url)) as connection:
            # Restore the synthetic batch link so the shared fixture owns cleanup.
            connection.execute(
                "UPDATE document_batch_items SET batch_id=(SELECT batch_id FROM "
                "document_batch_items WHERE id=%s) WHERE id=%s",
                (job.batch_item_id, second.batch_item_id),
            )
            connection.execute(
                "UPDATE policy_structuring_jobs SET family_member_id=%s WHERE id=%s",
                (job.family_member_id, second.id),
            )
            connection.execute("DELETE FROM document_batches WHERE id=%s", (batch,))


@pytest.mark.parametrize("reimport", [False, True])
def test_removed_primary_insured_blocks_later_automatic_publications(
    native_database: Any, reimport: bool
) -> None:
    url, job = native_database
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
    _retain_native(url, job)
    assert RangeEnrollmentProjector(url).project_pending() == 2
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE policy_parties SET deleted_at=clock_timestamp() WHERE household_space_id=%s",
            (job.household_space_id,),
        )
    second = _reextract(url, job, reimport=reimport)
    _retain_native(url, second)
    assert RangeEnrollmentProjector(url).project_pending() == 0
