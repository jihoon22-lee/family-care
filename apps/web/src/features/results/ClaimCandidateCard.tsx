import type {
  BenefitCalculationResponse,
  ClaimCandidateResponse,
  RuleEvaluationResponse,
} from "../../api/generated";
import { CalculationDetails } from "./CalculationDetails";
import {
  fieldLabel,
  reasonLabel,
  resultCopy,
  resultTechnicalLabel,
} from "./ResultGroup";
import styles from "./Results.module.css";

export function ClaimCandidateCard({
  candidate,
  claimStartEnabled,
  calculations,
  evaluations,
  label,
  onOpenEvidence,
  onStartClaim,
}: {
  candidate: ClaimCandidateResponse;
  claimStartEnabled: boolean;
  calculations: BenefitCalculationResponse[];
  evaluations: RuleEvaluationResponse[];
  label: string;
  onOpenEvidence: (evidenceIds: string[]) => void;
  onStartClaim: (riderId: string) => void;
}) {
  const evidenceIds = [
    ...new Set(
      evaluations.flatMap((evaluation) =>
        evaluation.evidence.map((item) => item.evidence_id),
      ),
    ),
  ];
  const missingFields = [
    ...new Set(evaluations.flatMap((evaluation) => evaluation.missing_fields)),
  ];
  const conflictingFields = [
    ...new Set(
      evaluations.flatMap((evaluation) => evaluation.conflicting_fields),
    ),
  ];
  const reasonCodes = [
    ...new Set([
      ...candidate.hold_reason_codes,
      ...evaluations
        .filter((evaluation) => evaluation.result !== "MATCH")
        .map((evaluation) => evaluation.reason_code),
    ]),
  ];
  const isReviewable = candidate.aggregate_result === "MATCH";

  return (
    <article className={styles.candidateCard}>
      <div className={styles.cardHeading}>
        <div>
          <p className={styles.cardKicker}>Enrolled Rider</p>
          <strong>{label}</strong>
        </div>
        <span
          className={`${styles.resultBadge} ${styles[`result${candidate.aggregate_result}`]}`}
          aria-label={`판정 ${resultTechnicalLabel(candidate.aggregate_result)}`}
        >
          {resultTechnicalLabel(candidate.aggregate_result)}
        </span>
      </div>
      <p className={styles.cardCopy}>
        {resultCopy(candidate.aggregate_result)}
      </p>

      {missingFields.length > 0 ? (
        <div className={styles.factNotice}>
          <strong>확인할 정보</strong>
          <ul>
            {missingFields.map((field) => (
              <li key={field}>{fieldLabel(field)}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {conflictingFields.length > 0 ? (
        <div className={styles.factNotice}>
          <strong>서로 다른 정보</strong>
          <ul>
            {conflictingFields.map((field) => (
              <li key={field}>{fieldLabel(field)}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {reasonCodes.length > 0 ? (
        <ul className={styles.reasonList}>
          {reasonCodes.map((code) => (
            <li key={code}>{reasonLabel(code)}</li>
          ))}
        </ul>
      ) : null}
      <dl className={styles.countList}>
        <div>
          <dt>필수 일치</dt>
          <dd>{candidate.required_match_count}</dd>
        </div>
        <div>
          <dt>추가 확인</dt>
          <dd>{candidate.required_unknown_count}</dd>
        </div>
        <div>
          <dt>조건 불일치</dt>
          <dd>{candidate.required_no_match_count}</dd>
        </div>
      </dl>
      <div className={styles.cardActions}>
        {claimStartEnabled && isReviewable ? (
          <button
            type="button"
            className={styles.primaryButton}
            onClick={() => onStartClaim(candidate.rider_id)}
          >
            청구 검토 시작
          </button>
        ) : null}
        {evidenceIds.length > 0 ? (
          <button
            type="button"
            className={styles.quietButton}
            onClick={() => onOpenEvidence(evidenceIds)}
          >
            근거 보기 ({evidenceIds.length})
          </button>
        ) : null}
      </div>
      {calculations.length > 0 ? (
        <div className={styles.calculationStack}>
          {calculations.map((calculation) => (
            <CalculationDetails
              key={calculation.calculation_id ?? calculation.rule_version_id}
              calculation={calculation}
              onOpenEvidence={onOpenEvidence}
              riderLabel={label}
            />
          ))}
        </div>
      ) : null}
    </article>
  );
}
