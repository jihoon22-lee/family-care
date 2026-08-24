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
from familycare_worker.ai.structurer import StructurerPayloadInvalid, structure_policy_candidate
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

    try:
        verified, verifier_request_id = verify_policy_candidate(
            candidate=structured,
            evidence=evidence,
            provider=provider,
            model=verifier_model,
        )
    except VerifierInventedField:
        candidate = _candidate(
            structured,
            status="NEEDS_REVIEW",
            issues=("INVENTED_FIELD",),
            request_ids=(structurer_request_id,),
        )
        return _result("NEEDS_REVIEW", (candidate,))
    except VerifierPayloadInvalid:
        candidate = _candidate(
            structured,
            status="NEEDS_REVIEW",
            issues=("UNSUPPORTED_STRUCTURE",),
            request_ids=(structurer_request_id,),
        )
        return _result("NEEDS_REVIEW", (candidate,))
    except (ProviderValidationError, ProviderConfigurationError, RetryableProviderError) as error:
        candidate = _candidate(
            structured,
            status="NEEDS_REVIEW",
            issues=("LOW_CONFIDENCE",),
            request_ids=(structurer_request_id,),
        )
        return _result(_provider_error(error), (candidate,))
    except (TimeoutError, ConnectionError) as error:
        candidate = _candidate(
            structured,
            status="NEEDS_REVIEW",
            issues=("LOW_CONFIDENCE",),
            request_ids=(structurer_request_id,),
        )
        return _result(_provider_error(error), (candidate,))

    issues = validate_candidate(candidate=structured, verifier=verified, evidence=evidence)
    if verified.decision == "rejected":
        status = "rejected"
    elif verified.decision == "needs_review" or issues:
        status = "NEEDS_REVIEW"
    else:
        status = "AI_VERIFIED"
    candidate = _candidate(
        structured,
        status=status,
        issues=issues,
        request_ids=(structurer_request_id, verifier_request_id),
    )
    classification: PipelineClassification = (
        "SUCCESS" if status == "AI_VERIFIED" else "NEEDS_REVIEW"
    )
    return _result(classification, (candidate,))


__all__ = ["run_policy_pipeline"]
