import { useId } from "react";
import type {
  CanonicalCoverageRef,
  GuidanceCandidate,
  GuidanceSubtotalComponent,
  GuidanceSubtotalOmission,
  LocalGuidanceResponse,
} from "../../api/generated";
import { money } from "./LocalGuidanceDetails";
import styles from "./Results.module.css";
import panelStyles from "./LocalGuidancePanel.module.css";

const assumptionLabels: Record<string, string> = {
  INDEPENDENT_FIXED_PAYMENTS_ASSUMED: "각 계약이 독립적으로 지급된다는 가정",
  DOCUMENT_CONTINUITY_ASSUMED: "문서상 계약 유지 가정",
  EVENT_STATUS_UNRESOLVED: "사건일의 계약 상태 미확인",
  EVENT_DATE_REQUIRED: "사건일 확인 필요",
  PLANNED_CARE_ASSUMED: "예정된 치료가 이루어진다는 가정",
  CONDITIONS_REMAIN: "남은 보장 조건에 따라 달라지는 금액",
};

const omissionLabels: Record<string, string> = {
  MULTIPLE_PAYOUT_CASES: "여러 지급 경우의 적용 관계가 확인되지 않았습니다.",
  SAME_CONTRACT_COMBINATION_UNRESOLVED:
    "같은 계약 안에서 함께 지급되는 조건이 확인되지 않았습니다.",
  CANONICAL_CONTRACT_UNRESOLVED:
    "다른 자료의 담보와 중복되는지 확인되지 않았습니다.",
  CANONICAL_IDENTITY_CONFLICT:
    "담보 연결 자료가 서로 달라 소계에 포함하지 않았습니다.",
  CANONICAL_DUPLICATE_CONFLICT:
    "같은 담보의 금액 자료가 서로 달라 소계에 포함하지 않았습니다.",
  EVENT_CONDITIONS_UNRESOLVED:
    "이 소계의 사건 조건을 아직 확인하지 못했습니다.",
  PAYOUT_CASE_UNRESOLVED: "선택한 지급 경우의 조건이나 금액을 확인해야 합니다.",
  POINT_ESTIMATE_UNAVAILABLE:
    "계산식이나 필요한 입력이 부족해 단일 금액을 산정하지 못했습니다.",
  PARTIAL_ESTIMATE: "일부 금액만 계산되어 정액 소계에 포함하지 않았습니다.",
  ESTIMATE_CONTEXT_UNSUPPORTED:
    "이 금액은 다른 계산 기준이나 가정에 해당합니다.",
  CALCULATION_TRACE_UNAVAILABLE:
    "계산 과정과 근거를 확인하지 못해 소계에 포함하지 않았습니다.",
  CALCULATION_CONTEXT_MISMATCH:
    "계산에 사용한 사건 또는 가정이 이 소계와 다릅니다.",
  INSUFFICIENT_COMPATIBLE_ADDENDS:
    "같은 조건으로 함께 묶을 정액 항목이 충분하지 않습니다.",
  SUBTOTAL_AMOUNT_LIMIT_EXCEEDED:
    "소계를 표시할 수 있는 금액 범위를 넘었습니다.",
  SCENARIO_KNOWLEDGE_INCOMPLETE:
    "관련 약관의 해석이 끝나지 않아 예정 치료 소계에 포함하지 않았습니다.",
  SCENARIO_ESTIMATE_CONFLICT:
    "같은 예정 치료에 연결된 예상액이 서로 달라 소계에 포함하지 않았습니다.",
  SCENARIO_HYPOTHESES_CONFLICT:
    "예정 치료의 가정이 서로 맞지 않아 함께 합산하지 않았습니다.",
  SCENARIO_SUBTOTAL_BUDGET_EXCEEDED:
    "예정 치료 조합이 많아 소계로 묶지 못했습니다. 담보별 금액은 따로 확인할 수 있습니다.",
};

function sameRef(left: CanonicalCoverageRef, right: CanonicalCoverageRef) {
  return (
    left.kind === right.kind &&
    left.contract_id === right.contract_id &&
    left.coverage_id === right.coverage_id
  );
}

function componentLabel(
  item: GuidanceSubtotalComponent,
  candidates: GuidanceCandidate[],
) {
  const matches = candidates.filter((candidate) =>
    [
      candidate.ref,
      ...(candidate.canonical_identity
        ? [
            candidate.canonical_identity.ref,
            ...candidate.canonical_identity.source_refs,
          ]
        : []),
      ...(candidate.cases ?? []).flatMap((sourceCase) =>
        sourceCase.source_ref ? [sourceCase.source_ref] : [],
      ),
    ].some((ref) => sameRef(ref, item.ref)),
  );
  if (matches.length !== 1) return "담보 정보를 확인할 수 없는 항목";
  const candidate = matches[0]!;
  const caseIndex =
    item.case_key == null
      ? -1
      : (candidate.cases ?? []).findIndex(
          (sourceCase) => sourceCase.case_key === item.case_key,
        );
  return `${candidate.contract_label} · ${candidate.coverage_label}${caseIndex >= 0 ? ` · 원문 조건 ${caseIndex + 1}` : ""}`;
}

function omissionLabel(item: GuidanceSubtotalOmission) {
  if (item.reason_code === "NON_FIXED_BENEFIT")
    return item.benefit_kind === "INDEMNITY"
      ? "실손형은 정액 소계에 포함하지 않습니다."
      : "보장 유형이 확인되지 않아 정액 소계에 포함하지 않았습니다.";
  return (
    omissionLabels[item.reason_code] ??
    "별도로 확인할 항목이 있어 소계에 포함하지 않았습니다."
  );
}

export function LocalGuidanceSubtotals({
  guidance,
  inputLabel,
}: {
  guidance: LocalGuidanceResponse;
  inputLabel: (path: string) => string;
}) {
  const id = useId();
  const totals = guidance.fixed_subtotals ?? [];
  const omissions = guidance.subtotal_omissions ?? [];
  if (!totals.length && !omissions.length) return null;
  const scenarioKeys = [
    ...new Set(
      [...totals, ...omissions].flatMap((item) =>
        item.scenario_key == null ? [] : [item.scenario_key],
      ),
    ),
  ];
  const contextLabel = (key: string | null | undefined) =>
    key == null
      ? "현재 사건 기준"
      : `예정 치료 가정 ${scenarioKeys.indexOf(key) + 1}`;

  return (
    <section className={styles.group} aria-labelledby={`${id}-title`}>
      <div className={styles.groupHeading}>
        <h2 id={`${id}-title`}>조건부 정액 소계</h2>
      </div>
      {!totals.length ? (
        <p>
          함께 묶을 수 있는 정액 소계가 없습니다. 담보별 예상액과 계산식은 계속
          확인할 수 있습니다.
        </p>
      ) : null}
      <div className={styles.candidateList}>
        {totals.map((subtotal, index) => (
          <section
            key={subtotal.subtotal_key}
            className={panelStyles.sourceCase}
            aria-labelledby={`${id}-subtotal-${index}`}
          >
            <h3 id={`${id}-subtotal-${index}`}>
              {contextLabel(subtotal.scenario_key)} 소계 · {subtotal.currency}
            </h3>
            <strong>{subtotal.partial ? "부분 소계" : "조건부 소계"}</strong>
            <p className={styles.subtotalAmount}>
              {money(subtotal.amount, subtotal.currency)}
            </p>
            {subtotal.partial ? (
              <p>계산하지 못한 항목이 있어 전체 예상액으로 볼 수 없습니다.</p>
            ) : null}
            {subtotal.scenario_key != null ? (
              <>
                <p>
                  예정대로 치료받는 경우의 가정 소계입니다. 실제 치료 사실이나
                  실제 지급액을 뜻하지 않습니다.
                </p>
                <ul>
                  {(subtotal.hypotheses ?? []).map(
                    (hypothesis, hypothesisIndex) => (
                      <li key={hypothesisIndex}>
                        {inputLabel(hypothesis.field_path)}:{" "}
                        {hypothesis.field_path ===
                          "MedicalEvent.admission_days" &&
                        typeof hypothesis.value === "number"
                          ? `${hypothesis.value}일`
                          : typeof hypothesis.value === "boolean"
                            ? hypothesis.value
                              ? "해당"
                              : "해당하지 않음"
                            : String(hypothesis.value)}{" "}
                        가정
                      </li>
                    ),
                  )}
                </ul>
              </>
            ) : (
              <p>
                입력한 사건을 기준으로 계산한 조건부 금액이며 실제 지급액이
                아닙니다.
              </p>
            )}
            <p>
              포함된 정액 계약이 함께 지급되고, 계약 사이의 공통 한도나 중복
              지급 감액이 적용되지 않는다고 가정한 금액입니다. 이 합산 전제는
              아직 확인되지 않았습니다.
            </p>
            <details className={panelStyles.trace}>
              <summary>포함한 담보와 합산 전제</summary>
              <ul>
                {subtotal.items.map((item, itemIndex) => (
                  <li key={itemIndex}>
                    <span>{componentLabel(item, guidance.candidates)}</span> —{" "}
                    <strong>{money(item.amount, subtotal.currency)}</strong>
                  </li>
                ))}
              </ul>
              <p>계산 과정과 근거는 아래의 해당 담보에서 확인할 수 있습니다.</p>
              <ul>
                {subtotal.scoped_assumptions.map(
                  (assumption, assumptionIndex) => (
                    <li key={assumptionIndex}>
                      <strong>
                        {assumptionLabels[assumption.code] ?? "추가 가정"}
                      </strong>
                      <ul>
                        {assumption.applies_to.map((item, itemIndex) => (
                          <li key={itemIndex}>
                            {componentLabel(item, guidance.candidates)}
                          </li>
                        ))}
                      </ul>
                    </li>
                  ),
                )}
              </ul>
            </details>
          </section>
        ))}
      </div>
      {omissions.length ? (
        <section aria-labelledby={`${id}-omitted`}>
          <h3 id={`${id}-omitted`}>소계에 포함하지 않은 항목</h3>
          <p>
            아래 항목을 0원으로 처리한 것은 아닙니다. 담보별 금액과 계산식은
            따로 확인하세요.
          </p>
          <details className={panelStyles.trace}>
            <summary>빠진 항목 {omissions.length}건과 이유</summary>
            <ul>
              {omissions.map((item, index) => (
                <li key={index}>
                  <strong>{componentLabel(item, guidance.candidates)}</strong>
                  <p>
                    {contextLabel(item.scenario_key)} ·{" "}
                    {item.currency ?? "통화 확인 필요"}
                  </p>
                  <p>{omissionLabel(item)}</p>
                </li>
              ))}
            </ul>
          </details>
        </section>
      ) : null}
    </section>
  );
}
