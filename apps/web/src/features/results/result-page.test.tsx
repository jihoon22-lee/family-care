import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  BenefitCalculationResponse,
  BenefitCalculationsResponse,
  ClaimCandidateResponse,
  CoverageDecisionResponse,
  MedicalEventResponse,
  OperationalEvaluationResponse,
  RuleEvaluationResponse,
} from "../../api/generated";
import { ActionFirstResult } from "./ActionFirstResult";
import { EventResultPage } from "./EventResultPage";
import { renderWithProviders } from "../../test/renderWithProviders";

const EVENT_ID = "00000000-0000-4000-8000-000000000201";
const CANDIDATE_A = "00000000-0000-4000-8000-000000000301";
const CANDIDATE_B = "00000000-0000-4000-8000-000000000302";
const CANDIDATE_C = "00000000-0000-4000-8000-000000000303";
const RIDER_A = "00000000-0000-4000-8000-000000000401";
const RIDER_B = "00000000-0000-4000-8000-000000000402";
const RIDER_C = "00000000-0000-4000-8000-000000000403";
const EVIDENCE_ID = "00000000-0000-4000-8000-000000000501";
const PRIVATE_CONTRACT = "00000000-0000-4000-8000-000000000601";
const PRIVATE_COVERAGE = "00000000-0000-4000-8000-000000000602";

const EVENT = {
  deleted: false,
  event_date: "2026-08-25",
  facts: {},
  family_member_id: "00000000-0000-4000-8000-000000000101",
  id: EVENT_ID,
  mode: "post_treatment",
  situation: "Synthetic member received outpatient treatment.",
  version: 2,
  visit_date: "2026-08-26",
} satisfies MedicalEventResponse;

function candidate(
  candidateId: string,
  riderId: string,
  aggregateResult: ClaimCandidateResponse["aggregate_result"],
  holdReasonCodes: string[] = [],
): ClaimCandidateResponse {
  return {
    aggregate_result: aggregateResult,
    benefit_kind: "FIXED",
    calculation: null,
    candidate_id: candidateId,
    claim_start_ready: aggregateResult === "MATCH",
    contract_label: "Registered Sample Policy",
    coverage_label:
      riderId === RIDER_A
        ? "Sample Rider A"
        : riderId === RIDER_B
          ? "Sample Rider B"
          : "Sample Rider C",
    hold_reason_codes: holdReasonCodes,
    questions: [],
    required_match_count: aggregateResult === "MATCH" ? 1 : 0,
    required_no_match_count: aggregateResult === "NO_MATCH" ? 1 : 0,
    required_unknown_count: aggregateResult === "UNKNOWN" ? 1 : 0,
    source: { kind: "OPERATIONAL_RIDER", rider_id: riderId },
  };
}

function privateCandidate(
  candidateId: string,
  coverageId: string,
  aggregateResult: ClaimCandidateResponse["aggregate_result"],
  coverageLabel = "Sample Shared Coverage",
): ClaimCandidateResponse {
  return {
    aggregate_result: aggregateResult,
    benefit_kind: "FIXED",
    calculation:
      aggregateResult === "MATCH"
        ? {
            applied_limit: null,
            applied_rate: null,
            calculation_id: `${candidateId.slice(0, -3)}901`,
            calculation_publication_id: `${candidateId.slice(0, -3)}902`,
            conditional_amount: "300000",
            confirmed_amount: null,
            currency: "KRW",
            deductible_amount: null,
            excluded_amount: null,
            hold_reason_code: null,
            kind: "FIXED",
            rounding_rule: null,
            status: "CALCULATED",
            steps: [
              {
                currency: "KRW",
                input_amount: null,
                operation: "fixed_amount",
                output_amount: "300000",
                reason_code: "FIXED_AMOUNT_CALCULATED",
                rounding_rule: null,
                step_number: 1,
              },
            ],
          }
        : null,
    candidate_id: candidateId,
    claim_start_ready: false,
    contract_label: "Sample Private Policy",
    coverage_label: coverageLabel,
    hold_reason_codes: [],
    questions: [],
    required_match_count: aggregateResult === "MATCH" ? 1 : 0,
    required_no_match_count: aggregateResult === "NO_MATCH" ? 1 : 0,
    required_unknown_count: aggregateResult === "UNKNOWN" ? 1 : 0,
    source: {
      kind: "PRIVATE_KNOWLEDGE_COVERAGE",
      knowledge_contract_id: PRIVATE_CONTRACT,
      knowledge_coverage_id: coverageId,
    },
  };
}

function evaluation(
  riderId: string,
  result: OperationalEvaluationResponse["result"],
  reasonCode: string,
  overrides: Partial<OperationalEvaluationResponse> = {},
): OperationalEvaluationResponse {
  return {
    citations: [],
    conflicting_fields: [],
    engine_version: "synthetic-decision-engine-v1",
    evaluation_id: `synthetic-evaluation-${riderId.slice(-3)}`,
    fact_paths: [],
    missing_fields: [],
    reason_code: reasonCode,
    required: true,
    result,
    source: {
      kind: "OPERATIONAL_RIDER",
      rider_id: riderId,
      rule_version_id: `synthetic-rule-${riderId.slice(-3)}`,
    },
    ...overrides,
  };
}

function result(
  overrides: Partial<CoverageDecisionResponse> = {},
): CoverageDecisionResponse {
  return {
    analysis_completeness: "COMPLETE",
    assistance: {
      mode: "STRUCTURED_SEARCH",
      model_label: null,
      outcome_code: "LOCAL_SEARCH_READY",
      recommendations: [
        {
          citation: {
            fact_id: "00000000-0000-4000-8000-000000000811",
            kind: "FACT_CITATION",
            page_end: 7,
            page_start: 7,
            source_clause_id: "00000000-0000-4000-8000-000000000812",
            terms_section_id: "00000000-0000-4000-8000-000000000813",
          },
          clause_label: "Sample related clause",
          contract_label: "Sample Private Policy",
          coverage_label: "Sample Shared Coverage",
          excerpt: "Synthetic bounded clause excerpt.",
          explanation_code: null,
          question_code: null,
          rank: 1,
          reason_code: "TOKEN_OVERLAP",
          recommendation_id: "00000000-0000-4000-8000-000000000814",
        },
      ],
      state: "SEARCH_READY",
    },
    candidates: [
      candidate(CANDIDATE_A, RIDER_A, "MATCH"),
      candidate(CANDIDATE_B, RIDER_B, "UNKNOWN", ["RULE_READER_UNAVAILABLE"]),
      candidate(CANDIDATE_C, RIDER_C, "NO_MATCH"),
      privateCandidate(
        "00000000-0000-4000-8000-000000000304",
        PRIVATE_COVERAGE,
        "MATCH",
      ),
    ],
    catalog_coverage: {
      benefit_coverage_count: 1,
      blocked_coverage_count: 0,
      contract_count: 1,
      not_applicable_coverage_count: 0,
      published_coverage_count: 1,
    },
    conditional_fixed_subtotals: [
      {
        amount: "300000",
        calculated_candidate_count: 1,
        currency: "KRW",
        unresolved_candidate_count: 0,
      },
    ],
    engine_version: "synthetic-decision-engine-v1",
    evaluations: [
      evaluation(RIDER_A, "MATCH", "SYNTHETIC_MATCH", {
        citations: [
          {
            bbox: null,
            content_sha256: "a".repeat(64),
            document_version_id: "synthetic-document-version-001",
            evidence_id: EVIDENCE_ID,
            extraction_id: "synthetic-extraction-001",
            kind: "OPERATIONAL_EVIDENCE",
            physical_page: 4,
            review_state: "USER_CONFIRMED",
          },
        ],
      }),
      evaluation(RIDER_B, "UNKNOWN", "RULE_RUNTIME_INVALID"),
      evaluation(RIDER_C, "NO_MATCH", "SYNTHETIC_NO_MATCH"),
    ],
    event_version: 2,
    indemnity_summary: {
      calculated_candidate_count: 0,
      candidate_count: 1,
      status: "UNKNOWN",
      unresolved_candidate_count: 1,
    },
    knowledge_snapshot_version: {
      catalog_import_run_id: "00000000-0000-4000-8000-000000000821",
      event_fact_schema_version: "medical-event-facts.v2",
      rule_import_run_id: "00000000-0000-4000-8000-000000000822",
    },
    medical_event_id: EVENT_ID,
    policy_snapshot_at: "2026-08-25T09:00:00Z",
    rule_set_version: "synthetic-rule-set-v1",
    run_id: "00000000-0000-4000-8000-000000000601",
    schema_version: "2",
    source_failure_codes: [],
    stale: false,
    ...overrides,
  };
}

const CALCULATION = {
  additional: null,
  applied_limit: null,
  applied_rate: null,
  calculation_id: "00000000-0000-4000-8000-000000000701",
  claim_candidate_id: CANDIDATE_A,
  confirmed: { amount: "120000", currency: "KRW" },
  created_at: "2026-08-25T09:01:00Z",
  currency: "KRW",
  deductible: null,
  engine_version: "synthetic-benefit-engine-v1",
  evidence_ids: [EVIDENCE_ID],
  excluded: null,
  excluded_reason_codes: [],
  hold_reason_codes: [],
  kind: "fixed",
  rounding_rule: null,
  rule_version_id: "00000000-0000-4000-8000-000000000702",
  schema_version: "1",
  status: "computed",
  steps: [],
  version: 1,
} satisfies BenefitCalculationResponse;

const CALCULATIONS = {
  calculations: [CALCULATION],
  schema_version: "1",
} satisfies BenefitCalculationsResponse;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    headers: {
      "cache-control": "no-store",
      "content-type": "application/json",
    },
    status,
  });
}

function installFetch(
  event: MedicalEventResponse = EVENT,
  decision: CoverageDecisionResponse = result(),
): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), window.location.origin);
    if (url.pathname === `/api/v1/medical-events/${EVENT_ID}`) {
      return jsonResponse(event);
    }
    if (url.pathname === `/api/v1/medical-events/${EVENT_ID}/results/2`) {
      return jsonResponse(decision);
    }
    if (url.pathname === `/api/v1/medical-events/${EVENT_ID}/calculations`) {
      return jsonResponse(CALCULATIONS);
    }
    return jsonResponse({ error_code: "NOT_FOUND", message: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("action-first event results", () => {
  it("renders fixed group order and safe terminology from the generated result contract", async () => {
    installFetch();

    renderWithProviders(<EventResultPage eventId={EVENT_ID} version={2} />);

    const headings = await screen.findAllByRole("heading");
    expect(headings.map((heading) => heading.textContent)).toEqual([
      "현재 사건",
      "분석 범위",
      "지금 할 일",
      "조건부 정액 합계",
      "실손 보장",
      "청구 검토 대상",
      "추가 확인 필요",
      "조건 불일치",
      "관련 약관 추천",
    ]);
    expect(screen.queryByText(/지급 확정|지급 가능/)).not.toBeInTheDocument();
    expect(screen.getAllByText("Sample Rider A")).not.toHaveLength(0);
    expect(screen.queryByText(RIDER_A)).not.toBeInTheDocument();
    expect(screen.getByText("300000 KRW")).toBeInTheDocument();
    expect(screen.getByText(/실손 금액은 별도로 확인/)).toBeInTheDocument();
    expect(screen.getByText("DB 검색")).toBeInTheDocument();
  });

  it("keeps successful cards and safely reports rule failures as a partial result", async () => {
    installFetch();

    renderWithProviders(<EventResultPage eventId={EVENT_ID} version={2} />);

    expect(await screen.findAllByText("Sample Rider A")).not.toHaveLength(0);
    expect(screen.getByRole("status")).toHaveTextContent(
      "1개 항목을 다시 확인할 수 있습니다",
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "보장 규칙을 불러오지 못했습니다",
    );
    expect(screen.getByRole("status")).not.toHaveTextContent(
      "RULE_READER_UNAVAILABLE",
    );
  });

  it("does not mix current calculations into an older event result", async () => {
    const fetchMock = installFetch(
      { ...EVENT, version: 3 },
      result({ event_version: 2, stale: true }),
    );

    renderWithProviders(<EventResultPage eventId={EVENT_ID} version={2} />);

    expect(
      await screen.findByRole("heading", { name: "현재 사건" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("120000 KRW")).not.toBeInTheDocument();
    expect(
      screen.getByText(/현재 사건 버전과 다른 결과이므로/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /청구 검토 시작/ }),
    ).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([, init]) => init?.method === "POST"),
    ).toBe(false);
  });

  it("renders stale metadata and server calculations without doing browser arithmetic", () => {
    const onStartClaim = vi.fn();
    const onOpenEvidence = vi.fn();
    const onReanalyze = vi.fn();

    renderWithProviders(
      <ActionFirstResult
        result={result({
          stale: true,
          event_version: 4,
        })}
        calculations={CALCULATIONS}
        onStartClaim={onStartClaim}
        onOpenEvidence={onOpenEvidence}
        onReanalyze={onReanalyze}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "다시 분석이 필요합니다",
    );
    expect(
      within(screen.getByRole("alert")).getByText("사건 버전").parentElement,
    ).toHaveTextContent("4");
    expect(screen.getByText("120000 KRW")).toBeInTheDocument();
    expect(screen.getByText("서버 계산 결과")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /다시 분석/i }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /근거 보기/i })).toHaveLength(
      2,
    );
    expect(
      screen.queryByRole("button", { name: /청구 검토 시작/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("300000 KRW")).toBeInTheDocument();
    expect(screen.queryByText("420000 KRW")).not.toBeInTheDocument();
  });

  it("renders every private coverage independently and never offers private claim start", () => {
    const onStartClaim = vi.fn();
    const privateCandidates = [1, 2, 3, 4].map((index) =>
      privateCandidate(
        `00000000-0000-4000-8000-00000000031${index}`,
        `00000000-0000-4000-8000-00000000062${index}`,
        "MATCH",
        "Repeated Sample Coverage",
      ),
    );

    renderWithProviders(
      <ActionFirstResult
        result={result({
          candidates: [
            candidate(CANDIDATE_A, RIDER_A, "MATCH"),
            ...privateCandidates,
          ],
          conditional_fixed_subtotals: [
            {
              amount: "1200000",
              calculated_candidate_count: 4,
              currency: "KRW",
              unresolved_candidate_count: 0,
            },
          ],
          evaluations: [evaluation(RIDER_A, "MATCH", "SYNTHETIC_MATCH")],
        })}
        onStartClaim={onStartClaim}
        onOpenEvidence={vi.fn()}
        onReanalyze={vi.fn()}
      />,
    );

    expect(screen.getAllByText("Repeated Sample Coverage")).toHaveLength(4);
    expect(screen.getByText("1200000 KRW")).toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: /청구 검토 시작/ }),
    ).toHaveLength(2);
    expect(
      screen.queryByRole("button", { name: /Repeated Sample Coverage.*청구/ }),
    ).not.toBeInTheDocument();
  });

  it("explains unpublished enrolled coverage instead of claiming there is no insurance", () => {
    renderWithProviders(
      <ActionFirstResult
        result={result({
          analysis_completeness: "UNAVAILABLE",
          candidates: [],
          catalog_coverage: {
            benefit_coverage_count: 3,
            blocked_coverage_count: 3,
            contract_count: 2,
            not_applicable_coverage_count: 0,
            published_coverage_count: 0,
          },
          conditional_fixed_subtotals: [],
          evaluations: [],
          source_failure_codes: ["KNOWLEDGE_PUBLICATION_UNAVAILABLE"],
        })}
        onStartClaim={vi.fn()}
        onOpenEvidence={vi.fn()}
        onReanalyze={vi.fn()}
      />,
    );

    expect(
      screen.getByText(
        /가입 담보는 확인됐지만 실행 규칙 검토가 완료되지 않았습니다/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/보험이 없습니다|해당 보험 없음/),
    ).not.toBeInTheDocument();
    expect(screen.getByText("검토 대기").parentElement).toHaveTextContent(
      /검토 대기\s*3개/,
    );
  });

  it("keeps unknown questions and private clause pages keyboard accessible", async () => {
    const user = userEvent.setup();
    const unknown = privateCandidate(
      "00000000-0000-4000-8000-000000000341",
      PRIVATE_COVERAGE,
      "UNKNOWN",
    );
    unknown.questions = [
      {
        field_path: "MedicalEvent.procedure_code",
        reason_code: "PROCEDURE_CODE_REQUIRED",
      },
    ];
    const privateEvaluation: RuleEvaluationResponse = {
      citations: [
        {
          evidence_purpose: "ELIGIBILITY",
          fact_id: "00000000-0000-4000-8000-000000000851",
          kind: "PRIVATE_KNOWLEDGE_CITATION",
          page_end: 9,
          page_start: 8,
          source_clause_id: "00000000-0000-4000-8000-000000000852",
          terms_section_id: "00000000-0000-4000-8000-000000000853",
        },
      ],
      conflicting_fields: [],
      engine_version: "private-knowledge-engine-v1",
      evaluation_id: "00000000-0000-4000-8000-000000000854",
      fact_paths: ["MedicalEvent.procedure_code"],
      missing_fields: ["MedicalEvent.procedure_code"],
      reason_code: "PROCEDURE_CODE_REQUIRED",
      required: true,
      result: "UNKNOWN",
      source: {
        kind: "PRIVATE_KNOWLEDGE_COVERAGE",
        knowledge_coverage_id: PRIVATE_COVERAGE,
        rule_publication_id: "00000000-0000-4000-8000-000000000855",
      },
    };

    renderWithProviders(
      <ActionFirstResult
        result={result({
          candidates: [unknown],
          evaluations: [privateEvaluation],
        })}
        onStartClaim={vi.fn()}
        onOpenEvidence={vi.fn()}
        onReanalyze={vi.fn()}
      />,
    );

    expect(screen.getByText("처치·수술 코드")).toBeInTheDocument();
    const citationButton = screen.getByRole("button", {
      name: /약관 근거 보기/,
    });
    citationButton.focus();
    expect(citationButton).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(screen.getByText("8–9쪽")).toBeInTheDocument();
  });

  it("labels LLM assistance as a review-only recommendation stream", () => {
    renderWithProviders(
      <ActionFirstResult
        result={result({
          assistance: {
            ...result().assistance,
            mode: "LLM_ASSISTED",
            model_label: "synthetic-model-v1",
            state: "LLM_READY",
          },
        })}
        onStartClaim={vi.fn()}
        onOpenEvidence={vi.fn()}
        onReanalyze={vi.fn()}
      />,
    );

    expect(screen.getByText("LLM 보조")).toBeInTheDocument();
    expect(
      screen.getByText(/검토 후보이며 보험금 지급 판정이 아닙니다/),
    ).toBeInTheDocument();
    expect(screen.queryByText("synthetic-model-v1")).not.toBeInTheDocument();
  });

  it("keeps DB recommendations usable while polling only the assistance stream", async () => {
    const pending = result({
      assistance: {
        ...result().assistance,
        state: "LLM_PENDING",
      },
    });
    const ready = result({
      assistance: {
        ...result().assistance,
        mode: "LLM_ASSISTED",
        model_label: "synthetic-model-v1",
        recommendations: [
          {
            ...result().assistance.recommendations[0],
            coverage_label: "LLM Ordered Coverage",
            recommendation_id: "00000000-0000-4000-8000-000000000815",
          },
        ],
        state: "LLM_READY",
      },
    });
    let resultReads = 0;
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input), window.location.origin);
        if (url.pathname === `/api/v1/medical-events/${EVENT_ID}`) {
          return jsonResponse(EVENT);
        }
        if (url.pathname === `/api/v1/medical-events/${EVENT_ID}/results/2`) {
          resultReads += 1;
          return jsonResponse(resultReads === 1 ? pending : ready);
        }
        if (
          url.pathname === `/api/v1/medical-events/${EVENT_ID}/calculations`
        ) {
          return jsonResponse(CALCULATIONS);
        }
        void init;
        return jsonResponse(
          { error_code: "NOT_FOUND", message: "not found" },
          404,
        );
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<EventResultPage eventId={EVENT_ID} version={2} />);

    expect(await screen.findByText("DB 검색")).toBeInTheDocument();
    expect(
      screen.getByText("Synthetic bounded clause excerpt."),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Sample Rider A")).not.toHaveLength(0);
    expect(
      await screen.findByText("LLM Ordered Coverage", {}, { timeout: 2_500 }),
    ).toBeInTheDocument();
    expect(screen.getByText("LLM 보조")).toBeInTheDocument();
    expect(screen.getAllByText("Sample Rider A")).not.toHaveLength(0);
    expect(resultReads).toBe(2);
    expect(
      fetchMock.mock.calls.some(([, init]) => init?.method === "POST"),
    ).toBe(false);
  });
});
