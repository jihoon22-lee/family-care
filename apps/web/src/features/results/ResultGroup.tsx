import type {
  BenefitCalculationResponse,
  ClaimCandidateResponse,
  RuleEvaluationResponse,
} from "../../api/generated";
import { ClaimCandidateCard } from "./ClaimCandidateCard";
import styles from "./Results.module.css";

export type ResultGroupKey = "claim_review" | "needs_information" | "mismatch";

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

export function resultCopy(
  result: ClaimCandidateResponse["aggregate_result"],
): string {
  if (result === "MATCH")
    return "확인된 조건이 규칙과 일치합니다. 청구 검토를 시작할 수 있습니다.";
  if (result === "NO_MATCH") return "확인된 조건과 규칙이 일치하지 않습니다.";
  return "필요한 정보를 더 확인해야 합니다.";
}

export function resultTechnicalLabel(
  result: ClaimCandidateResponse["aggregate_result"],
): string {
  return result;
}

export function fieldLabel(path: string): string {
  const labels: Record<string, string> = {
    "MedicalEvent.event_date": "사건일",
    "MedicalEvent.visit_date": "방문일",
    "MedicalEvent.classification": "상황 분류",
    "MedicalEvent.admission_days": "입원 일수",
    "PolicyContract.contract_start": "계약 시작일",
    "PolicyContract.contract_end": "계약 종료일",
    "Rider.status": "담보 상태",
    "ClaimHistory.counted_occurrence": "기존 청구 이력",
  };
  if (labels[path]) return labels[path];
  const short = path.includes(".")
    ? path.slice(path.lastIndexOf(".") + 1)
    : path;
  return short.replaceAll("_", " ");
}

export function reasonLabel(reasonCode: string): string {
  const labels: Record<string, string> = {
    EVIDENCE_UNAVAILABLE: "근거 문서를 확인할 수 없어 결과를 보류합니다.",
    RULE_READER_UNAVAILABLE:
      "보장 규칙을 불러오지 못했습니다. 다시 확인해 주세요.",
    RULE_RUNTIME_INVALID:
      "보장 규칙을 실행하지 못했습니다. 다시 확인해 주세요.",
    NO_EXECUTABLE_DECISION_RULE:
      "판정에 사용할 규칙이 없어 추가 확인이 필요합니다.",
    NO_EXECUTABLE_RULE: "확인 가능한 규칙이 없어 추가 확인이 필요합니다.",
    CONFLICTING_POLICY_SNAPSHOT:
      "계약 상태가 서로 달라 추가 확인이 필요합니다.",
  };
  return labels[reasonCode] ?? "판정에 필요한 조건을 확인할 수 없습니다.";
}

function emptyCopy(group: ResultGroupKey): string {
  if (group === "claim_review") return "현재 청구 검토 대상이 없습니다.";
  if (group === "needs_information") return "추가로 확인할 항목이 없습니다.";
  return "조건이 맞지 않는 항목이 없습니다.";
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
    (candidate) => resultGroupFor(candidate.aggregate_result) === group,
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
                  evaluations={evaluations.filter(
                    (evaluation) => evaluation.rider_id === candidate.rider_id,
                  )}
                  label={
                    riderLabels?.[candidate.rider_id] ??
                    candidate.rider_label ??
                    `가입 담보 ${String.fromCharCode(65 + candidateIndex)}`
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
