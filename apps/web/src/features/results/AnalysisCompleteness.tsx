import type {
  ClaimCandidateResponse,
  CoverageDecisionResponse,
  PrivateKnowledgeCandidateResponse,
} from "../../api/generated";
import { evaluationMatchesCandidate, reasonLabel } from "./resultPresentation";
import styles from "./Results.module.css";

function completenessLabel(
  completeness: CoverageDecisionResponse["analysis_completeness"],
): string {
  if (completeness === "COMPLETE") return "확인 가능한 범위 분석 완료";
  if (completeness === "PARTIAL") return "일부 범위만 분석 완료";
  return "실행 규칙 분석 준비 중";
}

function isPrivateCandidate(
  candidate: ClaimCandidateResponse,
): candidate is PrivateKnowledgeCandidateResponse {
  return candidate.source.kind === "PRIVATE_KNOWLEDGE_COVERAGE";
}

export function AnalysisCompleteness({
  result,
}: {
  result: CoverageDecisionResponse;
}) {
  const catalog = result.catalog_coverage;
  const advisoryCoverageCount = catalog.advisory_coverage_count;
  const hasAdvisoryCoverage = advisoryCoverageCount > 0;
  const hasLegacyExceptions = catalog.blocked_coverage_count > 0;
  const evaluatedPrivateCoverages = result.candidates
    .filter(isPrivateCandidate)
    .filter((candidate) =>
      result.evaluations.some((evaluation) =>
        evaluationMatchesCandidate(candidate, evaluation),
      ),
    );
  const reviewedCoverages = evaluatedPrivateCoverages.filter(
    (candidate, index) =>
      evaluatedPrivateCoverages.findIndex(
        (item) =>
          item.source.knowledge_coverage_id ===
          candidate.source.knowledge_coverage_id,
      ) === index,
  );

  return (
    <section className={styles.group} aria-labelledby="analysis-scope-title">
      <div className={styles.groupHeading}>
        <p className={styles.groupKicker}>Analysis scope</p>
        <h2 id="analysis-scope-title">분석 범위</h2>
      </div>
      <div className={styles.scopePanel}>
        <strong>{completenessLabel(result.analysis_completeness)}</strong>
        {hasAdvisoryCoverage ? (
          <p className={styles.scopeCopy}>
            가입 담보와 관련 약관을 검색할 수 있지만, 자동 판정 규칙은 아직
            완전하지 않습니다.
          </p>
        ) : (
          <p className={styles.scopeCopy}>
            증권에서 확인한 가입 담보와 자동 판정 규칙이 준비된 담보를 서로
            구분해 표시합니다.
          </p>
        )}
        {hasLegacyExceptions ? (
          <p className={styles.scopeWarning}>
            일부 이전 실행 항목은 예외 확인이 필요합니다.
          </p>
        ) : null}
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
            <dt>자동 판정 규칙 준비</dt>
            <dd>{catalog.published_coverage_count}개</dd>
          </div>
          <div>
            <dt>가입·검색 가능 · 자동 규칙 미완료</dt>
            <dd>{advisoryCoverageCount}개</dd>
          </div>
          {hasLegacyExceptions ? (
            <div>
              <dt>예외 확인</dt>
              <dd>{catalog.blocked_coverage_count}개</dd>
            </div>
          ) : null}
        </dl>
        {reviewedCoverages.length > 0 ? (
          <section
            className={styles.reviewedCoveragePanel}
            aria-labelledby="reviewed-coverages-title"
          >
            <h3 id="reviewed-coverages-title">자동 판정 규칙이 준비된 담보</h3>
            <p>이번 사건에 실제로 규칙을 실행한 담보와 연결된 계약입니다.</p>
            <ul>
              {reviewedCoverages.map((candidate) => (
                <li key={candidate.candidate_id}>
                  <strong>{candidate.coverage_label}</strong>
                  <span>{candidate.contract_label}</span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
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
