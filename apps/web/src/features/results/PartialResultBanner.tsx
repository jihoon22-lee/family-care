import type {
  ClaimCandidateResponse,
  RuleEvaluationResponse,
} from "../../api/generated";
import {
  candidateSourceKey,
  evaluationSourceKey,
  reasonLabel,
} from "./resultPresentation";
import styles from "./Results.module.css";

const PARTIAL_REASON_PREFIXES = ["RULE_", "EVIDENCE_", "KNOWLEDGE_"];

function isPartialReason(reasonCode: string): boolean {
  return PARTIAL_REASON_PREFIXES.some((prefix) =>
    reasonCode.startsWith(prefix),
  );
}

export function partialFailureCount(
  candidates: ClaimCandidateResponse[],
  evaluations: RuleEvaluationResponse[],
): number {
  const candidateBySource = new Map(
    candidates.map((candidate) => [
      candidateSourceKey(candidate),
      candidate.candidate_id,
    ]),
  );
  const failedCandidateIds = new Set<string>();
  for (const candidate of candidates) {
    if (candidate.hold_reason_codes.some(isPartialReason)) {
      failedCandidateIds.add(candidate.candidate_id);
    }
  }
  for (const evaluation of evaluations) {
    if (!isPartialReason(evaluation.reason_code)) continue;
    const candidateId = candidateBySource.get(evaluationSourceKey(evaluation));
    if (candidateId) failedCandidateIds.add(candidateId);
  }
  return failedCandidateIds.size;
}

export function partialReasonCodes(
  candidates: ClaimCandidateResponse[],
  evaluations: RuleEvaluationResponse[],
): string[] {
  const reasons = new Set<string>();
  for (const candidate of candidates) {
    for (const reason of candidate.hold_reason_codes) {
      if (isPartialReason(reason)) reasons.add(reason);
    }
  }
  for (const evaluation of evaluations) {
    if (isPartialReason(evaluation.reason_code))
      reasons.add(evaluation.reason_code);
  }
  return [...reasons];
}

export function PartialResultBanner({
  count,
  reasonCodes,
  onRetry,
}: {
  count: number;
  reasonCodes: string[];
  onRetry: () => void;
}) {
  if (count === 0) return null;
  return (
    <div className={styles.partialBanner} role="status" aria-live="polite">
      <strong>{count}개 항목을 다시 확인할 수 있습니다</strong>
      <p>
        {reasonCodes.length > 0
          ? reasonLabel(reasonCodes[0])
          : "일부 결과를 확인하지 못했습니다. 다시 시도해 주세요."}
      </p>
      <button
        type="button"
        className={styles.secondaryButton}
        onClick={onRetry}
      >
        다시 확인
      </button>
    </div>
  );
}
