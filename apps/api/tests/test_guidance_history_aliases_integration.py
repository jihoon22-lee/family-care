"""Reimported private claim sources retain only replayable enrollment identities."""

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from familycare_api.claims.repository import ClaimRepository
from familycare_api.insurance_reconciliation.canonical_repository import CanonicalLinkRepository
from familycare_api.insurance_reconciliation.source_bindings import (
    KnowledgeSourceBindingRepository,
    KnowledgeSourceManifest,
)
from familycare_api.private_knowledge.package import load_private_knowledge_package
from familycare_api.private_knowledge.publication_package import load_rule_publication_package
from familycare_api.private_knowledge.publication_repository import (
    PostgresRulePublicationRepository,
)
from familycare_api.private_knowledge.reconciliation import build_dry_run_report
from familycare_api.private_knowledge.repository import PostgresPrivateKnowledgeRepository
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests.private_knowledge_fixtures import (
    mutate_jsonl,
    write_synthetic_private_knowledge_package,
)
from apps.api.tests.private_knowledge_publication_fixtures import (
    bind_publication_package_to_knowledge,
    mutate_publication_jsonl,
    write_synthetic_rule_publication_package,
)
from apps.api.tests.test_guidance_claim_concurrency_integration import (
    LinkedPrivateClaim,
    _psycopg_url,
    enrollment_database,  # noqa: F401
    native_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_guidance_claim_concurrency_integration import (
    linked_private_claim as linked_private_claim,
)
from apps.api.tests.test_private_knowledge_decision_integration import ACTOR_ID

pytestmark = pytest.mark.integration


@pytest.fixture()
def reimported_claim(
    linked_private_claim: LinkedPrivateClaim, tmp_path: Path, request: pytest.FixtureRequest
) -> Iterator[tuple[LinkedPrivateClaim, UUID]]:
    linked = linked_private_claim
    saved = linked.saved
    scope = saved.scope
    if getattr(request, "param", None) == "paid":
        claims = ClaimRepository(saved.url)
        submitted = claims.transition_claim(
            scope,
            linked.original.id,
            target_status="submitted",
            expected_version=1,
            occurred_at=datetime(2025, 6, 17, tzinfo=UTC),
            metadata={},
        )
        claims.transition_claim(
            scope,
            linked.original.id,
            target_status="paid",
            expected_version=submitted["version"],
            occurred_at=datetime(2025, 6, 20, tzinfo=UTC),
            metadata={
                "amount": Decimal("0.5"),
                "currency": "KRW",
                "payment_date": date(2025, 6, 20),
            },
        )
    repository = PostgresPrivateKnowledgeRepository(saved.url)
    root = write_synthetic_private_knowledge_package(tmp_path / "second-knowledge")
    mutate_jsonl(root, "contracts.jsonl", lambda row: row.update(monthly_premium_krw=2000))
    if getattr(request, "param", None) == "unlinked":

        def unrelated_coverage(row):
            row["name"] = "Unrelated Synthetic Benefit"
            row["certificate_review"]["name"] = "Unrelated Synthetic Benefit"

        mutate_jsonl(root, "coverage-components.jsonl", unrelated_coverage)
    package = load_private_knowledge_package(root, repository_root=tmp_path / "repository")
    applied = repository.apply_snapshot(
        package,
        household_space_id=scope.household_space_id,
        actor_id=ACTOR_ID,
        approved_report=build_dry_run_report(
            package, repository.read_baseline(scope.household_space_id)
        ),
    )
    member = linked.service.get_medical_event(saved.event_id).family_member_id
    with psycopg.connect(_psycopg_url(saved.url), row_factory=dict_row) as connection:
        old = connection.execute(
            "SELECT b.* FROM private_knowledge_source_bindings b "
            "JOIN private_knowledge_coverages c ON c.import_run_id=b.import_run_id "
            "WHERE c.id=%s AND b.is_current",
            (saved.ref.coverage_id,),
        ).fetchone()
        assert old is not None and old["import_run_id"] != applied.run_id
        connection.execute(
            "UPDATE private_knowledge_subjects SET family_member_id=%s, "
            "binding_decision='MATCH',binding_conflict=false, "
            "binding_reason_code='USER_EXACT_BINDING',binding_confirmed_by=%s, "
            "binding_confirmed_at=clock_timestamp() WHERE import_run_id=%s",
            (member, ACTOR_ID, applied.run_id),
        )
        connection.execute(
            "INSERT INTO private_knowledge_contract_confirmations "
            "(import_run_id,household_space_id,knowledge_contract_id,decision,confirmed_status, "
            "status_as_of,authority,reason_code,confirmed_by,confirmed_at,is_current, "
            "confirmation_digest_sha256) SELECT import_run_id,household_space_id,id,'MATCH', "
            "'active',DATE '2025-01-01','USER_CONFIRMED_CURRENT_ENROLLMENT', "
            "'SYNTHETIC_CURRENT_CONFIRMED',%s,clock_timestamp(),true,%s "
            "FROM private_knowledge_contracts WHERE import_run_id=%s",
            (ACTOR_ID, "c" * 64, applied.run_id),
        )
        binding = connection.execute(
            "SELECT id,source_alias_digest_sha256 FROM private_knowledge_document_bindings "
            "WHERE import_run_id=%s AND source_alias='synthetic-certificate-source'",
            (applied.run_id,),
        ).fetchone()
        run = connection.execute(
            "SELECT package_digest_sha256,projection_digest_sha256 "
            "FROM private_knowledge_import_runs WHERE id=%s",
            (applied.run_id,),
        ).fetchone()
        new_coverage = connection.execute(
            "SELECT id FROM private_knowledge_coverages WHERE import_run_id=%s",
            (applied.run_id,),
        ).fetchone()["id"]
    assert new_coverage != saved.ref.coverage_id
    KnowledgeSourceBindingRepository(saved.url).apply_manifest(
        scope,
        KnowledgeSourceManifest.model_validate(
            {
                "schema_version": "knowledge-source-bindings-v1",
                "import_run_id": applied.run_id,
                "package_digest_sha256": run["package_digest_sha256"],
                "entries": [
                    {
                        "document_binding_id": binding["id"],
                        "source_alias_digest_sha256": binding["source_alias_digest_sha256"],
                        **{
                            field: old[field]
                            for field in (
                                "document_version_id",
                                "evidence_id",
                                "content_sha256",
                                "page_count",
                                "document_kind",
                            )
                        },
                        "expected_current_binding_id": None,
                    }
                ],
            }
        ),
    )
    publication_root = write_synthetic_rule_publication_package(tmp_path / "second-publication")
    mutate_publication_jsonl(
        publication_root,
        "contract-status-intervals.jsonl",
        lambda row: row.update(effective_from="2025-01-01", effective_through="2025-12-31"),
    )
    bind_publication_package_to_knowledge(publication_root, **run)
    publication = load_rule_publication_package(
        publication_root, repository_root=tmp_path / "repository"
    )
    publications = PostgresRulePublicationRepository(saved.url)
    publications.apply(
        publication,
        household_space_id=scope.household_space_id,
        actor_id=ACTOR_ID,
        approved_report=publications.prepare_dry_run(
            publication, household_space_id=scope.household_space_id
        ),
    )
    canonical = CanonicalLinkRepository(saved.url)
    unlinked = getattr(request, "param", None) == "unlinked"
    assert canonical.refresh(scope) == (0 if unlinked else 1)
    assert {item.knowledge_coverage_id for item in canonical.read_current(scope)} == (
        set() if unlinked else {new_coverage}
    )
    yield linked, new_coverage


def _aliases(linked: LinkedPrivateClaim, *, member: UUID | None = None):
    from familycare_api.insurance_reconciliation.claim_aliases import read_claim_coverage_aliases

    saved = linked.saved
    with psycopg.connect(_psycopg_url(saved.url), row_factory=dict_row) as connection:
        return read_claim_coverage_aliases(
            connection,
            saved.scope,
            family_member_id=member
            or linked.service.get_medical_event(saved.event_id).family_member_id,
            rider_ids=(linked.operational_ref.coverage_id,),
        )


@pytest.mark.parametrize("reimported_claim", [None, "paid"], indirect=True)
def test_reimported_claim_aliases_replay_after_ledger_correction(
    reimported_claim: tuple[LinkedPrivateClaim, UUID],
) -> None:
    linked, current_id = reimported_claim
    saved = linked.saved
    assert {item.knowledge_coverage_id for item in _aliases(linked)} == {
        saved.ref.coverage_id,
        current_id,
    }
    with psycopg.connect(_psycopg_url(saved.url)) as connection:
        old_rows = connection.execute(
            "SELECT fingerprint,proofs FROM private_knowledge_canonical_links "
            "WHERE knowledge_coverage_id=%s ORDER BY id",
            (saved.ref.coverage_id,),
        ).fetchall()
        connection.execute(
            "UPDATE riders SET insured_amount=619,version=version+1 WHERE id=%s",
            (linked.operational_ref.coverage_id,),
        )
    CanonicalLinkRepository(saved.url).refresh(saved.scope)
    aliases = _aliases(linked)
    assert {item.knowledge_coverage_id for item in aliases} == {saved.ref.coverage_id, current_id}
    assert all(item.field_value_conflict for item in aliases)
    with psycopg.connect(_psycopg_url(saved.url)) as connection:
        assert (
            connection.execute(
                "SELECT fingerprint,proofs FROM private_knowledge_canonical_links "
                "WHERE knowledge_coverage_id=%s ORDER BY id",
                (saved.ref.coverage_id,),
            ).fetchall()
            == old_rows
        )


@pytest.mark.parametrize("change", ["digest", "same_key_wrong_document", "member"])
def test_historical_aliases_reject_unproven_or_wrong_member_sources(
    reimported_claim: tuple[LinkedPrivateClaim, UUID], change: str
) -> None:
    linked, current_id = reimported_claim
    if change == "member":
        assert _aliases(linked, member=uuid4()) == ()
        return
    with psycopg.connect(_psycopg_url(linked.saved.url)) as connection:
        if change == "digest":
            connection.execute(
                "UPDATE private_knowledge_coverages SET source_record_digest_sha256=%s WHERE id=%s",
                ("0" * 64, linked.saved.ref.coverage_id),
            )
        else:
            source = connection.execute(
                "SELECT source_record_json FROM private_knowledge_coverages WHERE id=%s",
                (linked.saved.ref.coverage_id,),
            ).fetchone()[0]
            source["certificate_review"]["evidence_locations"][0]["document_alias"] = (
                "synthetic-terms-source"
            )
            digest = hashlib.sha256(
                json.dumps(
                    source, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            connection.execute(
                "UPDATE private_knowledge_coverages SET source_record_json=%s, "
                "source_record_digest_sha256=%s WHERE id=%s",
                (Jsonb(source), digest, linked.saved.ref.coverage_id),
            )
    assert {item.knowledge_coverage_id for item in _aliases(linked)} == {current_id}


@pytest.mark.parametrize("reimported_claim", ["unlinked"], indirect=True)
def test_historical_alias_survives_omitted_current_coverage(
    reimported_claim: tuple[LinkedPrivateClaim, UUID],
) -> None:
    linked, _ = reimported_claim
    assert CanonicalLinkRepository(linked.saved.url).read_current(linked.saved.scope) == ()
    assert {item.knowledge_coverage_id for item in _aliases(linked)} == {
        linked.saved.ref.coverage_id
    }


@pytest.mark.parametrize("budget", ["_MAX_REFERENCED_COVERAGES", "_MAX_HISTORICAL_RUNS"])
def test_historical_alias_budget_failure_is_explicit(
    reimported_claim: tuple[LinkedPrivateClaim, UUID], monkeypatch: pytest.MonkeyPatch, budget: str
) -> None:
    from familycare_api.insurance_reconciliation import claim_aliases
    from familycare_api.insurance_reconciliation.canonical_repository import CanonicalLinkError

    monkeypatch.setattr(claim_aliases, budget, 0)
    with pytest.raises(CanonicalLinkError):
        _aliases(reimported_claim[0])
