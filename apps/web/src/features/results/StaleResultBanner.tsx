import type { CoverageDecisionResponse } from "../../api/generated";
import styles from "./Results.module.css";

export function StaleResultBanner({
  result,
  onReanalyze,
}: {
  result: CoverageDecisionResponse;
  onReanalyze: () => void;
}) {
  if (!result.stale) return null;
  return (
    <div className={styles.staleBanner} role="alert">
      <strong>다시 분석이 필요합니다</strong>
      <p>
        이 결과는 현재 사건·계약·규칙과 달라질 수 있어 청구 검토에 바로 사용할
        수 없습니다.
      </p>
      <dl className={styles.metadata}>
        <div>
          <dt>사건 버전</dt>
          <dd>{result.event_version}</dd>
        </div>
        <div>
          <dt>정책 확인 시각</dt>
          <dd>{result.policy_snapshot_at}</dd>
        </div>
        <div>
          <dt>규칙 세트</dt>
          <dd>{result.rule_set_version}</dd>
        </div>
        <div>
          <dt>엔진</dt>
          <dd>{result.engine_version}</dd>
        </div>
      </dl>
      <button
        type="button"
        className={styles.primaryButton}
        onClick={onReanalyze}
      >
        다시 분석
      </button>
    </div>
  );
}
