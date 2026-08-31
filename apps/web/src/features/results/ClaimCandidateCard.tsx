import { useId, useState } from "react";

import type {
  BenefitCalculationResponse,
  ClaimCandidateResponse,
  KnowledgeBenefitCalculationResponse,
  OperationalEvaluationResponse,
  PrivateKnowledgeEvaluationResponse,
  RuleEvaluationResponse,
} from "../../api/generated";
import { CalculationDetails } from "./CalculationDetails";
import {
  fieldLabel,
  pageLabel,
  reasonLabel,
  resultCopy,
  resultTechnicalLabel,
} from "./resultPresentation";
import styles from "./Results.module.css";

function isOperationalEvaluation(
  evaluation: RuleEvaluationResponse,
): evaluation is OperationalEvaluationResponse {
  return evaluation.source.kind === "OPERATIONAL_RIDER";
}

function isPrivateEvaluation(
  evaluation: RuleEvaluationResponse,
): evaluation is PrivateKnowledgeEvaluationResponse {
  return evaluation.source.kind === "PRIVATE_KNOWLEDGE_COVERAGE";
}

function benefitKindLabel(
  kind: ClaimCandidateResponse["benefit_kind"],
): string {
  if (kind === "FIXED") return "정액형";
  if (kind === "INDEMNITY") return "실손형";
  return "보장 유형 확인 필요";
}

function isConditionalPolicyEstimate(
  calculation: KnowledgeBenefitCalculationResponse,
): boolean {
  return (
    calculation.status === "CALCULATED" &&
    calculation.confirmed_amount === null &&
    calculation.hold_reason_code !== null
  );
}

function calculationStatusLabel(
  calculation: KnowledgeBenefitCalculationResponse,
): string {
  if (isConditionalPolicyEstimate(calculation)) return "조건부 약관 예상액";
  if (calculation.status === "CALCULATED") {
    return calculation.confirmed_amount === null
      ? "서버 계산 완료"
      : "확인된 계산 결과";
  }
  if (calculation.status === "NOT_APPLICABLE") return "계산 대상 아님";
  if (calculation.status === "FAILED") return "계산 다시 확인 필요";
  return "계산 조건 추가 확인";
}

function calculationOperationLabel(operation: string): string {
  const labels: Record<string, string> = {
    apply_deductible: "자기부담금 적용",
    apply_limit: "보장 한도 적용",
    apply_rate: "보장 비율 적용",
    exclude_amount: "제외 금액 반영",
    fixed_amount: "정액 금액 적용",
    round: "약관 기준 반올림",
    sum_eligible_receipts: "대상 영수증 합계",
  };
  return labels[operation] ?? "승인된 계산 단계 적용";
}

function citationPurposeLabel(purpose: string): string {
  const labels: Record<string, string> = {
    CALCULATION: "계산 근거",
    ELIGIBILITY: "판정 조건 근거",
    EXCLUSION: "제외 조건 근거",
    LIMIT: "한도 근거",
  };
  return labels[purpose] ?? "약관 판정 근거";
}

function KnowledgeCalculationTrace({
  calculation,
}: {
  calculation: KnowledgeBenefitCalculationResponse;
}) {
  const conditionalPolicyEstimate = isConditionalPolicyEstimate(calculation);
  const amount =
    calculation.status === "CALCULATED"
      ? conditionalPolicyEstimate
        ? calculation.conditional_amount
        : (calculation.confirmed_amount ?? calculation.conditional_amount)
      : null;

  return (
    <div className={styles.knowledgeCalculation}>
      <div className={styles.calculationTraceHeading}>
        <div>
          <p className={styles.cardKicker}>Approved calculation trace</p>
          <strong>{calculationStatusLabel(calculation)}</strong>
        </div>
        <span className={styles.calculationKind}>
          {calculation.kind === "FIXED"
            ? "정액형"
            : calculation.kind === "INDEMNITY"
              ? "실손형"
              : "유형 확인 필요"}
        </span>
      </div>
      {amount && calculation.currency ? (
        <p className={styles.conditionalAmount}>
          {conditionalPolicyEstimate
            ? "조건부 예상액"
            : calculation.status === "CALCULATED" &&
                calculation.confirmed_amount !== null
              ? "확인된 계산 금액"
              : "조건부 계산"}
          : {amount} {calculation.currency}
        </p>
      ) : (
        <p className={styles.calculationStatus}>
          현재 표시할 계산 금액이 없습니다.
        </p>
      )}
      {calculation.applied_rate ? (
        <p className={styles.detailLine}>
          적용 비율 {calculation.applied_rate}
        </p>
      ) : null}
      {calculation.applied_limit && calculation.currency ? (
        <p className={styles.detailLine}>
          적용 한도 {calculation.applied_limit} {calculation.currency}
        </p>
      ) : null}
      {calculation.hold_reason_code ? (
        <p className={styles.calculationStatus}>
          {reasonLabel(calculation.hold_reason_code)}
        </p>
      ) : null}
      {calculation.steps.length > 0 ? (
        <ol className={styles.stepList} aria-label="구조화 계산 단계">
          {calculation.steps.map((step) => (
            <li key={step.step_number}>
              <span>
                {step.step_number}. {calculationOperationLabel(step.operation)}
              </span>
              <small>{reasonLabel(step.reason_code)}</small>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}

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
  const citationRegionId = useId();
  const [privateCitationsOpen, setPrivateCitationsOpen] = useState(false);
  const operationalEvaluations = evaluations.filter(isOperationalEvaluation);
  const privateEvaluations = evaluations.filter(isPrivateEvaluation);
  const evidenceIds = [
    ...new Set(
      operationalEvaluations.flatMap((evaluation) =>
        evaluation.citations.map((item) => item.evidence_id),
      ),
    ),
  ];
  const privateCitations = privateEvaluations.flatMap(
    (evaluation) => evaluation.citations,
  );
  const missingFields = [
    ...new Set([
      ...candidate.questions.map((question) => question.field_path),
      ...evaluations.flatMap((evaluation) => evaluation.missing_fields),
    ]),
  ];
  const conflictingFields = [
    ...new Set(
      evaluations.flatMap((evaluation) => evaluation.conflicting_fields),
    ),
  ];
  const reasonCodes = [
    ...new Set([
      ...candidate.hold_reason_codes,
      ...candidate.questions.map((question) => question.reason_code),
      ...evaluations
        .filter((evaluation) => evaluation.result !== "MATCH")
        .map((evaluation) => evaluation.reason_code),
    ]),
  ];
  const isOperational = candidate.source.kind === "OPERATIONAL_RIDER";
  const operationalRiderId =
    candidate.source.kind === "OPERATIONAL_RIDER"
      ? candidate.source.rider_id
      : undefined;
  const canStartClaim =
    claimStartEnabled &&
    operationalRiderId !== undefined &&
    candidate.claim_start_ready &&
    candidate.aggregate_result === "MATCH";

  return (
    <article className={styles.candidateCard}>
      <div className={styles.cardHeading}>
        <div>
          <p className={styles.cardKicker}>
            {isOperational ? "등록 담보" : "증권 확인 담보"}
          </p>
          <strong>{label}</strong>
          <span className={styles.contractLabel}>
            {candidate.contract_label}
          </span>
        </div>
        <div className={styles.badgeStack}>
          <span className={styles.benefitBadge}>
            {benefitKindLabel(candidate.benefit_kind)}
          </span>
          <span
            className={`${styles.resultBadge} ${styles[`result${candidate.aggregate_result}`]}`}
            aria-label={`판정 ${resultTechnicalLabel(candidate.aggregate_result)}`}
          >
            {resultTechnicalLabel(candidate.aggregate_result)}
          </span>
        </div>
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
        {canStartClaim && operationalRiderId ? (
          <button
            type="button"
            className={styles.primaryButton}
            onClick={() => onStartClaim(operationalRiderId)}
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
        {privateCitations.length > 0 ? (
          <button
            type="button"
            className={styles.quietButton}
            aria-controls={citationRegionId}
            aria-expanded={privateCitationsOpen}
            onClick={() => setPrivateCitationsOpen((open) => !open)}
          >
            약관 근거 보기 ({privateCitations.length})
          </button>
        ) : null}
      </div>
      {privateCitationsOpen ? (
        <ul className={styles.privateCitationList} id={citationRegionId}>
          {privateCitations.map((citation, index) => (
            <li
              key={`${citation.page_start}-${citation.page_end}-${citation.evidence_purpose}-${index}`}
            >
              <strong>{citationPurposeLabel(citation.evidence_purpose)}</strong>
              <span>{pageLabel(citation.page_start, citation.page_end)}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {candidate.source.kind === "PRIVATE_KNOWLEDGE_COVERAGE" &&
      candidate.calculation ? (
        <KnowledgeCalculationTrace calculation={candidate.calculation} />
      ) : null}
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
