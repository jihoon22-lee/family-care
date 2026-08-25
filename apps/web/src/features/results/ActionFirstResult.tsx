import type {
  BenefitCalculationsResponse,
  CoverageDecisionResponse,
} from "../../api/generated";
import { CalculationDetails } from "./CalculationDetails";
import {
  PartialResultBanner,
  partialFailureCount,
  partialReasonCodes,
} from "./PartialResultBanner";
import { ResultGroup } from "./ResultGroup";
import { StaleResultBanner } from "./StaleResultBanner";
import styles from "./Results.module.css";

export function ActionFirstResult({
  calculations,
  onOpenEvidence,
  onReanalyze,
  onStartClaim,
  result,
  riderLabels,
}: {
  calculations?: BenefitCalculationsResponse;
  onOpenEvidence: (evidenceIds: string[]) => void;
  onReanalyze: () => void;
  onStartClaim: (riderId: string) => void;
  result: CoverageDecisionResponse;
  riderLabels?: Record<string, string>;
}) {
  const partialCount = partialFailureCount(
    result.candidates,
    result.evaluations,
  );
  const partialReasons = partialReasonCodes(
    result.candidates,
    result.evaluations,
  );
  const calculationValues = calculations?.calculations ?? [];
  const claimStartEnabled = !result.stale;
  const firstReviewCandidate = result.candidates.find(
    (candidate) => candidate.aggregate_result === "MATCH",
  );
  const firstReviewLabel = firstReviewCandidate
    ? (riderLabels?.[firstReviewCandidate.rider_id] ??
      firstReviewCandidate.rider_label ??
      `가입 담보 ${String.fromCharCode(65 + result.candidates.indexOf(firstReviewCandidate))}`)
    : null;

  return (
    <div className={styles.resultBody}>
      <section
        className={styles.actionSection}
        aria-labelledby="result-actions"
      >
        <div className={styles.groupHeading}>
          <p className={styles.groupKicker}>Next action</p>
          <h2 id="result-actions">지금 할 일</h2>
        </div>
        <p className={styles.actionLead}>
          결과는 보험금 지급을 확정하지 않습니다. 확인 가능한 근거를 열어 보고
          필요한 검토를 시작하세요.
        </p>
        {claimStartEnabled && firstReviewCandidate && firstReviewLabel ? (
          <button
            type="button"
            className={styles.primaryButton}
            onClick={() => onStartClaim(firstReviewCandidate.rider_id)}
          >
            {firstReviewLabel} 청구 검토 시작
          </button>
        ) : (
          <p className={styles.muted}>
            현재 바로 시작할 청구 검토 대상이 없습니다.
          </p>
        )}
        <PartialResultBanner
          count={partialCount}
          reasonCodes={partialReasons}
          onRetry={onReanalyze}
        />
        <StaleResultBanner result={result} onReanalyze={onReanalyze} />
      </section>

      <ResultGroup
        calculations={calculationValues}
        candidates={result.candidates}
        evaluations={result.evaluations}
        group="claim_review"
        claimStartEnabled={claimStartEnabled}
        onOpenEvidence={onOpenEvidence}
        onStartClaim={onStartClaim}
        riderLabels={riderLabels}
      />
      <ResultGroup
        calculations={calculationValues}
        candidates={result.candidates}
        evaluations={result.evaluations}
        group="needs_information"
        claimStartEnabled={claimStartEnabled}
        onOpenEvidence={onOpenEvidence}
        onStartClaim={onStartClaim}
        riderLabels={riderLabels}
      />
      <ResultGroup
        calculations={calculationValues}
        candidates={result.candidates}
        evaluations={result.evaluations}
        group="mismatch"
        claimStartEnabled={claimStartEnabled}
        onOpenEvidence={onOpenEvidence}
        onStartClaim={onStartClaim}
        riderLabels={riderLabels}
      />

      {calculationValues.filter(
        (calculation) =>
          !result.candidates.some(
            (candidate) =>
              candidate.candidate_id === calculation.claim_candidate_id,
          ),
      ).length > 0 ? (
        <section
          className={styles.group}
          aria-labelledby="unlinked-calculations"
        >
          <div className={styles.groupHeading}>
            <p className={styles.groupKicker}>Calculation detail</p>
            <h2 id="unlinked-calculations">계산 상세</h2>
          </div>
          <p className={styles.muted}>
            연결된 보장 항목을 확인할 수 없는 계산은 별도로 합산하지 않습니다.
          </p>
          <div className={styles.calculationStack}>
            {calculationValues
              .filter(
                (calculation) =>
                  !result.candidates.some(
                    (candidate) =>
                      candidate.candidate_id === calculation.claim_candidate_id,
                  ),
              )
              .map((calculation) => (
                <CalculationDetails
                  key={
                    calculation.calculation_id ?? calculation.rule_version_id
                  }
                  calculation={calculation}
                  onOpenEvidence={onOpenEvidence}
                  riderLabel="연결되지 않은 보장 항목"
                />
              ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
