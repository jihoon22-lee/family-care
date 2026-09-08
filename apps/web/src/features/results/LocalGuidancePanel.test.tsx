import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type {
  CoverageDecisionResponse,
  GuidanceCandidate,
  GuidanceEstimate,
  LocalGuidanceResponse,
} from "../../api/generated";
import { ActionFirstResult } from "./ActionFirstResult";

function candidate(
  label = "Sample Coverage A",
  estimate: GuidanceEstimate = {
    kind: "POINT",
    amount: "120000",
    currency: "KRW",
    reason_code: "DOCUMENT_BASED_ESTIMATE",
  },
): GuidanceCandidate {
  return {
    ref: {
      kind: "PRIVATE_KNOWLEDGE_COVERAGE",
      contract_id: "synthetic-policy-001",
      coverage_id: `synthetic-coverage-${label}`,
    },
    contract_label: "Sample Policy",
    coverage_label: label,
    benefit_kind: "FIXED",
    group: "PRIMARY",
    freshness: "DOCUMENT_CONTINUITY",
    condition_result: "MATCH",
    assumptions: ["DOCUMENT_CONTINUITY_ASSUMED"],
    reason_codes: ["DOCUMENTED_RELEVANT_COVERAGE"],
    estimate,
    conditions: [
      {
        rule_id: "synthetic-rule-001",
        reason_code: "CONDITION_MATCH",
        result: "MATCH",
        evidence: [
          {
            kind: "TERMS_SECTION",
            evidence_id: "synthetic-section-001",
            page_start: 7,
            page_end: 9,
          },
        ],
      },
    ],
  };
}

function guidance(candidates = [candidate()]): LocalGuidanceResponse {
  return {
    candidates,
    event_date: "2026-09-01",
    event_version: 1,
    family_member_id: "synthetic-member-001",
    medical_event_id: "synthetic-event-001",
    outcome: "CANDIDATES",
    support: {
      evaluated_coverages: 1,
      total_coverages: 1,
      unsupported_coverages: 0,
    },
    versions: {},
  };
}

function result(
  localGuidance: LocalGuidanceResponse | null,
): CoverageDecisionResponse {
  return {
    local_guidance: localGuidance,
    analysis_completeness: "COMPLETE",
    assistance: {
      mode: "STRUCTURED_SEARCH",
      model_label: null,
      outcome_code: "LOCAL_SEARCH_READY",
      recommendations: [],
      state: "SEARCH_READY",
    },
    candidates: [
      {
        aggregate_result: "UNKNOWN",
        benefit_kind: "FIXED",
        calculation: null,
        candidate_id: "synthetic-legacy-candidate",
        claim_start_ready: false,
        contract_label: "Sample Policy",
        coverage_label: "Legacy Coverage",
        hold_reason_codes: [],
        questions: [],
        required_match_count: 0,
        required_no_match_count: 0,
        required_unknown_count: 1,
        source: {
          kind: "PRIVATE_KNOWLEDGE_COVERAGE",
          knowledge_contract_id: "synthetic-policy-001",
          knowledge_coverage_id: "synthetic-coverage-001",
        },
      },
    ],
    evaluations: [],
    catalog_coverage: {
      advisory_coverage_count: 0,
      benefit_coverage_count: 1,
      blocked_coverage_count: 0,
      contract_count: 1,
      not_applicable_coverage_count: 0,
      published_coverage_count: 1,
    },
    conditional_fixed_subtotals: [],
    engine_version: "synthetic-engine-001",
    event_version: 1,
    indemnity_summary: {
      calculated_candidate_count: 0,
      candidate_count: 0,
      status: "NONE",
      unresolved_candidate_count: 0,
    },
    knowledge_snapshot_version: {
      catalog_import_run_id: null,
      event_fact_schema_version: "medical-event-facts.v2",
      rule_import_run_id: null,
    },
    medical_event_id: "synthetic-event-001",
    policy_snapshot_at: "2026-09-01T00:00:00Z",
    rule_set_version: "synthetic-rules-001",
    run_id: "synthetic-run-001",
    schema_version: "2",
    source_failure_codes: [],
    stale: false,
  };
}

function show(response: CoverageDecisionResponse) {
  const onReanalyze = vi.fn();
  const onOpenEvidence = vi.fn();
  const onStartClaim = vi.fn();
  render(
    <ActionFirstResult
      result={response}
      onReanalyze={onReanalyze}
      onOpenEvidence={onOpenEvidence}
      onStartClaim={onStartClaim}
    />,
  );
  return { onReanalyze, onOpenEvidence, onStartClaim };
}

describe("local guidance result", () => {
  it("shows semantic original citations as terms and preserves distinct source references", () => {
    const value = candidate();
    value.conditions = [];
    value.estimate.evidence = [1, 2].map((number) => ({
      kind: "SEMANTIC_CITATION",
      citation_id: `synthetic-citation-${number}`,
      publication_id: "synthetic-publication-001",
      document_version_id: "synthetic-document-001",
      terms_edition_id: "synthetic-edition-001",
      generation_id: "synthetic-generation-001",
      root_node_id: "synthetic-root-001",
      source_node_id: "synthetic-node-001",
      page_start: 3,
      page_end: 3,
      start: number * 10,
      end: number * 10 + 5,
      source_layer: "native",
      bbox: [0, 0, 10, 10],
      source_sha256: "a".repeat(64),
      manifest_sha256: "b".repeat(64),
    }));
    show(result(guidance([value])));
    expect(screen.getAllByText(/약관.*3/)).toHaveLength(2);
    expect(screen.queryByText(/가입 문서.*3/)).not.toBeInTheDocument();
  });
  it.each([true, false])(
    "preserves operational claim actions when private guidance has candidates: %s",
    async (hasPrivateCandidates) => {
      const response = result(
        hasPrivateCandidates
          ? guidance()
          : {
              ...guidance([]),
              outcome: "KNOWLEDGE_PENDING",
              support: {
                total_coverages: 1,
                evaluated_coverages: 0,
                unsupported_coverages: 1,
              },
            },
      );
      response.candidates.push({
        aggregate_result: "MATCH",
        benefit_kind: "FIXED",
        calculation: null,
        candidate_id: "synthetic-operational-candidate",
        claim_start_ready: true,
        contract_label: "Sample Operational Policy",
        coverage_label: "Sample Operational Coverage",
        hold_reason_codes: [],
        questions: [],
        required_match_count: 1,
        required_no_match_count: 0,
        required_unknown_count: 0,
        source: {
          kind: "OPERATIONAL_RIDER",
          rider_id: "synthetic-operational-rider",
        },
      });
      const { onStartClaim } = show(response);
      expect(
        screen.getByText("Sample Operational Coverage"),
      ).toBeInTheDocument();
      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: /청구 검토 시작/ }));
      expect(onStartClaim).toHaveBeenCalledWith("synthetic-operational-rider");
      if (!hasPrivateCandidates) {
        expect(
          screen.getByRole("button", { name: "다시 확인" }),
        ).toBeInTheDocument();
      }
      expect(
        screen.queryByText(/관련 담보를 확인하지 못했습니다/),
      ).not.toBeInTheDocument();
    },
  );

  it("keeps successful guidance visible while retrying unsupported coverages", async () => {
    const response = result({
      ...guidance(),
      support: {
        total_coverages: 2,
        evaluated_coverages: 1,
        unsupported_coverages: 1,
        failure_codes: ["GUIDANCE_COVERAGE_FAILED"],
      },
    });
    const { onReanalyze } = show(response);
    expect(screen.getByText("120,000원")).toBeInTheDocument();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "다시 확인" }));
    expect(onReanalyze).toHaveBeenCalledOnce();
    expect(screen.getByText("120,000원")).toBeInTheDocument();
  });

  it("shows document-based coverage and money despite an UNKNOWN legacy result", () => {
    const { onOpenEvidence } = show(result(guidance()));
    expect(
      screen.getByRole("heading", { name: "주요 후보" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Sample Coverage A" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Sample Policy")).toBeInTheDocument();
    expect(screen.getByText("120,000원")).toBeInTheDocument();
    expect(
      screen.getByText(/문서에 기록된 계약이 사건일까지 유지된 것으로 가정/),
    ).toBeInTheDocument();
    expect(screen.getByText("약관 7–9쪽")).toBeInTheDocument();
    expect(screen.queryByText("Legacy Coverage")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/현재 바로 시작할 청구 검토 대상이 없습니다/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /청구|근거/ }),
    ).not.toBeInTheDocument();
    expect(onOpenEvidence).not.toHaveBeenCalled();
  });

  it("keeps point, range, formula and unavailable estimates with independent questions", () => {
    const conditional = candidate("Sample Coverage B", {
      kind: "RANGE",
      lower: "10000",
      upper: "25000",
      currency: "KRW",
      reason_code: "SCENARIO_RANGE",
    });
    conditional.group = "CONDITIONAL";
    conditional.condition_result = "UNKNOWN";
    conditional.questions = [
      {
        field_path: "MedicalEvent.admission_days",
        reason_code: "EVENT_FACT_NEEDED",
      },
    ];
    show(
      result(
        guidance([
          candidate(),
          conditional,
          candidate("Sample Coverage C", {
            kind: "FORMULA",
            formula: "일당 × 입원 일수",
            missing_inputs: ["MedicalEvent.admission_days"],
            reason_code: "CALCULATION_INPUT_NEEDED",
          }),
          candidate("Sample Coverage D", {
            kind: "UNAVAILABLE",
            reason_code: "CALCULATION_NOT_PUBLISHED",
          }),
        ]),
      ),
    );
    const conditionalGroup = screen.getByRole("region", {
      name: "조건에 따라 달라지는 후보",
    });
    expect(
      within(conditionalGroup).getByRole("heading", {
        name: "Sample Coverage B",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("120,000원")).toBeInTheDocument();
    expect(screen.getByText("10,000원 ~ 25,000원")).toBeInTheDocument();
    expect(screen.getByText("일당 × 입원 일수")).toBeInTheDocument();
    expect(
      screen.getByText("예상 금액을 계산할 자료가 부족합니다."),
    ).toBeInTheDocument();
    const questions = screen.getByRole("region", {
      name: "결과를 더 구체화할 정보",
    });
    expect(
      within(questions).getAllByText("입원 일수를 알려주세요."),
    ).toHaveLength(1);
    expect(
      screen.queryByText(
        /UNKNOWN|CONDITIONAL|CALCULATION_NOT_PUBLISHED|MedicalEvent/,
      ),
    ).not.toBeInTheDocument();
  });

  it.each([
    ["NO_RELEVANT_COVERAGE", "현재 사건과 관련된 가입 담보를 찾지 못했습니다."],
    ["INPUT_UNRESOLVED", "사건 내용을 조금 더 구체적으로 입력해 주세요."],
    [
      "KNOWLEDGE_PENDING",
      "보험 자료를 불러오거나 해석하지 못해 관련 담보를 확인하지 못했습니다.",
    ],
  ] as const)(
    "distinguishes %s without showing legacy candidates",
    (outcome, copy) => {
      show(result({ ...guidance([]), outcome }));
      expect(screen.getByText(copy)).toBeInTheDocument();
      expect(screen.queryByText("Legacy Coverage")).not.toBeInTheDocument();
    },
  );

  it("keeps current source guidance readable when legacy freshness is unresolved", () => {
    show({
      ...result({ ...guidance(), schema_version: "2" }),
      stale: true,
      local_guidance_stale: false,
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("120,000원")).toBeInTheDocument();
  });

  it("warns when the sources of a saved guidance result have changed", () => {
    show({
      ...result({ ...guidance(), schema_version: "2" }),
      stale: false,
      local_guidance_stale: true,
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "다시 분석이 필요합니다",
    );
    expect(screen.getByText("120,000원")).toBeInTheDocument();
  });

  it("keeps the stale-result warning and keyboard reanalysis action", async () => {
    const user = userEvent.setup();
    const { onReanalyze } = show({ ...result(guidance()), stale: true });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "다시 분석이 필요합니다",
    );
    expect(screen.getByText("120,000원")).toBeInTheDocument();
    await user.tab();
    expect(screen.getByRole("button", { name: "다시 분석" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(onReanalyze).toHaveBeenCalledOnce();
  });

  it("preserves the legacy presentation when local guidance is absent", () => {
    show(result(null));
    expect(
      screen.getByRole("heading", { name: "지금 할 일" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Legacy Coverage")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "주요 후보" }),
    ).not.toBeInTheDocument();
  });
});

it("explains a numeric source conflict while preserving the candidate and formula", () => {
  const source = candidate("Sample Linked Coverage", {
    kind: "FORMULA",
    currency: "KRW",
    formula: "가입금액 × 1",
    reason_code: "CALCULATION_INPUT_NEEDED",
    missing_inputs: ["Rider.insured_amount"],
  });
  const identity = {
    ref: {
      kind: "OPERATIONAL_RIDER" as const,
      contract_id: "synthetic-policy-002",
      coverage_id: "synthetic-rider-002",
    },
    source_refs: [
      source.ref,
      {
        kind: "OPERATIONAL_RIDER" as const,
        contract_id: "synthetic-policy-002",
        coverage_id: "synthetic-rider-002",
      },
    ],
    authority: "PROGRAM_VERIFIED_SOURCE_IDENTITY" as const,
    ledger_version: 2,
    verification_digest_sha256: "a".repeat(64),
    field_conflicts: ["insured_amount" as const],
  };
  render(
    <ActionFirstResult
      onOpenEvidence={vi.fn()}
      onReanalyze={vi.fn()}
      onStartClaim={vi.fn()}
      result={result(
        guidance([
          { ...source, ref: identity.ref, canonical_identity: identity },
        ]),
      )}
    />,
  );
  expect(
    screen.getByRole("heading", { name: "Sample Linked Coverage" }),
  ).toBeInTheDocument();
  expect(
    screen.getByText("가입 분석과 앱 원장의 금액이 달라 계산식만 안내합니다."),
  ).toBeInTheDocument();
  expect(screen.getByText("가입금액 × 1")).toBeInTheDocument();
  expect(screen.queryByText("Legacy Coverage")).not.toBeInTheDocument();
});

function trace(): NonNullable<GuidanceEstimate["trace"]> {
  return {
    publication_id: "synthetic-trace-publication",
    source_revision: "synthetic-v1",
    source_digest_sha256: "a".repeat(64),
    formula_digest_sha256: "b".repeat(64),
    runtime_revision: "synthetic-runtime-v1",
    status: "COMPLETE",
    value: "3",
    unit: "DAYS",
    currency: null,
    steps: [
      {
        step_number: 1,
        expression_path: "/calculation",
        operation: "subtract",
        operands: [
          {
            expression_path: "/calculation/args/0",
            kind: "FIELD",
            field_path: "MedicalEvent.admission_days",
            value: "5",
            unit: "DAYS",
            provenance: "DERIVED_CONFIRMED",
            status: "AVAILABLE",
          },
          {
            expression_path: "/calculation/args/1",
            kind: "LITERAL",
            value: "2",
            unit: "DAYS",
            status: "AVAILABLE",
          },
        ],
        value: "3",
        unit: "DAYS",
        status: "AVAILABLE",
      },
    ],
  };
}

function point(amount: string): GuidanceEstimate {
  return {
    kind: "POINT",
    amount,
    currency: "KRW",
    formula: "가입금액 × 지급 비율",
    reason_code: "DOCUMENT_BASED_ESTIMATE",
  };
}

describe("source-preserving guidance details", () => {
  it("separates certificate 100 from expected payout 300 and labels authority", () => {
    const value = candidate("Sample Detail", point("300"));
    value.contract_amount = {
      amount: "100",
      currency: "KRW",
      amount_authority: "PROGRAM_VERIFIED",
      currency_authority: "USER_CONFIRMED",
    };
    show(result(guidance([value])));
    expect(screen.getByText("100원")).toBeInTheDocument();
    expect(screen.getByText("300원")).toBeInTheDocument();
    expect(screen.getByText(/계약에 기록된 가입금액/)).toBeInTheDocument();
    expect(screen.getByText(/원문 대조/)).toBeInTheDocument();
  });

  it("labels 40000 as calculated portion separately from registered 50000 costs", () => {
    const value = candidate("Sample Partial", {
      kind: "FORMULA",
      currency: "KRW",
      formula: "보장대상 비용 × 0.8",
      partial_amount: "40000",
      basis: "CONFIRMED_COST_SUBSET",
      reason_code: "PARTIAL_COSTS",
      trace: trace(),
    });
    const response = guidance([value]);
    const empty = {
      line_ids: [],
      known_line_ids: [],
      unknown_amount_line_ids: [],
      known_cost: null,
      total_cost: null,
      source_refs: [],
    };
    response.expenses = {
      event_id: response.medical_event_id,
      event_version: 1,
      status: "AVAILABLE",
      reader_revision: "synthetic-v1",
      digest_sha256: "a".repeat(64),
      unassigned_line_ids: [],
      reason_codes: [],
      currencies: [
        {
          currency: "KRW",
          covered: {
            ...empty,
            line_ids: ["synthetic-line"],
            known_line_ids: ["synthetic-line"],
            known_cost: "50000",
            total_cost: "50000",
          },
          excluded: empty,
          coverage_review: empty,
          unconfirmed: empty,
        },
      ],
    };
    show(result(response));
    expect(screen.getByText("40,000원")).toBeInTheDocument();
    expect(screen.getByText("50,000원")).toBeInTheDocument();
    expect(screen.getByText(/계산된 부분/)).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "등록된 비용" }),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText(/금액이 등록되지 않았습니다/).length,
    ).toBeGreaterThan(0);
  });

  it("keeps planned 300 separate from the base formula and actual care", () => {
    const value = candidate("Sample Planned", {
      kind: "FORMULA",
      currency: "KRW",
      formula: "가입금액 × 입원 일수",
      reason_code: "MISSING_DAYS",
    });
    value.scenarios = [
      {
        scenario_key: "a".repeat(64),
        kind: "PLANNED_CARE",
        hypotheses: [
          {
            field_path: "MedicalEvent.admission_days",
            value: 5,
            spans: [{ start: 0, end: 5 }],
            source_refs: [
              {
                source_kind: "EVENT_SCENARIO",
                source_id: "synthetic-event",
                version: 1,
                digest_sha256: "a".repeat(64),
              },
            ],
          },
        ],
        estimate: {
          ...point("300"),
          basis: "USER_SCENARIO",
          assumptions: ["PLANNED_CARE_ASSUMED"],
        },
      },
    ];
    show(result(guidance([value])));
    expect(screen.getByText("300원")).toBeInTheDocument();
    expect(screen.getByText("가입금액 × 입원 일수")).toBeInTheDocument();
    expect(screen.getByText(/예정된 치료를 가정/)).toBeInTheDocument();
    expect(screen.getByText(/실제로 치료받았다는 확인/)).toBeInTheDocument();
  });

  it("shows source cases 100 and 200 without a fabricated combined 300", () => {
    const value = candidate("Sample Cases", {
      kind: "UNAVAILABLE",
      reason_code: "MULTIPLE_PAYOUT_CASES",
    });
    value.cases = ["100", "200"].map((amount, index) => ({
      case_key: `synthetic-case-${index}`,
      benefit_kind: "FIXED",
      condition_result: "UNKNOWN",
      reason_codes: ["SOURCE_CASE"],
      estimate: point(amount),
    }));
    value.case_relation = "UNRESOLVED";
    show(result(guidance([value])));
    expect(screen.getByText("100원")).toBeInTheDocument();
    expect(screen.getByText("200원")).toBeInTheDocument();
    expect(screen.queryByText("300원")).not.toBeInTheDocument();
    expect(
      screen.queryByText("예상 금액을 계산할 자료가 부족합니다."),
    ).not.toBeInTheDocument();
  });

  it("explains source-alternative ranges without promising a minimum", () => {
    show(
      result(
        guidance([
          candidate("Sample Range", {
            kind: "RANGE",
            currency: "KRW",
            lower: "100",
            upper: "200",
            formula: "원문 조건별 금액",
            basis: "SOURCE_ALTERNATIVES",
            reason_code: "SOURCE_CASE_RANGE",
          }),
        ]),
      ),
    );
    expect(screen.getByText(/나열된 원문 조건 중 하나/)).toBeInTheDocument();
    expect(screen.getByText(/최소 수령액을 보장/)).toBeInTheDocument();
  });

  it("opens a closed accessible arithmetic trace without displaying source IDs or AST paths", async () => {
    const value = candidate("Sample Trace", {
      ...point("300"),
      trace: trace(),
    });
    show(result(guidance([value])));
    const summary = screen.getByText("계산 과정과 근거");
    const details = summary.closest("details");
    expect(details).not.toHaveAttribute("open");
    await userEvent.setup().click(summary);
    expect(details).toHaveAttribute("open");
    expect(screen.getByText(/5일 − 2일 = 3일/)).toBeInTheDocument();
    expect(
      screen.queryByText(
        /synthetic-trace-publication|\/calculation|MedicalEvent/,
      ),
    ).not.toBeInTheDocument();
  });

  it("shows one source case only once", () => {
    const value = candidate("Sample Single Case", point("100"));
    value.cases = [
      {
        case_key: "synthetic-single-case",
        benefit_kind: "FIXED",
        condition_result: "MATCH",
        reason_codes: [],
        estimate: point("100"),
      },
    ];
    show(result(guidance([value])));
    expect(screen.getAllByText("100원")).toHaveLength(1);
    expect(screen.getAllByText("가입금액 × 지급 비율")).toHaveLength(1);
  });

  it("keeps currencies and all cost buckets separate, including a known zero", () => {
    const response = guidance();
    const empty = {
      line_ids: [],
      known_line_ids: [],
      unknown_amount_line_ids: [],
      known_cost: null,
      total_cost: null,
      source_refs: [],
    };
    response.expenses = {
      event_id: response.medical_event_id,
      event_version: 1,
      status: "PARTIAL",
      reader_revision: "synthetic-v1",
      digest_sha256: "a".repeat(64),
      unassigned_line_ids: ["synthetic-no-currency"],
      reason_codes: [],
      currencies: [
        {
          currency: "KRW",
          covered: { ...empty, known_cost: "50000", total_cost: "50000" },
          excluded: { ...empty, known_cost: "0", total_cost: "0" },
          coverage_review: {
            ...empty,
            unknown_amount_line_ids: ["synthetic-unknown"],
            known_cost: "12000",
          },
          unconfirmed: empty,
        },
        {
          currency: "USD",
          covered: { ...empty, known_cost: "500", total_cost: "500" },
          excluded: empty,
          coverage_review: empty,
          unconfirmed: { ...empty, known_cost: "80", total_cost: "80" },
        },
      ],
    };
    show(result(response));
    const costs = within(screen.getByRole("region", { name: "등록된 비용" }));
    expect(costs.getByText("0원")).toBeInTheDocument();
    expect(costs.getByText("50,000원")).toBeInTheDocument();
    expect(costs.getByText("500 USD")).toBeInTheDocument();
    expect(costs.getByText("80 USD")).toBeInTheDocument();
    expect(costs.getByText(/금액이 있는 항목만 12,000원/)).toBeInTheDocument();
    expect(
      costs.getByText(/통화가 확인되지 않은 비용 1건/),
    ).toBeInTheDocument();
    expect(costs.queryByText(/62,000|50,500|580 USD/)).not.toBeInTheDocument();
  });

  it("keeps every arithmetic operand and its rounding and unconfirmed input visible", async () => {
    const detail = trace();
    detail.status = "PARTIAL";
    detail.steps = [
      {
        step_number: 1,
        expression_path: "/calculation",
        operation: "round",
        operands: [
          {
            expression_path: "/calculation/args/0",
            kind: "FIELD",
            field_path: "Receipt.covered_amount",
            value: "123.45",
            unit: "MONEY",
            currency: "USD",
            provenance: "USER_CONFIRMED",
            status: "AVAILABLE",
          },
          {
            expression_path: "/calculation/args/1",
            kind: "LITERAL",
            value: "0",
            unit: "NUMBER",
            status: "AVAILABLE",
          },
          {
            expression_path: "/calculation/args/2",
            kind: "FIELD",
            field_path: "ClaimHistory.counted_occurrence",
            value: null,
            supplied_value: "1",
            unit: "COUNT",
            provenance: "AI_STRUCTURED",
            stale: true,
            status: "UNAVAILABLE",
          },
        ],
        value: null,
        unit: "MONEY",
        currency: "USD",
        status: "UNAVAILABLE",
        rounding_rule: "half_up",
      },
    ];
    const value = candidate("Sample Unfinished Trace", {
      kind: "FORMULA",
      formula: "원문 계산식",
      reason_code: "MISSING",
      trace: detail,
    });
    show(result(guidance([value])));
    const user = userEvent.setup();
    const summary = screen.getByText("계산 과정과 근거");
    summary.focus();
    expect(summary).toHaveFocus();
    await user.click(summary);
    expect(summary.closest("details")).toHaveAttribute("open");
    expect(
      screen.getByText(/자리수 조정 \(123.45 USD, 0, 미산정\)/),
    ).toBeInTheDocument();
    expect(screen.getByText(/중간값은 올리는 반올림/)).toBeInTheDocument();
    expect(screen.getByText(/AI 정리 · 확인 전/)).toHaveTextContent(
      "입력값(1회)은 계산에 사용하지 않음",
    );
    expect(screen.getByText(/전체 지급 예상액이 아닙니다/)).toBeInTheDocument();
  });
});
