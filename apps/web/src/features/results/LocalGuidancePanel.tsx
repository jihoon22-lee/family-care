import { useId } from "react";
import type {
  CanonicalCoverageRef,
  GuidanceCandidate,
  GuidanceEvidence,
  GuidanceSemanticEvidence,
  LocalGuidanceResponse,
} from "../../api/generated";
import {
  CandidateAmounts,
  ContractAmount,
  Expenses,
  EvidencePages,
} from "./LocalGuidanceDetails";
import {
  GuidanceEvidenceContext,
  type GuidanceEvidenceReferences,
} from "./GuidanceEvidenceContext";
import { LocalGuidanceSubtotals } from "./LocalGuidanceSubtotals";
import styles from "./Results.module.css";
import panelStyles from "./LocalGuidancePanel.module.css";

const inputLabels: Record<string, string> = {
  "MedicalEvent.event_date": "사건일",
  "MedicalEvent.visit_date": "방문일",
  "MedicalEvent.diagnosis_label": "진단명",
  "MedicalEvent.diagnosis_code": "진단 코드",
  "MedicalEvent.procedure_code": "처치·수술 코드",
  "MedicalEvent.admission_days": "입원 일수",
  "MedicalEvent.admission": "입원 여부",
  "MedicalEvent.outpatient": "외래 여부",
  "MedicalEvent.pharmacy": "약국 이용 여부",
  "MedicalEvent.condition_class": "질병·상해 구분",
  "MedicalEvent.treatment_kind": "치료 종류",
  "MedicalEvent.treatment_setting": "치료 환경",
  "MedicalEvent.treatment_context": "치료 맥락",
  "MedicalEvent.pathology_code": "병리 코드",
  "MedicalEvent.anatomical_site_code": "신체 부위 코드",
  "MedicalEvent.separately_billed_treatment": "별도 결제 치료 여부",
  "Receipt.covered_amount": "보장대상 비용",
  "Receipt.noncovered_amount": "비급여 영수증 금액",
  "Receipt.total_amount": "영수증 총액",
  "Receipt.currency": "영수증 통화",
  "ClaimHistory.counted_occurrence": "기존 청구 이력",
  "Rider.insured_amount": "가입금액",
};

export function guidanceInputLabel(path: string): string {
  return inputLabels[path] ?? "추가 사건 정보";
}

function questionCopy(path: string): string {
  const label = guidanceInputLabel(path);
  const lastCharacter = label.charCodeAt(label.length - 1);
  const particle = (lastCharacter - 0xac00) % 28 === 0 ? "를" : "을";
  return `${label}${particle} 알려주세요.`;
}

function evidenceKey(item: GuidanceEvidence | GuidanceSemanticEvidence) {
  const source =
    "citation_id" in item
      ? `${item.publication_id}:${item.citation_id}`
      : item.evidence_id;
  return `${item.kind}:${source}:${item.page_start}:${item.page_end}`;
}

function Candidate({
  candidate,
  onStartClaim,
  claimStartDisabled,
  onOpenEvidence,
}: {
  candidate: GuidanceCandidate;
  onStartClaim?: (coverage: CanonicalCoverageRef) => void;
  claimStartDisabled?: boolean;
  onOpenEvidence?: (
    coverage: CanonicalCoverageRef,
    evidence: GuidanceEvidenceReferences,
  ) => void;
}) {
  const titleId = useId();
  const evidence = [
    ...new Map(
      [
        ...(candidate.conditions ?? []).flatMap(
          (condition) => condition.evidence,
        ),
        ...(candidate.estimate.evidence ?? []),
        ...(candidate.relevance ?? []).flatMap((item) => item.evidence),
        ...(candidate.scenarios ?? []).flatMap(
          (scenario) => scenario.estimate.evidence ?? [],
        ),
        ...(candidate.cases ?? []).flatMap(
          (item) => item.estimate.evidence ?? [],
        ),
        ...(candidate.contract_amount?.evidence ?? []).filter(
          (item): item is GuidanceEvidence =>
            item.kind === "OPERATIONAL_EVIDENCE" ||
            item.kind === "TERMS_SECTION",
        ),
      ].map((item) => [evidenceKey(item), item]),
    ).values(),
  ];
  return (
    <GuidanceEvidenceContext.Provider
      value={
        onOpenEvidence
          ? (refs) => onOpenEvidence(candidate.ref, refs)
          : undefined
      }
    >
      <article
        className={`${styles.candidateCard} ${panelStyles.card}`}
        aria-labelledby={titleId}
      >
        <div className={styles.cardHeading}>
          <div>
            <span className={styles.contractLabel}>
              {candidate.contract_label}
            </span>
            <h3 id={titleId}>{candidate.coverage_label}</h3>
          </div>
          <span className={styles.benefitBadge}>
            {candidate.benefit_kind === "FIXED"
              ? "정액형"
              : candidate.benefit_kind === "INDEMNITY"
                ? "실손형"
                : "보장 유형 확인 필요"}
          </span>
        </div>
        <p className={styles.cardCopy}>
          {candidate.condition_result === "MATCH"
            ? "입력한 사건과 가입 문서의 보장 조건이 관련됩니다."
            : "입력한 사건과 관련된 담보이며, 추가 사건 정보에 따라 적용 조건이 달라질 수 있습니다."}
        </p>
        {candidate.estimate.kind === "FORMULA" &&
        candidate.canonical_identity?.field_conflicts?.includes(
          "insured_amount",
        ) ? (
          <p className={styles.cardCopy}>
            가입 분석과 앱 원장의 금액이 달라 계산식만 안내합니다.
          </p>
        ) : null}
        {candidate.estimate.kind === "FORMULA" &&
        candidate.canonical_identity?.field_conflicts?.includes("currency") ? (
          <p className={styles.cardCopy}>
            가입 분석과 앱 원장의 통화가 달라 계산식만 안내합니다.
          </p>
        ) : null}
        {candidate.contract_amount ? (
          <ContractAmount value={candidate.contract_amount} />
        ) : null}
        <CandidateAmounts
          candidate={candidate}
          inputLabel={guidanceInputLabel}
        />
        {candidate.freshness === "STATUS_UNRESOLVED" ? (
          <p className={styles.cardCopy}>
            사건일의 계약 상태에 따라 이 후보가 달라질 수 있습니다.
          </p>
        ) : null}
        {candidate.assumptions?.includes("EVENT_DATE_REQUIRED") ? (
          <p className={styles.cardCopy}>
            사건일이 확인되면 계약 기간과 적용 조건을 다시 계산합니다.
          </p>
        ) : null}
        {candidate.benefit_kind === "INDEMNITY" ? (
          <p className={styles.cardCopy}>
            실손형 예상액은 실제 지출과 자기부담 조건을 기준으로 하며 정액형과
            별도로 봅니다.
          </p>
        ) : null}
        <EvidencePages evidence={evidence} />
        {onStartClaim ? (
          <button
            type="button"
            className={styles.primaryButton}
            disabled={claimStartDisabled}
            aria-label={`${candidate.coverage_label} 청구 준비`}
            onClick={() => onStartClaim(candidate.ref)}
          >
            청구 준비
          </button>
        ) : null}
      </article>
    </GuidanceEvidenceContext.Provider>
  );
}

export function LocalGuidancePanel({
  guidance,
  onRetry,
  showEmpty = true,
  onStartClaim,
  claimStartDisabled = false,
  onOpenEvidence,
}: {
  guidance: LocalGuidanceResponse;
  onRetry?: () => void;
  showEmpty?: boolean;
  onStartClaim?: (coverage: CanonicalCoverageRef) => void;
  claimStartDisabled?: boolean;
  onOpenEvidence?: (
    coverage: CanonicalCoverageRef,
    evidence: GuidanceEvidenceReferences,
  ) => void;
}) {
  const id = useId();
  const questions = [
    ...new Set(
      guidance.candidates.flatMap((candidate) =>
        (candidate.questions ?? []).map((question) => question.field_path),
      ),
    ),
  ];
  const continuity = guidance.candidates.some(
    (candidate) => candidate.freshness === "DOCUMENT_CONTINUITY",
  );
  const emptyCopy: Record<LocalGuidanceResponse["outcome"], string> = {
    CANDIDATES: "표시할 후보가 없습니다.",
    NO_RELEVANT_COVERAGE: "현재 사건과 관련된 가입 담보를 찾지 못했습니다.",
    INPUT_UNRESOLVED: "사건 내용을 조금 더 구체적으로 입력해 주세요.",
    KNOWLEDGE_PENDING:
      "보험 자료를 불러오거나 해석하지 못해 관련 담보를 확인하지 못했습니다.",
  };
  return (
    <div className={styles.resultBody}>
      <p className={styles.summaryNotice}>
        가입 문서와 입력한 사건을 기준으로 살펴본 후보입니다. 예상 금액은 실제
        지급액과 다를 수 있습니다.
      </p>
      {continuity ? (
        <p className={styles.scopeWarning}>
          문서에 기록된 계약이 사건일까지 유지된 것으로 가정했습니다.
          해지·실효·변경이 있다면 결과가 달라질 수 있습니다.
        </p>
      ) : null}
      {showEmpty && guidance.candidates.length === 0 ? (
        <p className={styles.emptyGroup}>{emptyCopy[guidance.outcome]}</p>
      ) : null}
      <LocalGuidanceSubtotals
        guidance={guidance}
        inputLabel={guidanceInputLabel}
      />
      {(["PRIMARY", "CONDITIONAL"] as const).map((group) => {
        const candidates = guidance.candidates.filter(
          (candidate) => candidate.group === group,
        );
        return candidates.length ? (
          <section
            key={group}
            className={styles.group}
            aria-labelledby={`${id}-${group}`}
          >
            <div className={styles.groupHeading}>
              <h2 id={`${id}-${group}`}>
                {group === "PRIMARY"
                  ? "주요 후보"
                  : "조건에 따라 달라지는 후보"}
              </h2>
            </div>
            <div className={styles.candidateList}>
              {candidates.map((candidate) => (
                <Candidate
                  key={`${candidate.ref.kind}:${candidate.ref.contract_id}:${candidate.ref.coverage_id}`}
                  candidate={candidate}
                  onStartClaim={onStartClaim}
                  claimStartDisabled={claimStartDisabled}
                  onOpenEvidence={onOpenEvidence}
                />
              ))}
            </div>
          </section>
        ) : null;
      })}
      {guidance.expenses ? <Expenses expenses={guidance.expenses} /> : null}
      {questions.length ? (
        <section className={styles.group} aria-labelledby={`${id}-questions`}>
          <div className={styles.groupHeading}>
            <h2 id={`${id}-questions`}>결과를 더 구체화할 정보</h2>
          </div>
          <p className={styles.cardCopy}>
            아래 정보를 보완하면 해당 후보의 조건이나 예상 금액을 더 구체적으로
            확인할 수 있습니다. 현재 결과는 그대로 볼 수 있습니다.
          </p>
          <ul className={styles.reasonList}>
            {questions.map((path) => (
              <li key={path}>{questionCopy(path)}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {guidance.support.unsupported_coverages > 0 ||
      guidance.support.failure_codes?.length ? (
        <div className={styles.scopeWarning}>
          <p>
            일부 담보의 자료를 해석하지 못했습니다. 위에서 확인된 후보와 예상
            금액은 계속 볼 수 있습니다.
          </p>
          {onRetry ? (
            <button
              type="button"
              className={styles.secondaryButton}
              onClick={onRetry}
            >
              다시 확인
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
