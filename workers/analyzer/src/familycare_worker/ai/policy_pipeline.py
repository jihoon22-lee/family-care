"""Structurer → verifier → deterministic validator orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from familycare_worker.ai.provider import (
    AiProvider,
    EvidenceSlice,
    ProviderConfigurationError,
    ProviderValidationError,
    RetryableProviderError,
)
from familycare_worker.ai.schemas import (
    CandidatePipelineResult,
    IssueCode,
    PipelineClassification,
    PolicyCandidate,
    StructurerCandidate,
)
from familycare_worker.ai.structurer import (
    StructurerPayloadInvalid,
    structure_policy_candidate,
    structure_policy_candidate_batch,
)
from familycare_worker.ai.validator import validate_candidate
from familycare_worker.ai.verifier import (
    VerifierInventedField,
    VerifierPayloadInvalid,
    verify_policy_candidate,
)

_INVALID_CANDIDATE_ID = UUID("00000000-0000-4000-8000-000000000001")


def _result(
    classification: PipelineClassification,
    candidates: tuple[PolicyCandidate, ...] = (),
) -> CandidatePipelineResult:
    return CandidatePipelineResult(classification=classification, candidates=candidates)


def _candidate(
    source: StructurerCandidate,
    *,
    status: str,
    issues: tuple[IssueCode, ...],
    request_ids: tuple[str, ...],
) -> PolicyCandidate:
    return PolicyCandidate.model_validate(
        {
            "schema_version": "1",
            "candidate_id": source.candidate_id,
            "candidate_kind": source.candidate_kind,
            "status": status,
            "fields": source.fields,
            "issue_codes": issues,
            "provider_request_ids": request_ids,
        },
        strict=True,
    )


def _invalid_candidate(issue: IssueCode) -> PolicyCandidate:
    return PolicyCandidate(
        candidate_id=_INVALID_CANDIDATE_ID,
        candidate_kind="policy_contract",
        status="NEEDS_REVIEW",
        fields=(),
        issue_codes=(issue,),
        provider_request_ids=(),
    )


def _provider_error(error: BaseException) -> PipelineClassification:
    if isinstance(error, (RetryableProviderError, TimeoutError, ConnectionError)):
        return "RETRYABLE_PROVIDER_ERROR"
    if isinstance(error, ProviderConfigurationError):
        return "CONFIGURATION_ERROR"
    return "VALIDATION_ERROR"


def _verify_candidate(
    source: StructurerCandidate,
    *,
    evidence: Sequence[EvidenceSlice],
    provider: AiProvider,
    verifier_model: str,
    structurer_request_id: str,
) -> tuple[PolicyCandidate, PipelineClassification]:
    try:
        verified, verifier_request_id = verify_policy_candidate(
            candidate=source,
            evidence=evidence,
            provider=provider,
            model=verifier_model,
        )
    except VerifierInventedField:
        return (
            _candidate(
                source,
                status="NEEDS_REVIEW",
                issues=("INVENTED_FIELD",),
                request_ids=(structurer_request_id,),
            ),
            "NEEDS_REVIEW",
        )
    except VerifierPayloadInvalid:
        return (
            _candidate(
                source,
                status="NEEDS_REVIEW",
                issues=("UNSUPPORTED_STRUCTURE",),
                request_ids=(structurer_request_id,),
            ),
            "NEEDS_REVIEW",
        )
    except (
        ProviderValidationError,
        ProviderConfigurationError,
        RetryableProviderError,
        TimeoutError,
        ConnectionError,
    ) as error:
        return (
            _candidate(
                source,
                status="NEEDS_REVIEW",
                issues=("LOW_CONFIDENCE",),
                request_ids=(structurer_request_id,),
            ),
            _provider_error(error),
        )

    issues = validate_candidate(candidate=source, verifier=verified, evidence=evidence)
    if verified.decision == "rejected":
        status = "rejected"
    elif verified.decision == "needs_review" or issues:
        status = "NEEDS_REVIEW"
    else:
        status = "AI_VERIFIED"
    candidate = _candidate(
        source,
        status=status,
        issues=issues,
        request_ids=(structurer_request_id, verifier_request_id),
    )
    classification: PipelineClassification = (
        "SUCCESS" if status == "AI_VERIFIED" else "NEEDS_REVIEW"
    )
    return candidate, classification


def run_policy_pipeline(
    *,
    evidence: Sequence[EvidenceSlice],
    provider: AiProvider,
    structurer_model: str,
    verifier_model: str,
    schema_version: str = "1",
) -> CandidatePipelineResult:
    """Run the two AI stages and keep all authority in deterministic validation."""

    if (
        schema_version != "1"
        or not evidence
        or len(evidence) > 64
        or not structurer_model
        or not verifier_model
        or len({item.evidence_id for item in evidence}) != len(evidence)
    ):
        return _result("VALIDATION_ERROR", (_invalid_candidate("UNSUPPORTED_STRUCTURE"),))
    try:
        structured, structurer_request_id = structure_policy_candidate(
            evidence=evidence,
            provider=provider,
            model=structurer_model,
        )
    except StructurerPayloadInvalid:
        return _result("VALIDATION_ERROR", (_invalid_candidate("UNSUPPORTED_STRUCTURE"),))
    except (ProviderValidationError, ProviderConfigurationError, RetryableProviderError) as error:
        return _result(_provider_error(error))
    except (TimeoutError, ConnectionError) as error:
        return _result(_provider_error(error))

    candidate, classification = _verify_candidate(
        structured,
        evidence=evidence,
        provider=provider,
        verifier_model=verifier_model,
        structurer_request_id=structurer_request_id,
    )
    return _result(classification, (candidate,))


def run_policy_batch_pipeline(
    *,
    evidence: Sequence[EvidenceSlice],
    provider: AiProvider,
    structurer_model: str,
    verifier_model: str,
    schema_version: str = "2",
) -> CandidatePipelineResult:
    """Structure one policy plus bounded riders, then verify every candidate separately."""

    if (
        schema_version != "2"
        or not evidence
        or len(evidence) > 64
        or not structurer_model
        or not verifier_model
        or len({item.evidence_id for item in evidence}) != len(evidence)
    ):
        return _result("VALIDATION_ERROR")
    try:
        structured, structurer_request_id = structure_policy_candidate_batch(
            evidence=evidence,
            provider=provider,
            model=structurer_model,
        )
    except StructurerPayloadInvalid:
        return _result("VALIDATION_ERROR")
    except (ProviderValidationError, ProviderConfigurationError, RetryableProviderError) as error:
        return _result(_provider_error(error))
    except (TimeoutError, ConnectionError) as error:
        return _result(_provider_error(error))

    classifications: list[PipelineClassification] = []
    candidates: list[PolicyCandidate] = []
    for source in (structured.policy, *structured.riders):
        candidate, classification = _verify_candidate(
            source,
            evidence=evidence,
            provider=provider,
            verifier_model=verifier_model,
            structurer_request_id=structurer_request_id,
        )
        candidates.append(candidate)
        classifications.append(classification)
    priority = {
        "SUCCESS": 0,
        "NEEDS_REVIEW": 1,
        "VALIDATION_ERROR": 2,
        "RETRYABLE_PROVIDER_ERROR": 3,
        "CONFIGURATION_ERROR": 4,
    }
    overall = max(classifications, key=priority.__getitem__)
    return _result(overall, tuple(candidates))


__all__ = ["run_policy_batch_pipeline", "run_policy_pipeline"]
