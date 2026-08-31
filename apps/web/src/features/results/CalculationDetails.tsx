import type { BenefitCalculationResponse } from "../../api/generated";
import { reasonLabel } from "./resultPresentation";
import styles from "./Results.module.css";

function moneyLabel(
  value: { amount: string; currency: string } | null,
): string | null {
  return value ? `${value.amount} ${value.currency}` : null;
}

function statusLabel(status: BenefitCalculationResponse["status"]): string {
  if (status === "computed") return "계산된 참고 금액";
  if (status === "partial") return "일부 조건만 확인된 계산";
  return "추가 확인이 필요한 계산";
}

function kindLabel(kind: BenefitCalculationResponse["kind"]): string {
  return kind === "fixed" ? "정액형" : "실손형";
}

export function CalculationDetails({
  calculation,
  onOpenEvidence,
  riderLabel,
}: {
  calculation: BenefitCalculationResponse;
  onOpenEvidence: (evidenceIds: string[]) => void;
  riderLabel: string;
}) {
  const amounts = [
    ["확인된 금액", moneyLabel(calculation.confirmed)],
    ["추가 금액", moneyLabel(calculation.additional)],
    ["제외 금액", moneyLabel(calculation.excluded)],
    ["공제액", moneyLabel(calculation.deductible)],
    ["적용 한도", moneyLabel(calculation.applied_limit)],
  ] as const;
  const visibleAmounts = amounts.filter(([, value]) => value !== null);

  return (
    <article className={styles.calculationCard}>
      <div className={styles.cardHeading}>
        <div>
          <p className={styles.cardKicker}>서버 계산 결과</p>
          <strong>{riderLabel}</strong>
        </div>
        <span className={styles.calculationKind}>
          {kindLabel(calculation.kind)}
        </span>
      </div>
      <p className={styles.calculationStatus}>
        {statusLabel(calculation.status)}
      </p>
      {visibleAmounts.length > 0 ? (
        <dl className={styles.amountList}>
          {visibleAmounts.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className={styles.muted}>
          확정된 금액이 없습니다. 조건을 먼저 확인해 주세요.
        </p>
      )}
      {calculation.applied_rate ? (
        <p className={styles.detailLine}>
          적용 비율 {calculation.applied_rate}
        </p>
      ) : null}
      {calculation.hold_reason_codes.length > 0 ? (
        <ul className={styles.reasonList}>
          {calculation.hold_reason_codes.map((code) => (
            <li key={code}>{reasonLabel(code)}</li>
          ))}
        </ul>
      ) : null}
      {calculation.excluded_reason_codes.length > 0 ? (
        <ul className={styles.reasonList}>
          {calculation.excluded_reason_codes.map((code) => (
            <li key={code}>제외 사유를 확인해 주세요.</li>
          ))}
        </ul>
      ) : null}
      {calculation.steps.length > 0 ? (
        <ol className={styles.stepList} aria-label="서버 계산 단계">
          {calculation.steps.map((step) => (
            <li key={step.step_number}>
              <span>
                {step.step_number}. {step.operation}
              </span>
              <small>{reasonLabel(step.reason_code)}</small>
            </li>
          ))}
        </ol>
      ) : null}
      {calculation.evidence_ids.length > 0 ? (
        <button
          type="button"
          className={styles.quietButton}
          onClick={() => onOpenEvidence(calculation.evidence_ids)}
        >
          근거 보기 ({calculation.evidence_ids.length})
        </button>
      ) : null}
    </article>
  );
}
