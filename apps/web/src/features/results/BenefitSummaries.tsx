import type { CoverageDecisionResponse } from "../../api/generated";
import styles from "./Results.module.css";

function indemnityCopy(
  summary: CoverageDecisionResponse["indemnity_summary"],
): string {
  if (summary.status === "NONE") {
    return "이번 결과에 분리해 표시할 실손형 후보가 없습니다.";
  }
  if (summary.status === "CALCULATED") {
    return "실손 계산 상태를 확인했습니다. 정액형 조건부 합계에는 더하지 않습니다.";
  }
  return "실손 금액은 별도로 확인해야 합니다. 영수증과 자기부담 조건 없이는 금액을 확정하지 않습니다.";
}

export function BenefitSummaries({
  result,
}: {
  result: CoverageDecisionResponse;
}) {
  return (
    <>
      <section className={styles.group} aria-labelledby="fixed-summary-title">
        <div className={styles.groupHeading}>
          <p className={styles.groupKicker}>Fixed benefit ledger</p>
          <h2 id="fixed-summary-title">조건부 정액 합계</h2>
        </div>
        <p className={styles.summaryNotice}>
          조건이 일치하고 서버 계산이 완료된 정액형 후보만 통화별로 모았습니다.
          실제 지급액을 확정하는 합계가 아닙니다.
        </p>
        {result.conditional_fixed_subtotals.length > 0 ? (
          <div className={styles.subtotalGrid}>
            {result.conditional_fixed_subtotals.map((subtotal) => (
              <article className={styles.subtotalCard} key={subtotal.currency}>
                <p className={styles.cardKicker}>Conditional subtotal</p>
                <strong className={styles.subtotalAmount}>
                  {subtotal.amount} {subtotal.currency}
                </strong>
                <dl className={styles.subtotalCounts}>
                  <div>
                    <dt>계산 포함</dt>
                    <dd>{subtotal.calculated_candidate_count}개</dd>
                  </div>
                  <div>
                    <dt>금액 미확정</dt>
                    <dd>{subtotal.unresolved_candidate_count}개</dd>
                  </div>
                </dl>
              </article>
            ))}
          </div>
        ) : (
          <p className={styles.emptyGroup}>
            현재 서버가 계산한 조건부 정액 합계가 없습니다.
          </p>
        )}
      </section>

      <section
        className={styles.group}
        aria-labelledby="indemnity-summary-title"
      >
        <div className={styles.groupHeading}>
          <p className={styles.groupKicker}>Indemnity stays separate</p>
          <h2 id="indemnity-summary-title">실손 보장</h2>
        </div>
        <div className={styles.indemnityCard}>
          <strong>
            {result.indemnity_summary.status === "CALCULATED"
              ? "계산 상태 확인"
              : result.indemnity_summary.status === "UNKNOWN"
                ? "추가 자료 확인 필요"
                : "별도 후보 없음"}
          </strong>
          <p>{indemnityCopy(result.indemnity_summary)}</p>
          <dl className={styles.subtotalCounts}>
            <div>
              <dt>실손형 후보</dt>
              <dd>{result.indemnity_summary.candidate_count}개</dd>
            </div>
            <div>
              <dt>추가 확인</dt>
              <dd>{result.indemnity_summary.unresolved_candidate_count}개</dd>
            </div>
          </dl>
        </div>
      </section>
    </>
  );
}
