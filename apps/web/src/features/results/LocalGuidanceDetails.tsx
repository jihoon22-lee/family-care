import type {
  GuidanceCalculationOperand,
  GuidanceCalculationStep,
  GuidanceCalculationTrace,
  GuidanceCandidate,
  GuidanceContractAmount,
  GuidanceCostGroup,
  GuidanceEstimate,
  GuidanceEvidence,
  GuidanceExpenses,
  GuidancePayoutCase,
  GuidanceScenario,
  GuidanceSemanticEvidence,
  GuidanceSourceReference,
} from "../../api/generated";
import { pageLabel } from "./resultPresentation";
import styles from "./Results.module.css";
import panelStyles from "./LocalGuidancePanel.module.css";

export function money(
  amount: string,
  currency: string | null | undefined,
): string {
  const [integer, fraction] = amount.split(".");
  const formatted =
    integer.replace(/\B(?=(\d{3})+(?!\d))/g, ",") +
    (fraction ? `.${fraction}` : "");
  return currency === "KRW"
    ? `${formatted}원`
    : `${formatted}${currency ? ` ${currency}` : " (통화 미확인)"}`;
}

const provenanceLabels: Record<string, string> = {
  USER_CONFIRMED: "사용자 확인",
  DOCUMENT_REVIEWED: "문서 검수 확인",
  PROGRAM_VERIFIED: "원문 대조",
  DERIVED_CONFIRMED: "확인된 사건 정보에서 도출",
  SCENARIO_ASSUMPTION: "예정 치료 가정",
  AI_STRUCTURED: "AI 정리 · 확인 전",
  UNCONFIRMED: "확인 전",
};

function provenanceLabel(value: string | null | undefined) {
  return provenanceLabels[value ?? ""] ?? "확인 근거 미확인";
}

function unitValue(
  value: string | null | undefined,
  unit: GuidanceCalculationOperand["unit"],
  currency?: string | null,
): string {
  if (value == null) return "미산정";
  switch (unit) {
    case "MONEY":
      return money(value, currency);
    case "DAYS":
      return `${value}일`;
    case "COUNT":
      return `${value}회`;
    case "RATIO":
      return `${value} (비율)`;
    case "NUMBER":
      return value;
    case "UNKNOWN":
      return `${value} (단위 미확인)`;
  }
}

function sourceLabel(ref: GuidanceSourceReference): string {
  switch (ref.source_kind) {
    case "RECEIPT_LINE":
      return "등록된 비용 항목";
    case "EVENT_RECEIPT_SET":
      return "등록된 비용 묶음";
    case "EVENT_SCENARIO":
      return "사건에 입력한 예정 치료";
    default:
      return "계산에 연결된 근거 자료";
  }
}

function sourceLabels(refs: GuidanceSourceReference[] | undefined): string {
  return [...new Set((refs ?? []).map(sourceLabel))].join(", ");
}

export function EvidencePages({
  evidence,
}: {
  evidence: (GuidanceEvidence | GuidanceSemanticEvidence)[];
}) {
  const pages = [
    ...new Map(
      evidence.map((item) => {
        const key =
          "citation_id" in item
            ? `${item.publication_id}:${item.citation_id}`
            : item.evidence_id;
        return [`${key}:${item.page_start}:${item.page_end}`, item] as const;
      }),
    ).values(),
  ];
  return pages.length ? (
    <div className={styles.certificateEvidence}>
      <strong>근거 페이지</strong>
      <ul>
        {pages.map((item, index) => (
          <li key={index}>
            {item.kind === "OPERATIONAL_EVIDENCE" ? "가입 문서" : "약관"}{" "}
            {pageLabel(item.page_start, item.page_end)}
          </li>
        ))}
      </ul>
    </div>
  ) : null;
}

const roundingLabels: Record<
  NonNullable<GuidanceCalculationStep["rounding_rule"]>,
  string
> = {
  half_up: "중간값은 올리는 반올림",
  half_even: "중간값은 짝수로 반올림",
  up: "올림",
  down: "버림",
};

function stepExpression(step: GuidanceCalculationStep): string {
  const values = step.operands.map((operand) =>
    unitValue(operand.value, operand.unit, operand.currency),
  );
  let expression: string;
  switch (step.operation) {
    case "add":
      expression = values.join(" + ");
      break;
    case "subtract":
      expression = values.join(" − ");
      break;
    case "multiply":
      expression = values.join(" × ");
      break;
    case "min":
      expression = `작은 값 선택 (${values.join(", ")})`;
      break;
    case "max":
      expression = `큰 값 선택 (${values.join(", ")})`;
      break;
    case "round":
      expression = `자리수 조정 (${values.join(", ")})`;
      break;
  }
  return `${expression} = ${unitValue(step.value, step.unit, step.currency)}`;
}

function CalculationTrace({
  trace,
  evidence,
  inputLabel,
}: {
  trace: GuidanceCalculationTrace;
  evidence: (GuidanceEvidence | GuidanceSemanticEvidence)[];
  inputLabel: (path: string) => string;
}) {
  return (
    <details className={panelStyles.trace}>
      <summary>계산 과정과 근거</summary>
      {trace.status !== "COMPLETE" ? (
        <p>
          아직 계산되지 않은 부분이 있습니다. 아래 중간 계산값은 전체 지급
          예상액이 아닙니다.
        </p>
      ) : null}
      <ol className={panelStyles.steps}>
        {(trace.steps ?? []).map((step) => (
          <li key={step.expression_path}>
            <p className={panelStyles.formula}>{stepExpression(step)}</p>
            {step.rounding_rule ? (
              <p>자리수 처리: {roundingLabels[step.rounding_rule]}</p>
            ) : null}
            {step.status !== "AVAILABLE" ? (
              <p>
                {step.status === "FAILED"
                  ? "이 단계의 계산을 완료하지 못했습니다."
                  : "이 단계에 필요한 값을 확인해야 합니다."}
              </p>
            ) : null}
            <ul>
              {step.operands.map((operand, index) => (
                <li key={operand.expression_path}>
                  {operand.kind === "FIELD"
                    ? inputLabel(operand.field_path ?? "")
                    : operand.kind === "LITERAL"
                      ? "문서 계산식의 값"
                      : `앞 단계 계산값 ${index + 1}`}
                  : {unitValue(operand.value, operand.unit, operand.currency)}
                  {operand.provenance
                    ? ` · ${provenanceLabel(operand.provenance)}`
                    : null}
                  {operand.stale ? " · 이전 정보로 다시 확인 필요" : null}
                  {operand.status !== "AVAILABLE" &&
                  operand.supplied_value != null
                    ? ` · 입력값(${unitValue(operand.supplied_value, operand.unit, operand.currency)})은 계산에 사용하지 않음`
                    : null}
                  {sourceLabels(operand.source_refs)
                    ? ` · ${sourceLabels(operand.source_refs)}`
                    : null}
                </li>
              ))}
            </ul>
            {sourceLabels(step.unit_source_refs) ? (
              <p>단위 근거: {sourceLabels(step.unit_source_refs)}</p>
            ) : null}
          </li>
        ))}
      </ol>
      {!trace.steps?.length ? (
        <p>계산식 결과: {unitValue(trace.value, trace.unit, trace.currency)}</p>
      ) : null}
      {trace.missing_paths?.length ? (
        <p>
          필요한 정보:{" "}
          {[...new Set(trace.missing_paths.map(inputLabel))].join(", ")}
        </p>
      ) : null}
      {sourceLabels(trace.source_refs) ? (
        <p>계산식 근거: {sourceLabels(trace.source_refs)}</p>
      ) : null}
      <EvidencePages evidence={evidence} />
    </details>
  );
}

export function Estimate({
  estimate,
  inputLabel,
}: {
  estimate: GuidanceEstimate;
  inputLabel: (path: string) => string;
}) {
  let amount: string | null = null;
  if (estimate.kind === "POINT" && estimate.amount != null)
    amount = money(estimate.amount, estimate.currency);
  else if (
    estimate.kind === "RANGE" &&
    estimate.lower != null &&
    estimate.upper != null
  )
    amount = `${money(estimate.lower, estimate.currency)} ~ ${money(estimate.upper, estimate.currency)}`;
  return (
    <div className={panelStyles.estimate}>
      <strong>
        {estimate.basis === "USER_SCENARIO"
          ? "가정에 따른 예상 금액"
          : estimate.kind === "FORMULA"
            ? "예상 금액 계산식"
            : "예상 금액"}
      </strong>
      {amount ? <p className={styles.subtotalAmount}>{amount}</p> : null}
      {estimate.formula ? (
        <p className={panelStyles.formula}>{estimate.formula}</p>
      ) : null}
      {estimate.kind === "FORMULA" && estimate.partial_amount != null ? (
        <div className={panelStyles.partial}>
          <strong>계산된 부분</strong>
          <p className={styles.subtotalAmount}>
            {money(estimate.partial_amount, estimate.currency)}
          </p>
          <p>
            확인된 비용 중 계산할 수 있는 부분입니다. 전체 지급 예상액이나 최소
            수령액이 아니며, 나머지는 미산정입니다.
          </p>
        </div>
      ) : null}
      {estimate.kind === "UNAVAILABLE" || (!amount && !estimate.formula) ? (
        <p className={styles.cardCopy}>예상 금액을 계산할 자료가 부족합니다.</p>
      ) : null}
      {estimate.basis === "SOURCE_ALTERNATIVES" ? (
        <p className={styles.cardCopy}>
          나열된 원문 조건 중 하나가 적용될 때의 금액 범위입니다. 최소 수령액을
          보장하지 않으며, 각 경우의 금액을 더하지 않습니다.
        </p>
      ) : null}
      {estimate.assumptions?.includes("CONDITIONS_REMAIN") ? (
        <p className={styles.cardCopy}>
          남은 조건이 충족되는 경우의 예상입니다.
        </p>
      ) : null}
      {estimate.missing_inputs?.length ? (
        <p className={styles.cardCopy}>
          계산에 필요한 정보:{" "}
          {[...new Set(estimate.missing_inputs.map(inputLabel))].join(", ")}
        </p>
      ) : null}
      {estimate.trace ? (
        <CalculationTrace
          trace={estimate.trace}
          evidence={estimate.evidence ?? []}
          inputLabel={inputLabel}
        />
      ) : null}
    </div>
  );
}

export function ContractAmount({ value }: { value: GuidanceContractAmount }) {
  return (
    <div className={panelStyles.contractAmount}>
      <strong>계약에 기록된 가입금액</strong>
      <p>
        {value.amount == null
          ? "가입금액 미확인"
          : money(value.amount, value.currency)}
      </p>
      <p className={styles.cardCopy}>
        금액: {provenanceLabel(value.amount_authority)} · 통화:{" "}
        {provenanceLabel(value.currency_authority)}
      </p>
      <p className={styles.cardCopy}>
        가입금액에 해당 담보의 계산식을 적용해 지급 예상액을 구합니다.
      </p>
      {value.evidence?.length ? (
        <ul>
          {value.evidence.map((item, index) => (
            <li key={index}>
              {"document_alias" in item ? item.document_alias : "가입 문서"}{" "}
              {pageLabel(item.page_start, item.page_end)}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function Scenarios({
  scenarios,
  inputLabel,
}: {
  scenarios: GuidanceScenario[];
  inputLabel: (path: string) => string;
}) {
  return scenarios.map((scenario, index) => (
    <section
      className={panelStyles.scenario}
      key={scenario.scenario_key}
      aria-label={`예정 치료 가정 ${index + 1}`}
    >
      <h4>
        예정된 치료를 가정한 경우{scenarios.length > 1 ? ` ${index + 1}` : ""}
      </h4>
      <p>
        실제로 치료받았다는 확인이 아닙니다. 아래 가정대로 치료받고 지급 조건을
        충족하는 경우의 예상입니다.
      </p>
      <ul>
        {scenario.hypotheses.map((hypothesis, hypothesisIndex) => (
          <li key={`${hypothesis.field_path}:${hypothesisIndex}`}>
            {inputLabel(hypothesis.field_path)}:{" "}
            {hypothesis.field_path === "MedicalEvent.admission_days"
              ? `${hypothesis.value}일`
              : typeof hypothesis.value === "boolean"
                ? hypothesis.value
                  ? "해당"
                  : "해당하지 않음"
                : String(hypothesis.value)}{" "}
            가정
          </li>
        ))}
      </ul>
      <Estimate estimate={scenario.estimate} inputLabel={inputLabel} />
    </section>
  ));
}

function CaseContractDetails({
  sourceCase,
  showAmount,
}: {
  sourceCase: GuidancePayoutCase;
  showAmount: boolean;
}) {
  return (
    <>
      {showAmount && sourceCase.contract_amount ? (
        <ContractAmount value={sourceCase.contract_amount} />
      ) : null}
      {sourceCase.freshness === "DOCUMENT_CONTINUITY" ? (
        <p>이 경우는 문서상 계약 유지 가정에 따른 안내입니다.</p>
      ) : null}
      {sourceCase.freshness === "CONFIRMED_AT_EVENT" ? (
        <p>이 경우는 사건일의 계약 상태가 확인되었습니다.</p>
      ) : null}
      {sourceCase.freshness === "STATUS_UNRESOLVED" ? (
        <p>이 경우는 사건일의 계약 상태에 따라 달라질 수 있습니다.</p>
      ) : null}
    </>
  );
}

export function CandidateAmounts({
  candidate,
  inputLabel,
}: {
  candidate: GuidanceCandidate;
  inputLabel: (path: string) => string;
}) {
  const cases = candidate.cases ?? [];
  // A single source case is the candidate estimate, so render it only once.
  if (cases.length < 2)
    return (
      <>
        {cases[0] ? (
          <CaseContractDetails
            sourceCase={cases[0]}
            showAmount={!candidate.contract_amount}
          />
        ) : null}
        {cases[0]?.conditions?.length ? (
          <ul>
            {cases[0].conditions.map((condition, index) => (
              <li key={index}>
                세부 조건 {index + 1}:{" "}
                {condition.result === "MATCH"
                  ? "일치"
                  : condition.result === "NO_MATCH"
                    ? "불일치"
                    : "확인 필요"}
              </li>
            ))}
          </ul>
        ) : null}
        <Estimate
          estimate={cases[0]?.estimate ?? candidate.estimate}
          inputLabel={inputLabel}
        />
        <Scenarios
          scenarios={
            cases[0]?.scenarios?.length
              ? cases[0].scenarios
              : (candidate.scenarios ?? [])
          }
          inputLabel={inputLabel}
        />
      </>
    );
  return (
    <div className={panelStyles.cases}>
      <p>
        {candidate.case_relation === "MUTUALLY_EXCLUSIVE"
          ? "각 경우는 함께 적용되지 않습니다. 해당하는 경우의 금액을 확인하세요."
          : "각 경우의 적용 관계는 아직 확인되지 않았습니다. 금액을 합산하지 않습니다."}
      </p>
      {cases.map((sourceCase, index) => (
        <section
          className={panelStyles.sourceCase}
          key={sourceCase.case_key}
          aria-label={`원문 조건 ${index + 1}`}
        >
          <h4>
            원문 조건 {index + 1} ·{" "}
            {sourceCase.benefit_kind === "FIXED"
              ? "정액형"
              : sourceCase.benefit_kind === "INDEMNITY"
                ? "실손형"
                : "보장 유형 확인 필요"}
          </h4>
          <p>
            {sourceCase.condition_result === "MATCH"
              ? "입력한 사건이 이 경우의 확인된 조건과 일치합니다."
              : "이 경우의 적용 조건에 추가 확인이 필요합니다."}
          </p>
          <CaseContractDetails
            sourceCase={sourceCase}
            showAmount={!candidate.contract_amount}
          />
          {sourceCase.conditions?.length ? (
            <ul>
              {sourceCase.conditions.map((condition, conditionIndex) => (
                <li key={conditionIndex}>
                  세부 조건 {conditionIndex + 1}:{" "}
                  {condition.result === "MATCH"
                    ? "일치"
                    : condition.result === "NO_MATCH"
                      ? "불일치"
                      : "확인 필요"}
                </li>
              ))}
            </ul>
          ) : null}
          <Estimate estimate={sourceCase.estimate} inputLabel={inputLabel} />
          <Scenarios
            scenarios={sourceCase.scenarios ?? []}
            inputLabel={inputLabel}
          />
          <EvidencePages
            evidence={[
              ...(sourceCase.conditions ?? []).flatMap(
                (condition) => condition.evidence,
              ),
              ...(sourceCase.estimate.evidence ?? []),
            ]}
          />
          {sourceCase.questions?.length ? (
            <p>
              이 경우에 필요한 정보:{" "}
              {[
                ...new Set(
                  sourceCase.questions.map((question) =>
                    inputLabel(question.field_path),
                  ),
                ),
              ].join(", ")}
            </p>
          ) : null}
        </section>
      ))}
    </div>
  );
}

function CostGroup({
  group,
  label,
  currency,
}: {
  group: GuidanceCostGroup;
  label: string;
  currency: string;
}) {
  return (
    <div className={panelStyles.costGroup}>
      <dt>{label}</dt>
      <dd>
        {group.total_cost != null ? (
          money(group.total_cost, currency)
        ) : group.known_cost != null ? (
          <>금액이 있는 항목만 {money(group.known_cost, currency)}</>
        ) : (
          "금액이 등록되지 않았습니다."
        )}
        {group.unknown_amount_line_ids.length ? (
          <span>
            금액 미확인 {group.unknown_amount_line_ids.length}건 · 미확인 금액은
            포함하지 않았습니다.
          </span>
        ) : null}
      </dd>
    </div>
  );
}

export function Expenses({ expenses }: { expenses: GuidanceExpenses }) {
  return (
    <section className={panelStyles.expenses} aria-label="등록된 비용">
      <h2>등록된 비용</h2>
      <p>등록한 지출 내역입니다. 지급 예상액과는 다르며 통화별로 표시합니다.</p>
      {expenses.status === "UNAVAILABLE" ? (
        <p>등록된 비용을 확인하지 못했습니다.</p>
      ) : expenses.status === "EMPTY" ? (
        <p>등록된 비용이 없습니다.</p>
      ) : null}
      {expenses.currencies.map((group) => (
        <div key={group.currency}>
          <h3>{group.currency}</h3>
          <dl>
            <CostGroup
              group={group.covered}
              label="보장대상으로 확인한 비용"
              currency={group.currency}
            />
            <CostGroup
              group={group.excluded}
              label="제외로 확인한 비용"
              currency={group.currency}
            />
            <CostGroup
              group={group.coverage_review}
              label="보장 여부 확인이 필요한 비용"
              currency={group.currency}
            />
            <CostGroup
              group={group.unconfirmed}
              label="아직 확인하지 않은 비용"
              currency={group.currency}
            />
          </dl>
        </div>
      ))}
      {expenses.unassigned_line_ids.length ? (
        <p>
          통화가 확인되지 않은 비용 {expenses.unassigned_line_ids.length}건은 위
          금액에 포함하지 않았습니다.
        </p>
      ) : null}
      {expenses.status === "PARTIAL" ? (
        <p>일부 비용 정보가 미확인 상태입니다.</p>
      ) : null}
    </section>
  );
}
