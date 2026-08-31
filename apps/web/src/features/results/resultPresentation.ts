import type {
  ClaimCandidateResponse,
  RuleEvaluationResponse,
} from "../../api/generated";

export function resultCopy(
  result: ClaimCandidateResponse["aggregate_result"],
): string {
  if (result === "MATCH")
    return "확인된 조건이 규칙과 일치합니다. 근거를 확인해 청구 검토를 진행할 수 있습니다.";
  if (result === "NO_MATCH") return "확인된 조건과 규칙이 일치하지 않습니다.";
  return "판정에 필요한 정보를 더 확인해야 합니다.";
}

export function resultTechnicalLabel(
  result: ClaimCandidateResponse["aggregate_result"],
): string {
  if (result === "MATCH") return "조건 일치";
  if (result === "NO_MATCH") return "조건 불일치";
  return "추가 확인";
}

export function fieldLabel(path: string): string {
  const labels: Record<string, string> = {
    "MedicalEvent.event_date": "사건일",
    "MedicalEvent.visit_date": "방문일",
    "MedicalEvent.classification": "상황 분류",
    "MedicalEvent.condition_class": "상황 분류",
    "MedicalEvent.diagnosis_label": "진단 표기",
    "MedicalEvent.diagnosis_code": "진단 코드",
    "MedicalEvent.procedure_code": "처치·수술 코드",
    "MedicalEvent.anatomical_site_code": "신체 부위 코드",
    "MedicalEvent.pathology_code": "병리 코드",
    "MedicalEvent.treatment_kind": "치료 종류",
    "MedicalEvent.treatment_setting": "치료 환경",
    "MedicalEvent.treatment_context": "치료 맥락",
    "MedicalEvent.separately_billed_treatment": "별도 결제 치료 여부",
    "MedicalEvent.admission": "입원 여부",
    "MedicalEvent.outpatient": "외래 여부",
    "MedicalEvent.pharmacy": "약국 이용 여부",
    "MedicalEvent.admission_days": "입원 일수",
    "PolicyContract.contract_start": "계약 시작일",
    "PolicyContract.contract_end": "계약 종료일",
    "Rider.status": "담보 상태",
    "ClaimHistory.counted_occurrence": "기존 청구 이력",
    "ReceiptLine.amount": "영수증 금액",
    "ReceiptLine.currency": "영수증 통화",
    "ReceiptLine.coverage_category": "급여·비급여 구분",
  };
  if (labels[path]) return labels[path];
  const short = path.includes(".")
    ? path.slice(path.lastIndexOf(".") + 1)
    : path;
  return short.replaceAll("_", " ");
}

export function reasonLabel(reasonCode: string): string {
  const labels: Record<string, string> = {
    EVIDENCE_UNAVAILABLE: "근거 문서를 확인할 수 없어 결과를 보류합니다.",
    RULE_READER_UNAVAILABLE:
      "보장 규칙을 불러오지 못했습니다. 다시 확인해 주세요.",
    RULE_RUNTIME_INVALID:
      "보장 규칙을 실행하지 못했습니다. 다시 확인해 주세요.",
    NO_EXECUTABLE_DECISION_RULE:
      "판정에 사용할 규칙이 없어 추가 확인이 필요합니다.",
    NO_EXECUTABLE_RULE: "확인 가능한 규칙이 없어 추가 확인이 필요합니다.",
    CONFLICTING_POLICY_SNAPSHOT:
      "계약 상태가 서로 달라 추가 확인이 필요합니다.",
    KNOWLEDGE_PUBLICATION_UNAVAILABLE:
      "가입 담보의 실행 규칙 검토가 아직 완료되지 않았습니다.",
    KNOWLEDGE_SOURCE_UNAVAILABLE:
      "구조화된 보험 자료 일부를 불러오지 못했습니다.",
    KNOWLEDGE_COVERAGE_EVALUATION_FAILED:
      "일부 가입 담보의 조건을 확인하지 못했습니다.",
    KNOWLEDGE_DISPOSITION_INCOMPLETE:
      "가입 담보별 검토 상태를 더 확인해야 합니다.",
    KNOWLEDGE_MAPPING_INCOMPLETE:
      "증권과 약관의 연결 상태를 더 확인해야 합니다.",
    KNOWLEDGE_CALCULATION_PUBLICATION_CONFLICT:
      "금액 계산 규칙이 서로 달라 추가 검토가 필요합니다.",
    COVERAGE_PUBLICATION_ADVISORY:
      "가입 담보와 관련 약관을 검색할 수 있지만 자동 판정 규칙은 아직 완전하지 않습니다.",
    COVERAGE_PUBLICATION_BLOCKED:
      "이전 실행 항목에 예외가 남아 있어 별도 확인이 필요합니다.",
    EVENT_DATE_STATUS_UNCONFIRMED: "사건일의 계약 상태를 확인해야 합니다.",
    EVENT_DATE_OUTSIDE_CONTRACT_TERM:
      "사건일이 확인된 계약 기간에 포함되지 않습니다.",
    PROCEDURE_CODE_REQUIRED: "처치·수술 코드를 확인해 주세요.",
    DIAGNOSIS_CODE_REQUIRED: "진단 코드를 확인해 주세요.",
    PATHOLOGY_CODE_REQUIRED: "병리 코드를 확인해 주세요.",
    RECEIPT_REQUIRED: "영수증 정보를 확인해 주세요.",
    RECEIPT_LINES_REQUIRED: "영수증 항목을 입력해 주세요.",
    RECEIPT_COVERED_AMOUNT_REQUIRED: "급여 대상 영수증 금액을 확인해 주세요.",
    RECEIPT_CURRENCY_CONFLICT: "영수증 통화가 서로 달라 확인이 필요합니다.",
    RECEIPT_AMOUNT_INVALID: "영수증 금액 형식을 확인해 주세요.",
    FIXED_AMOUNT_CALCULATED: "승인된 정액 계산 규칙을 적용했습니다.",
    TOKEN_OVERLAP: "입력 내용과 관련 표현이 있는 약관 조항입니다.",
    CONTRACT_TERMS_TOKEN_OVERLAP:
      "같은 계약의 약관에서 찾은 후보이며, 이 담보에 직접 적용된다는 뜻은 아닙니다.",
    RELATED_CLAUSE: "입력 내용과 관련 가능성이 있는 약관 조항입니다.",
  };
  return labels[reasonCode] ?? "판정에 필요한 조건을 확인할 수 없습니다.";
}

export function candidateSourceKey(candidate: ClaimCandidateResponse): string {
  return candidate.source.kind === "OPERATIONAL_RIDER"
    ? `rider:${candidate.source.rider_id}`
    : `knowledge:${candidate.source.knowledge_coverage_id}`;
}

export function evaluationSourceKey(
  evaluation: RuleEvaluationResponse,
): string {
  return evaluation.source.kind === "OPERATIONAL_RIDER"
    ? `rider:${evaluation.source.rider_id}`
    : `knowledge:${evaluation.source.knowledge_coverage_id}`;
}

export function evaluationMatchesCandidate(
  candidate: ClaimCandidateResponse,
  evaluation: RuleEvaluationResponse,
): boolean {
  return candidateSourceKey(candidate) === evaluationSourceKey(evaluation);
}

export function pageLabel(pageStart: number, pageEnd: number): string {
  return pageStart === pageEnd
    ? `${pageStart}쪽`
    : `${pageStart}\u2013${pageEnd}쪽`;
}
