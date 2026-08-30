"""Synthetic acceptance proof for private publication, decisions, and assistance."""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from familycare_api.decisions.repository import DecisionRepository
from familycare_api.decisions.schemas import CoverageDecisionResponse
from familycare_api.decisions.service import DecisionService
from familycare_api.private_knowledge.confirmations import load_confirmation_manifest
from familycare_api.private_knowledge.package import load_private_knowledge_package
from familycare_api.private_knowledge.publication_package import (
    load_rule_publication_package,
)
from familycare_api.private_knowledge.publication_repository import (
    PostgresRulePublicationRepository,
)
from familycare_api.private_knowledge.reconciliation import build_dry_run_report
from familycare_api.private_knowledge.repository import (
    PostgresPrivateKnowledgeRepository,
)
from familycare_worker.ai.provider import (
    OpenAiResponsesAdapter,
    ProviderResponse,
    ProviderTimeoutError,
)
from familycare_worker.ai.recommender import RECOMMENDER_SCHEMA_NAME, recommender_schema
from familycare_worker.recommendation_jobs import PostgresRecommendationJobQueue
from familycare_worker.runner import RecommendationJobRunner
from psycopg.rows import dict_row

from apps.api.tests.private_knowledge_fixtures import (
    refresh_manifest,
    synthetic_reconciliation,
    synthetic_records,
    write_synthetic_private_knowledge_package,
)
from apps.api.tests.private_knowledge_publication_fixtures import (
    bind_publication_package_to_knowledge,
    refresh_publication_manifest,
    write_synthetic_rule_publication_package,
)
from apps.api.tests.test_decision_integration import (
    DecisionSeed,
    _psycopg_url,
    _reset_database,
    _seed,
)

pytestmark = pytest.mark.integration

ACTOR_ID = UUID("00000000-0000-4000-8000-000000008101")


@pytest.fixture()
def database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required")
    _reset_database(value)
    return value


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _write_private_json(path: Path, value: object) -> None:
    path.write_bytes(_canonical_json(value))
    path.chmod(0o600)


def _write_private_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_bytes(b"".join(_canonical_json(row) for row in rows))
    path.chmod(0o600)


def _write_acceptance_knowledge_package(root: Path) -> Path:
    write_synthetic_private_knowledge_package(root)
    records = copy.deepcopy(synthetic_records())
    base_coverage = records["coverage-components.jsonl"][0]
    base_mapping = records["coverage-terms-mappings.jsonl"][0]
    base_reference = records["terms-semantic-review.jsonl"][0]["coverage_references"][0]

    coverages: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    for index in range(1, 6):
        coverage_key = f"synthetic-coverage-{index:03d}"
        benefit_type = "fixed" if index < 5 else "indemnity"
        label = f"Sample Fixed Benefit {index}" if index < 5 else "Sample Indemnity Benefit"
        mapping = copy.deepcopy(base_mapping)
        mapping["canonical_rider_id"] = coverage_key
        coverage = copy.deepcopy(base_coverage)
        coverage.update(
            {
                "canonical_rider_id": coverage_key,
                "name": label,
                "benefit_type": benefit_type,
                "terms_mapping": copy.deepcopy(mapping),
            }
        )
        coverage["source_refs"][0]["local_rider_id"] = f"synthetic-local-rider-{index:03d}"
        review = coverage["certificate_review"]
        review.update(
            {
                "canonical_rider_id": coverage_key,
                "name": label,
                "candidate_benefit_type": benefit_type,
                "reviewed_benefit_type": benefit_type,
            }
        )
        reference = copy.deepcopy(base_reference)
        reference["canonical_rider_id"] = coverage_key
        coverages.append(coverage)
        mappings.append(mapping)
        references.append(reference)

    contract = records["contracts.jsonl"][0]
    contract["row_reconciliation"].update(
        {
            "benefit_coverages": 5,
            "canonical_components": 5,
            "certificate_rows_detected": 5,
        }
    )
    selected_evidence = records["policy-terms-pairings.jsonl"][0]["selected_evidence"][0]
    selected_evidence.update(
        {
            "rider_count": 5,
            "rider_exact_count": 5,
            "rider_overlap_count": 5,
        }
    )
    records["coverage-components.jsonl"] = coverages
    records["coverage-terms-mappings.jsonl"] = mappings
    records["terms-semantic-review.jsonl"][0]["coverage_references"] = references
    for name, rows in records.items():
        _write_private_jsonl(root / name, rows)

    reconciliation = synthetic_reconciliation()
    reconciliation.update(
        {
            "coverage_component_count": 5,
            "benefit_coverage_count": 5,
            "certificate_enrollment_match_count": 5,
            "coverage_section_match_count": 5,
            "current_status_unknown_count": 5,
        }
    )
    _write_private_json(root / "reconciliation.json", reconciliation)
    refresh_manifest(root)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["counts"] = reconciliation
    _write_private_json(manifest_path, manifest)
    return root


def _write_confirmation_manifest(
    root: Path,
    *,
    seed: DecisionSeed,
    package_digest: str,
    source_subject_key: str,
) -> Path:
    root.mkdir(mode=0o700)
    path = root / "confirmation.json"
    _write_private_json(
        path,
        {
            "schema_version": "private-knowledge-confirmation.sol-v1",
            "package_digest_sha256": package_digest,
            "household_space_id": str(seed.scope_a.household_space_id),
            "confirmed_by": str(ACTOR_ID),
            "status_as_of": "2025-01-01",
            "authority": "USER_CONFIRMED_CURRENT_ENROLLMENT",
            "subjects": [
                {
                    "source_subject_key": source_subject_key,
                    "family_member_id": str(seed.member_a),
                }
            ],
            "contracts": [
                {
                    "canonical_policy_id": "synthetic-policy-001",
                    "decision": "MATCH",
                    "confirmed_status": "active",
                    "reason_code": "SYNTHETIC_CURRENT_CONFIRMED",
                }
            ],
        },
    )
    return path


def _apply_knowledge_and_confirmation(
    database_url: str,
    seed: DecisionSeed,
    tmp_path: Path,
) -> tuple[str, str]:
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO app_users (
              id, household_space_id, username, display_name, password_hash
            ) VALUES (
              %s, %s, 'synthetic-acceptance-operator', 'Admin A',
              '$argon2id$synthetic'
            )
            """,
            (ACTOR_ID, seed.scope_a.household_space_id),
        )

    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    package = load_private_knowledge_package(
        _write_acceptance_knowledge_package(tmp_path / "knowledge-package"),
        repository_root=repository_root,
    )
    repository = PostgresPrivateKnowledgeRepository(database_url)
    applied = repository.apply_snapshot(
        package,
        household_space_id=seed.scope_a.household_space_id,
        actor_id=ACTOR_ID,
        approved_report=build_dry_run_report(
            package,
            repository.read_baseline(seed.scope_a.household_space_id),
        ),
    )
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        subject = connection.execute(
            """
            SELECT source_subject_key
            FROM private_knowledge_subjects
            WHERE import_run_id = %s AND household_space_id = %s
            """,
            (applied.run_id, seed.scope_a.household_space_id),
        ).fetchone()
    assert subject is not None
    confirmation = load_confirmation_manifest(
        _write_confirmation_manifest(
            tmp_path / "confirmation",
            seed=seed,
            package_digest=package.package_digest_sha256,
            source_subject_key=str(subject["source_subject_key"]),
        ),
        repository_root=repository_root,
    )
    report = repository.prepare_confirmation_dry_run(confirmation)
    assert report.operation == "APPLY"
    confirmed = repository.apply_confirmations(confirmation, approved_report=report)
    no_op = repository.prepare_confirmation_dry_run(confirmation)
    assert no_op.operation == "NO_OP"
    assert repository.apply_confirmations(confirmation, approved_report=no_op) == confirmed

    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        digests = connection.execute(
            """
            SELECT package_digest_sha256, projection_digest_sha256
            FROM private_knowledge_import_runs
            WHERE id = %s AND household_space_id = %s
            """,
            (applied.run_id, seed.scope_a.household_space_id),
        ).fetchone()
    assert digests is not None
    return str(digests["package_digest_sha256"]), str(digests["projection_digest_sha256"])


def _rule(
    base: dict[str, Any],
    *,
    number: int,
    coverage_number: int,
    field: str,
    operation: str = "equals",
) -> dict[str, Any]:
    row = copy.deepcopy(base)
    rule_key = f"synthetic-rule-{number:03d}"
    citation_key = f"synthetic-rule-citation-{number:03d}"
    reason_code = f"SYNTHETIC_RULE_{number:03d}_MATCH"
    expression: dict[str, object] = {"op": operation, "field": field}
    if operation == "equals":
        expression["value"] = (
            "sample_category" if field == "MedicalEvent.classification" else "extended_sample"
        )
    row.update(
        {
            "rule_key": rule_key,
            "canonical_coverage_id": f"synthetic-coverage-{coverage_number:03d}",
            "rule_kind": "required_document" if operation == "present" else "eligibility",
            "result_reason_code": reason_code,
        }
    )
    row["rule_document"].update(
        {
            "rule_kind": row["rule_kind"],
            "input_field_paths": [field],
            "expression": expression,
            "result_reason_code": reason_code,
            "evidence_ids": [citation_key],
        }
    )
    return row


def _rule_citation(
    base: dict[str, Any],
    *,
    number: int,
    coverage_number: int,
) -> dict[str, Any]:
    row = copy.deepcopy(base)
    row.update(
        {
            "citation_key": f"synthetic-rule-citation-{number:03d}",
            "rule_key": f"synthetic-rule-{number:03d}",
            "canonical_coverage_id": f"synthetic-coverage-{coverage_number:03d}",
        }
    )
    return row


def _write_acceptance_publication_package(
    root: Path,
    *,
    package_digest: str,
    projection_digest: str,
) -> Path:
    write_synthetic_rule_publication_package(root)

    def rows(name: str) -> list[dict[str, Any]]:
        return [json.loads(line) for line in (root / name).read_text(encoding="utf-8").splitlines()]

    base_disposition = rows("coverage-dispositions.jsonl")[0]
    dispositions = []
    for index in range(1, 6):
        disposition = copy.deepcopy(base_disposition)
        disposition["canonical_coverage_id"] = f"synthetic-coverage-{index:03d}"
        disposition["benefit_type"] = "FIXED" if index < 5 else "INDEMNITY"
        dispositions.append(disposition)

    status = rows("contract-status-intervals.jsonl")[0]
    status.update({"effective_from": "2025-01-01", "effective_through": "2025-12-31"})
    base_rule = rows("rule-publications.jsonl")[0]
    rule_rows = [
        _rule(
            base_rule,
            number=index,
            coverage_number=index,
            field=(
                "MedicalEvent.classification" if index < 3 else "MedicalEvent.treatment_context"
            ),
        )
        for index in range(1, 6)
    ]
    rule_rows.append(
        _rule(
            base_rule,
            number=6,
            coverage_number=5,
            field="Receipt.covered_amount",
            operation="present",
        )
    )
    base_rule_citation = rows("rule-citations.jsonl")[0]
    rule_citations = [
        _rule_citation(
            base_rule_citation,
            number=index,
            coverage_number=(index if index < 6 else 5),
        )
        for index in range(1, 7)
    ]

    base_calculation = rows("calculation-publications.jsonl")[0]
    base_calculation_citation = rows("calculation-citations.jsonl")[0]
    calculation_rows = []
    calculation_citations = []
    for index in range(1, 5):
        calculation = copy.deepcopy(base_calculation)
        calculation_key = f"synthetic-calculation-{index:03d}"
        calculation_citation_key = f"synthetic-calculation-citation-{index:03d}"
        calculation.update(
            {
                "calculation_key": calculation_key,
                "canonical_coverage_id": f"synthetic-coverage-{index:03d}",
            }
        )
        calculation["calculation_document"]["evidence_ids"] = [calculation_citation_key]
        citation = copy.deepcopy(base_calculation_citation)
        citation.update(
            {
                "citation_key": calculation_citation_key,
                "calculation_key": calculation_key,
                "canonical_coverage_id": f"synthetic-coverage-{index:03d}",
            }
        )
        calculation_rows.append(calculation)
        calculation_citations.append(citation)

    counts = {
        "subject_count": 1,
        "contract_count": 1,
        "coverage_count": 5,
        "disposition_count": 5,
        "published_disposition_count": 5,
        "blocked_disposition_count": 0,
        "not_applicable_disposition_count": 0,
        "status_interval_count": 1,
        "fact_normalizer_count": 1,
        "rule_publication_count": 6,
        "rule_citation_count": 6,
        "calculation_publication_count": 4,
        "calculation_citation_count": 4,
    }
    _write_private_jsonl(root / "coverage-dispositions.jsonl", dispositions)
    _write_private_jsonl(root / "contract-status-intervals.jsonl", [status])
    _write_private_jsonl(root / "rule-publications.jsonl", rule_rows)
    _write_private_jsonl(root / "rule-citations.jsonl", rule_citations)
    _write_private_jsonl(root / "calculation-publications.jsonl", calculation_rows)
    _write_private_jsonl(root / "calculation-citations.jsonl", calculation_citations)
    _write_private_json(root / "reconciliation.json", counts)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["counts"] = counts
    _write_private_json(manifest_path, manifest)
    refresh_publication_manifest(root)
    bind_publication_package_to_knowledge(
        root,
        package_digest_sha256=package_digest,
        projection_digest_sha256=projection_digest,
    )
    return root


def _apply_publication(
    database_url: str,
    seed: DecisionSeed,
    tmp_path: Path,
    *,
    package_digest: str,
    projection_digest: str,
) -> UUID:
    repository_root = tmp_path / "repository"
    package = load_rule_publication_package(
        _write_acceptance_publication_package(
            tmp_path / "publication-package",
            package_digest=package_digest,
            projection_digest=projection_digest,
        ),
        repository_root=repository_root,
    )
    repository = PostgresRulePublicationRepository(database_url)
    report = repository.prepare_dry_run(
        package,
        household_space_id=seed.scope_a.household_space_id,
    )
    assert report.operation == "CREATE"
    applied = repository.apply(
        package,
        household_space_id=seed.scope_a.household_space_id,
        actor_id=ACTOR_ID,
        approved_report=report,
    )
    no_op = repository.prepare_dry_run(
        package,
        household_space_id=seed.scope_a.household_space_id,
    )
    assert no_op.operation == "NO_OP"
    repeated = repository.apply(
        package,
        household_space_id=seed.scope_a.household_space_id,
        actor_id=ACTOR_ID,
        approved_report=no_op,
    )
    assert repeated.run_id == applied.run_id
    return applied.run_id


def _create_updated_event(
    service: DecisionService,
    seed: DecisionSeed,
    facts: Mapping[str, object],
) -> Any:
    event = service.create_medical_event(
        family_member_id=seed.member_a,
        mode="post_treatment",
        situation="Synthetic sample category phrase treatment event.",
        event_date=date(2025, 6, 15),
        visit_date=date(2025, 6, 16),
    )
    return service.update_medical_event(
        event.id,
        expected_version=event.version,
        facts=facts,
        confirmation={key: "user" for key in facts},
    )


def _verified_projection(value: Any) -> dict[str, Any]:
    result = CoverageDecisionResponse.from_value(value).model_dump(mode="json")
    result.pop("assistance")
    return result


def _recommendation_projection(value: Any) -> list[tuple[object, ...]]:
    assert value.assistance is not None
    return [
        (
            item.contract_label,
            item.coverage_label,
            item.clause_label,
            item.excerpt,
            item.page_start,
            item.page_end,
            item.citation_kind,
        )
        for item in value.assistance.recommendations
    ]


def _assert_private_result(
    value: Any,
    *,
    fixed_match_count: int,
    subtotal: Decimal,
) -> None:
    knowledge = value.knowledge_result
    assert knowledge is not None
    assert len(knowledge.candidates) == 5
    assert len(knowledge.evaluations) == 6
    assert all(
        evaluation.citations
        and all(
            citation.page_start == 2 and citation.page_end == 2 for citation in evaluation.citations
        )
        for evaluation in knowledge.evaluations
    )
    assert (
        len(
            [
                candidate
                for candidate in knowledge.candidates
                if candidate.benefit_type == "FIXED" and candidate.result == "MATCH"
            ]
        )
        == fixed_match_count
    )
    assert len(knowledge.fixed_subtotals) == 1
    assert knowledge.fixed_subtotals[0].currency == "KRW"
    assert knowledge.fixed_subtotals[0].amount == subtotal
    assert knowledge.indemnity_summary.status == "UNKNOWN"
    assert knowledge.indemnity_summary.unresolved_candidate_count == 1


class _ReverseProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, **kwargs: object) -> ProviderResponse:
        self.calls += 1
        payload = kwargs["input_payload"]
        assert isinstance(payload, Mapping)
        candidates = payload["candidates"]
        assert isinstance(candidates, list)
        return ProviderResponse(
            payload={
                "schema_version": "1",
                "recommendations": [
                    {
                        "token": candidate["token"],
                        "explanation_code": "RELATED_CLAUSE",
                        "question_code": None,
                    }
                    for candidate in reversed(candidates)
                ],
            },
            request_id="synthetic-request-acceptance",
        )


class _TimeoutProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, **kwargs: object) -> ProviderResponse:
        self.calls += 1
        raise ProviderTimeoutError


def test_private_publication_decisions_and_optional_assistance_are_complete_and_isolated(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = _seed(database_url)
    package_digest, projection_digest = _apply_knowledge_and_confirmation(
        database_url,
        seed,
        tmp_path,
    )
    publication_run_id = _apply_publication(
        database_url,
        seed,
        tmp_path,
        package_digest=package_digest,
        projection_digest=projection_digest,
    )
    service = DecisionService(seed.scope_a, DecisionRepository(database_url))
    queue = PostgresRecommendationJobQueue(database_url)

    first_event = _create_updated_event(
        service,
        seed,
        {"MedicalEvent.classification": "sample_category"},
    )
    first = service.analyze_medical_event(first_event.id)
    _assert_private_result(first, fixed_match_count=2, subtotal=Decimal("2"))
    assert first.assistance is not None
    first_verified = _verified_projection(first)
    first_recommendations = _recommendation_projection(first)

    factory_calls: list[str] = []
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    missing_provider = OpenAiResponsesAdapter(
        {RECOMMENDER_SCHEMA_NAME: recommender_schema()},
        client_factory=lambda key: factory_calls.append(key),  # type: ignore[arg-type,return-value]
    )
    missing_runner = RecommendationJobRunner(queue=queue, provider=missing_provider)
    assert missing_runner.run_once("synthetic-worker") is True
    assert missing_runner.run_once("synthetic-worker") is False
    assert factory_calls == []
    first_loaded = service.get_decision_result(first_event.id, first_event.version)
    assert first_loaded.assistance is not None
    assert first_loaded.assistance.mode == "STRUCTURED_SEARCH"
    assert first_loaded.assistance.state == "SEARCH_READY"
    assert _recommendation_projection(first_loaded) == first_recommendations
    assert _verified_projection(first_loaded) == first_verified

    second_event = _create_updated_event(
        service,
        seed,
        {
            "MedicalEvent.classification": "sample_category",
            "MedicalEvent.treatment_context": "extended_sample",
        },
    )
    second = service.analyze_medical_event(second_event.id)
    _assert_private_result(second, fixed_match_count=4, subtotal=Decimal("4"))
    second_verified = _verified_projection(second)
    second_recommendations = _recommendation_projection(second)
    provider = _ReverseProvider()
    success_runner = RecommendationJobRunner(queue=queue, provider=provider)
    assert success_runner.run_once("synthetic-worker") is True
    assert success_runner.run_once("synthetic-worker") is False
    assert provider.calls == 1
    second_loaded = service.get_decision_result(second_event.id, second_event.version)
    assert second_loaded.assistance is not None
    assert second_loaded.assistance.mode == "LLM_ASSISTED"
    assert second_loaded.assistance.state == "LLM_READY"
    assert _recommendation_projection(second_loaded) == list(reversed(second_recommendations))
    assert _verified_projection(second_loaded) == second_verified

    third_event = _create_updated_event(
        service,
        seed,
        {
            "MedicalEvent.classification": "sample_category",
            "MedicalEvent.treatment_context": "extended_sample",
        },
    )
    third = service.analyze_medical_event(third_event.id)
    third_verified = _verified_projection(third)
    third_recommendations = _recommendation_projection(third)
    timeout_provider = _TimeoutProvider()
    timeout_runner = RecommendationJobRunner(queue=queue, provider=timeout_provider)
    assert timeout_runner.run_once("synthetic-worker") is True
    assert timeout_runner.run_once("synthetic-worker") is False
    assert timeout_provider.calls == 1
    third_loaded = service.get_decision_result(third_event.id, third_event.version)
    assert third_loaded.assistance is not None
    assert third_loaded.assistance.mode == "STRUCTURED_SEARCH"
    assert third_loaded.assistance.state == "SEARCH_READY"
    assert _recommendation_projection(third_loaded) == third_recommendations
    assert _verified_projection(third_loaded) == third_verified

    with pytest.raises(RuntimeError):
        DecisionService(seed.scope_b, DecisionRepository(database_url)).get_decision_result(
            second_event.id,
            second_event.version,
        )
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        counts = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM private_knowledge_rule_import_runs
               WHERE household_space_id = %(other)s) AS other_publications,
              (SELECT count(*) FROM private_knowledge_import_runs
               WHERE household_space_id = %(other)s) AS other_knowledge,
              (SELECT count(*) FROM private_knowledge_claim_candidates
               WHERE household_space_id = %(other)s) AS other_candidates,
              (SELECT count(*) FROM private_knowledge_rule_import_runs
               WHERE household_space_id = %(current)s AND is_current) AS current_publications
            """,
            {
                "other": seed.scope_b.household_space_id,
                "current": seed.scope_a.household_space_id,
            },
        ).fetchone()
    assert counts == {
        "other_publications": 0,
        "other_knowledge": 0,
        "other_candidates": 0,
        "current_publications": 1,
    }
    assert publication_run_id is not None
