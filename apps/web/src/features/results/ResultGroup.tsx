import type {
  BenefitCalculationResponse,
  ClaimCandidateResponse,
  RuleEvaluationResponse,
} from "../../api/generated";
import { ClaimCandidateCard } from "./ClaimCandidateCard";
import { evaluationMatchesCandidate } from "./resultPresentation";
import styles from "./Results.module.css";

export type ResultGroupKey = "claim_review" | "needs_information" | "mismatch";

const CATALOG_ONLY_HOLD_REASONS = new Set([
  "COVERAGE_PUBLICATION_ADVISORY",
  "COVERAGE_PUBLICATION_BLOCKED",
]);

export interface ResultGroupProps {
  claimStartEnabled: boolean;
  calculations: BenefitCalculationResponse[];
  candidates: ClaimCandidateResponse[];
  evaluations: RuleEvaluationResponse[];
  group: ResultGroupKey;
  onOpenEvidence: (evidenceIds: string[]) => void;
  onStartClaim: (riderId: string) => void;
  riderLabels?: Record<string, string>;
}

export function resultGroupFor(
  result: ClaimCandidateResponse["aggregate_result"],
): ResultGroupKey {
  if (result === "MATCH") return "claim_review";
  if (result === "NO_MATCH") return "mismatch";
  return "needs_information";
}

export function groupTitle(group: ResultGroupKey): string {
  if (group === "claim_review") return "청구 검토 대상";
  if (group === "needs_information") return "추가 확인 필요";
  return "조건 불일치";
}

function emptyCopy(group: ResultGroupKey): string {
  if (group === "claim_review") return "현재 청구 검토 대상이 없습니다.";
  if (group === "needs_information") return "추가로 확인할 항목이 없습니다.";
  return "조건이 맞지 않는 항목이 없습니다.";
}

export function hasOnlyAllUnknownPrivateEvaluations(
  candidate: ClaimCandidateResponse,
  evaluations: RuleEvaluationResponse[],
): boolean {
  if (candidate.source.kind !== "PRIVATE_KNOWLEDGE_COVERAGE") {
    return false;
  }
  const candidateEvaluations = evaluations.filter((evaluation) =>
    evaluationMatchesCandidate(candidate, evaluation),
  );
  return (
    candidateEvaluations.length > 0 &&
    candidateEvaluations.every(
      (evaluation) =>
        evaluation.result === "UNKNOWN" &&
        evaluation.reason_code === "ALL_UNKNOWN",
    )
  );
}

export function isAllUnknownPrivateCandidate(
  candidate: ClaimCandidateResponse,
  evaluations: RuleEvaluationResponse[],
): boolean {
  return (
    candidate.aggregate_result === "UNKNOWN" &&
    hasOnlyAllUnknownPrivateEvaluations(candidate, evaluations)
  );
}

export function isHiddenPrivateCandidate(
  candidate: ClaimCandidateResponse,
  evaluations: RuleEvaluationResponse[],
): boolean {
  if (candidate.source.kind !== "PRIVATE_KNOWLEDGE_COVERAGE") {
    return false;
  }
  const candidateEvaluations = evaluations.filter((evaluation) =>
    evaluationMatchesCandidate(candidate, evaluation),
  );
  const catalogOnly = candidate.hold_reason_codes.some((code) =>
    CATALOG_ONLY_HOLD_REASONS.has(code),
  );
  if (catalogOnly && candidateEvaluations.length === 0) {
    return true;
  }
  return isAllUnknownPrivateCandidate(candidate, evaluations);
}

export function ResultGroup({
  claimStartEnabled,
  calculations,
  candidates,
  evaluations,
  group,
  onOpenEvidence,
  onStartClaim,
  riderLabels,
}: ResultGroupProps) {
  const grouped = candidates.filter(
    (candidate) =>
      resultGroupFor(candidate.aggregate_result) === group &&
      !isHiddenPrivateCandidate(candidate, evaluations),
  );

  return (
    <section className={styles.group} aria-labelledby={`result-group-${group}`}>
      <div className={styles.groupHeading}>
        <p className={styles.groupKicker}>Result group</p>
        <h2 id={`result-group-${group}`}>{groupTitle(group)}</h2>
      </div>
      {grouped.length === 0 ? (
        <p className={styles.emptyGroup}>{emptyCopy(group)}</p>
      ) : (
        <ul className={styles.candidateList} aria-label={groupTitle(group)}>
          {grouped.map((candidate) => {
            const candidateIndex = candidates.indexOf(candidate);
            return (
              <li key={candidate.candidate_id}>
                <ClaimCandidateCard
                  claimStartEnabled={claimStartEnabled}
                  candidate={candidate}
                  calculations={calculations.filter(
                    (calculation) =>
                      calculation.claim_candidate_id === candidate.candidate_id,
                  )}
                  evaluations={evaluations.filter((evaluation) =>
                    evaluationMatchesCandidate(candidate, evaluation),
                  )}
                  label={
                    candidate.source.kind === "OPERATIONAL_RIDER"
                      ? (riderLabels?.[candidate.source.rider_id] ??
                        candidate.coverage_label ??
                        `가입 담보 ${String.fromCharCode(65 + candidateIndex)}`)
                      : candidate.coverage_label
                  }
                  onOpenEvidence={onOpenEvidence}
                  onStartClaim={onStartClaim}
                />
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
