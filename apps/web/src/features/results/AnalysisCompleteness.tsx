import type { CoverageDecisionResponse } from "../../api/generated";
import { reasonLabel } from "./resultPresentation";
import styles from "./Results.module.css";

function completenessLabel(
  completeness: CoverageDecisionResponse["analysis_completeness"],
): string {
  if (completeness === "COMPLETE") return "확인 가능한 범위 분석 완료";
  if (completeness === "PARTIAL") return "일부 범위만 분석 완료";
  return "실행 규칙 분석 준비 중";
}

export function AnalysisCompleteness({
  result,
}: {
  result: CoverageDecisionResponse;
}) {
  const catalog = result.catalog_coverage;
  const enrolledButUnpublished =
    catalog.benefit_coverage_count > 0 &&
    catalog.published_coverage_count === 0;

  return (
    <section className={styles.group} aria-labelledby="analysis-scope-title">
      <div className={styles.groupHeading}>
        <p className={styles.groupKicker}>Analysis scope</p>
        <h2 id="analysis-scope-title">분석 범위</h2>
      </div>
      <div className={styles.scopePanel}>
        <strong>{completenessLabel(result.analysis_completeness)}</strong>
        {enrolledButUnpublished ? (
          <p className={styles.scopeWarning}>
            가입 담보는 확인됐지만 실행 규칙 검토가 완료되지 않았습니다.
          </p>
        ) : (
          <p className={styles.scopeCopy}>
            증권에서 확인한 가입 담보와 검토 완료된 실행 규칙을 서로 구분해
            표시합니다.
          </p>
        )}
        <dl className={styles.scopeGrid}>
          <div>
            <dt>구조화 계약</dt>
            <dd>{catalog.contract_count}개</dd>
          </div>
          <div>
            <dt>가입 담보</dt>
            <dd>{catalog.benefit_coverage_count}개</dd>
          </div>
          <div>
            <dt>규칙 검토 완료</dt>
            <dd>{catalog.published_coverage_count}개</dd>
          </div>
          <div>
            <dt>검토 대기</dt>
            <dd>{catalog.blocked_coverage_count}개</dd>
          </div>
        </dl>
        {result.source_failure_codes.length > 0 ? (
          <div className={styles.sourceFailures}>
            <strong>확인하지 못한 자료 범위</strong>
            <ul>
              {result.source_failure_codes.map((code) => (
                <li key={code}>{reasonLabel(code)}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </section>
  );
}
