import type { AnalysisAssistanceResponse } from "../../api/generated";
import { pageLabel, reasonLabel } from "./resultPresentation";
import styles from "./Results.module.css";

function modeLabel(mode: AnalysisAssistanceResponse["mode"]): string {
  if (mode === "LLM_ASSISTED") return "LLM 보조";
  if (mode === "STRUCTURED_SEARCH") return "DB 검색";
  return "추천 없음";
}

export function RelatedClauseRecommendations({
  assistance,
}: {
  assistance: AnalysisAssistanceResponse;
}) {
  return (
    <section className={styles.group} aria-labelledby="recommendations-title">
      <div className={styles.groupHeadingWithBadge}>
        <div className={styles.groupHeadingText}>
          <p className={styles.groupKicker}>Related clause review</p>
          <h2 id="recommendations-title">관련 약관 추천</h2>
        </div>
        <span className={styles.modeBadge}>{modeLabel(assistance.mode)}</span>
      </div>
      <p className={styles.recommendationDisclaimer}>
        아래 항목은 검토 후보이며 보험금 지급 판정이 아닙니다. 실제 판정과
        계산은 위의 증권·약관 근거가 연결된 담보 카드에서 확인하세요.
      </p>
      {assistance.state === "LLM_PENDING" ? (
        <p
          className={styles.assistancePending}
          role="status"
          aria-live="polite"
        >
          DB 검색 결과를 먼저 표시합니다. LLM 추천은 제한된 시간 동안 이
          구역에서만 확인하며, 완료되지 않아도 DB 결과를 계속 사용할 수
          있습니다.
        </p>
      ) : null}
      {assistance.recommendations.length > 0 ? (
        <ol className={styles.recommendationList}>
          {assistance.recommendations.map((recommendation) => {
            const contractTermsFallback =
              recommendation.reason_code === "CONTRACT_TERMS_TOKEN_OVERLAP";
            return (
              <li key={recommendation.recommendation_id}>
                <article className={styles.recommendationCard}>
                  <div className={styles.recommendationHeading}>
                    <div>
                      <p className={styles.cardKicker}>검토 후보</p>
                      <strong>{recommendation.coverage_label}</strong>
                    </div>
                    <span>추천 {recommendation.rank}</span>
                  </div>
                  <p className={styles.recommendationContract}>
                    {recommendation.contract_label} ·{" "}
                    {recommendation.clause_label}
                  </p>
                  <p className={styles.recommendationExcerpt}>
                    {recommendation.excerpt}
                  </p>
                  <p className={styles.recommendationReason}>
                    {reasonLabel(
                      contractTermsFallback
                        ? recommendation.reason_code
                        : (recommendation.explanation_code ??
                            recommendation.reason_code),
                    )}
                  </p>
                  {contractTermsFallback && recommendation.explanation_code ? (
                    <p className={styles.recommendationReason}>
                      {reasonLabel(recommendation.explanation_code)}
                    </p>
                  ) : null}
                  <details className={styles.citationDetails}>
                    <summary>추천 근거 보기</summary>
                    <p>
                      약관{" "}
                      {pageLabel(
                        recommendation.citation.page_start,
                        recommendation.citation.page_end,
                      )}
                    </p>
                  </details>
                </article>
              </li>
            );
          })}
        </ol>
      ) : (
        <p className={styles.emptyGroup}>
          현재 입력과 직접 연결된 관련 약관 추천이 없습니다. 위의 검증된 판정
          결과는 그대로 사용할 수 있습니다.
        </p>
      )}
    </section>
  );
}
