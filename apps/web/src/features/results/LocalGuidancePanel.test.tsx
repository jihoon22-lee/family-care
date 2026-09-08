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
