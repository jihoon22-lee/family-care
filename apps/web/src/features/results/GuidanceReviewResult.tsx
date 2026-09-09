import type {
  CanonicalCoverageRef,
  GuidanceReviewResult as ReviewResult,
} from "../../api/generated";
import { CandidateAmounts } from "./LocalGuidanceDetails";
import { guidanceInputLabel, LocalGuidancePanel } from "./LocalGuidancePanel";
import { pageLabel } from "./resultPresentation";

const kinds = {
  AGREEMENT: "기존 해석과 일치",
  CORRECTION: "해석 교정 제안",
  ADDITIONAL_CANDIDATE: "추가 후보 제안",
  EXCEPTION: "예외 조건 확인",
  CONFLICT: "상충하는 해석",
};
const statuses = {
  APPLIED: "프로그램 재평가에 반영",
  AGREEMENT: "기존 해석과 일치",
  OPINION: "AI 의견 · 프로그램 결과에 미반영",
  REJECTED: "근거 검증을 통과하지 못해 미반영",
};
const changes = {
  ADDED: "추가된 후보",
  REMOVED: "제외된 후보",
  CHANGED: "달라진 후보",
};

export function GuidanceReviewResult({ result }: { result: ReviewResult }) {
  const scope = result.scope;
  function coverageLabel(ref: CanonicalCoverageRef | null) {
    const coverage = scope.coverages?.find(
      (item) =>
        ref &&
        item.ref.kind === ref.kind &&
        item.ref.contract_id === ref.contract_id &&
        item.ref.coverage_id === ref.coverage_id,
    );
    return coverage
      ? `${coverage.contract_label} · ${coverage.coverage_label}`
      : "검수 자료 전체";
  }
  return (
    <>
      <h3>검수 범위</h3>
      <p>
        가입 자료의 담보 항목 {scope.total_coverages}개 중 검수 목록에 포함된
        항목 {scope.indexed_coverages}개 · 원문 묶음 {scope.total_packets}개 중{" "}
        {scope.reviewed_packets}개 검수
      </p>
      {!scope.complete ? (
        <>
          <p>일부 범위만 검수했습니다.</p>
          {scope.unreviewed_packets || scope.omitted_packets ? (
            <p>
              미검수 원문 묶음 {scope.unreviewed_packets}개 · 범위에서 제외된
              원문 묶음 {scope.omitted_packets}개
            </p>
          ) : null}
          {(scope.expected_regions ?? 0) > 0 ? (
            <p>
              약관 조항·별표 등의 원문 범위 {scope.expected_regions}개 중{" "}
              {scope.supplied_regions}개를 검수 입력에 포함했습니다. 확인하지
              못한 원문 범위는 {scope.unsupplied_regions}개입니다.
            </p>
          ) : null}
          {result.reason_codes.includes("REVIEW_PARTIAL_INTERPRETATION") ? (
            <p>
              일부 조항의 해석은 검증하지 못했습니다. 확인된 계산과 미확인
              부분을 함께 봐 주세요.
            </p>
          ) : null}
          {result.reason_codes.includes("REVIEW_LOCAL_COMPARISON_PARTIAL") ? (
            <p>
              기존 안내의 일부 계산이나 조건은 검수 비교에 포함하지 못했습니다.
            </p>
          ) : null}
        </>
      ) : null}
      {scope.coverages?.length ? (
        <details>
          <summary>담보별 검수 범위</summary>
          <ul>
            {scope.coverages.map((coverage, index) => (
              <li key={index}>
                {coverage.contract_label} · {coverage.coverage_label}:{" "}
                {coverage.reviewed_packets}/{coverage.total_packets}개 검수
                {coverage.source_state === "UNAVAILABLE"
                  ? " · 원문 확인 불가"
                  : coverage.source_state === "PARTIAL"
                    ? " · 일부 원문만 확인 가능"
                    : ""}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <details>
        <summary>AI 검수 의견과 근거</summary>
        <p>
          AI 의견은 지급 판정이 아닙니다. 검증된 해석을 프로그램이 다시 평가한
          결과를 아래에서 확인해 주세요.
        </p>
        {result.findings.length === 0 ? (
          <p>표시할 검수 의견이 없습니다.</p>
        ) : (
          result.findings.map((finding, index) => (
            <article key={index}>
              <h3>{kinds[finding.kind]}</h3>
              <p>{coverageLabel(finding.coverage)}</p>
              <p>{statuses[finding.status]}</p>
              {finding.affected_fact_paths?.length ? (
                <p>
                  확인 항목:{" "}
                  {[
                    ...new Set(
                      finding.affected_fact_paths.map(guidanceInputLabel),
                    ),
                  ].join(", ")}
                </p>
              ) : null}
              {(finding.evidence ?? []).map((citation, citationIndex) => (
                <div key={citationIndex}>
                  <p>
                    약관 {pageLabel(citation.page_start, citation.page_end)}
                  </p>
                  <blockquote>{citation.quote}</blockquote>
                </div>
              ))}
            </article>
          ))
        )}
      </details>
      <details>
        <summary>프로그램 재평가 결과와 변경점</summary>
        <p>
          기존 로컬 안내는 위에 유지됩니다. 아래는 검수 해석을 반영한 별도
          결과이며, 청구 준비는 기존 결과에서 시작합니다.
        </p>
        {result.differences.length === 0 ? (
          <p>기존 후보와 달라진 항목이 없습니다.</p>
        ) : (
          result.differences.map((difference, index) => (
            <article key={index}>
              <h3>
                {changes[difference.change]} ·{" "}
                {difference.after?.coverage_label ??
                  difference.before?.coverage_label}
              </h3>
              {difference.before ? (
                <>
                  <p>
                    검수 전 ·{" "}
                    {difference.before.condition_result === "MATCH"
                      ? "사건과 보장 조건 관련"
                      : "추가 조건 확인 필요"}
                  </p>
                  <CandidateAmounts
                    candidate={difference.before}
                    inputLabel={guidanceInputLabel}
                  />
                </>
              ) : null}
              {difference.after ? (
                <>
                  <p>
                    검수 후 ·{" "}
                    {difference.after.condition_result === "MATCH"
                      ? "사건과 보장 조건 관련"
                      : "추가 조건 확인 필요"}
                  </p>
                  <CandidateAmounts
                    candidate={difference.after}
                    inputLabel={guidanceInputLabel}
                  />
                </>
              ) : null}
            </article>
          ))
        )}
        <LocalGuidancePanel guidance={result.guidance} />
      </details>
    </>
  );
}
