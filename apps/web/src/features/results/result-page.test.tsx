import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  BenefitCalculationResponse,
  BenefitCalculationsResponse,
  ClaimCandidateResponse,
  CoverageDecisionResponse,
  KnowledgeBenefitCalculationResponse,
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

function privateCalculation(
  overrides: Partial<KnowledgeBenefitCalculationResponse> = {},
): KnowledgeBenefitCalculationResponse {
  return {
    applied_limit: null,
    applied_rate: null,
    certificate_amount_decision: "MATCH",
    certificate_amount_evidence_state: "DIRECT",
    calculation_id: "00000000-0000-4000-8000-000000000901",
    calculation_publication_id: "00000000-0000-4000-8000-000000000902",
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
    ...overrides,
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
        ? privateCalculation({
            calculation_id: `${candidateId.slice(0, -3)}901`,
            calculation_publication_id: `${candidateId.slice(0, -3)}902`,
          })
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

function catalogCoverage(
  overrides: Partial<CoverageDecisionResponse["catalog_coverage"]> = {},
): CoverageDecisionResponse["catalog_coverage"] {
  return {
    advisory_coverage_count: 0,
    benefit_coverage_count: 1,
    blocked_coverage_count: 0,
    contract_count: 1,
    not_applicable_coverage_count: 0,
    published_coverage_count: 1,
    ...overrides,
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

function privateEvaluation(
  coverageId: string,
  result: RuleEvaluationResponse["result"] = "MATCH",
): RuleEvaluationResponse {
  return {
    citations: [],
    conflicting_fields: [],
    engine_version: "private-knowledge-engine-v2",
    evaluation_id: `synthetic-private-evaluation-${coverageId.slice(-3)}`,
    fact_paths: ["MedicalEvent.classification"],
    missing_fields: [],
    reason_code: "SYNTHETIC_PRIVATE_MATCH",
    required: true,
    result,
    source: {
      kind: "PRIVATE_KNOWLEDGE_COVERAGE",
      knowledge_coverage_id: coverageId,
      rule_publication_id: `synthetic-private-rule-${coverageId.slice(-3)}`,
    },
  };
}

function renderedCandidateCard(label: string): HTMLElement {
  const cards = screen.getAllByText(label).flatMap((element) => {
    const card = element.closest("article");
    return card ? [card] : [];
  });
  expect(cards).toHaveLength(1);
  return cards[0]!;
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
          knowledge_coverage_id: PRIVATE_COVERAGE,
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
    catalog_coverage: catalogCoverage(),
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

  it("explains advisory conditional estimates and calculation-unavailable counts", () => {
    renderWithProviders(
      <ActionFirstResult
        result={result({
          conditional_fixed_subtotals: [
            {
              amount: "300000",
              calculated_candidate_count: 1,
              currency: "KRW",
              unresolved_candidate_count: 2,
            },
          ],
        })}
        onStartClaim={vi.fn()}
        onOpenEvidence={vi.fn()}
        onReanalyze={vi.fn()}
      />,
    );

    const summary = screen
      .getByRole("heading", { name: "조건부 정액 합계" })
      .closest("section");
    expect(summary).not.toBeNull();
    expect(
      within(summary!).getByText(
        /검토된 계산식 또는 증권의 정액 가입금액으로 산출한 조건부 예상액.*판정이 추가 확인으로 남은 자문 담보의 조건부 예상액도 포함될 수 있습니다/,
      ),
    ).toBeInTheDocument();
    expect(
      within(summary!).getByText("계산 불가·계산식 미완료").parentElement,
    ).toHaveTextContent(/계산 불가·계산식 미완료\s*2개/);
    expect(within(summary!).queryByText("금액 미확정")).not.toBeInTheDocument();
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

  it("separates automatic, advisory, and exceptional catalog coverage", () => {
    renderWithProviders(
      <ActionFirstResult
        result={result({
          analysis_completeness: "PARTIAL",
          candidates: [],
          catalog_coverage: catalogCoverage({
            advisory_coverage_count: 2,
            benefit_coverage_count: 4,
            blocked_coverage_count: 1,
            contract_count: 2,
            published_coverage_count: 1,
          }),
          conditional_fixed_subtotals: [],
          evaluations: [],
        })}
        onStartClaim={vi.fn()}
        onOpenEvidence={vi.fn()}
        onReanalyze={vi.fn()}
      />,
    );

    expect(
      screen.getByText(
        /가입 담보와 관련 약관을 검색할 수 있지만, 자동 판정 규칙은 아직 완전하지 않습니다/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("자동 판정 규칙 준비").parentElement,
    ).toHaveTextContent(/자동 판정 규칙 준비\s*1개/);
    expect(
      screen.getByText("가입·검색 가능 · 자동 규칙 미완료").parentElement,
    ).toHaveTextContent(/가입·검색 가능 · 자동 규칙 미완료\s*2개/);
    expect(screen.getByText("예외 확인").parentElement).toHaveTextContent(
      /예외 확인\s*1개/,
    );
    expect(screen.queryByText(/검토 대기|차단/)).not.toBeInTheDocument();
    expect(screen.queryByText(/현재 상태 확인/)).not.toBeInTheDocument();
  });

  it("names every coverage behind the automatic-rule count", () => {
    const firstCoverage = "00000000-0000-4000-8000-000000000671";
    const secondCoverage = "00000000-0000-4000-8000-000000000672";
    const first = privateCandidate(
      "00000000-0000-4000-8000-000000000371",
      firstCoverage,
      "MATCH",
      "Synthetic Reviewed Coverage A",
    );
    const second = privateCandidate(
      "00000000-0000-4000-8000-000000000372",
      secondCoverage,
      "UNKNOWN",
      "Synthetic Reviewed Coverage B",
    );

    renderWithProviders(
      <ActionFirstResult
        result={result({
          candidates: [first, second],
          catalog_coverage: catalogCoverage({
            benefit_coverage_count: 2,
            published_coverage_count: 2,
          }),
          evaluations: [
            privateEvaluation(firstCoverage),
            privateEvaluation(secondCoverage, "UNKNOWN"),
          ],
        })}
        onStartClaim={vi.fn()}
        onOpenEvidence={vi.fn()}
        onReanalyze={vi.fn()}
      />,
    );

    const reviewed = screen
      .getByRole("heading", { name: "자동 판정 규칙이 준비된 담보" })
      .closest("section");
    expect(reviewed).not.toBeNull();
    expect(
      within(reviewed!).getByText("Synthetic Reviewed Coverage A"),
    ).toBeInTheDocument();
    expect(
      within(reviewed!).getByText("Synthetic Reviewed Coverage B"),
    ).toBeInTheDocument();
  });

  it("keeps catalog-only advisory and legacy exception rows out of event cards", () => {
    const advisoryOnly = privateCandidate(
      "00000000-0000-4000-8000-000000000351",
      "00000000-0000-4000-8000-000000000651",
      "UNKNOWN",
      "Synthetic Advisory Catalog Row",
    );
    advisoryOnly.hold_reason_codes = ["COVERAGE_PUBLICATION_ADVISORY"];
    advisoryOnly.calculation = privateCalculation({
      calculation_id: "00000000-0000-4000-8000-000000000907",
      calculation_publication_id: null,
      hold_reason_code: "COVERAGE_PUBLICATION_ADVISORY",
    });
    const blockedOnly = privateCandidate(
      "00000000-0000-4000-8000-000000000352",
      "00000000-0000-4000-8000-000000000652",
      "UNKNOWN",
      "Synthetic Legacy Catalog Row",
    );
    blockedOnly.hold_reason_codes = ["COVERAGE_PUBLICATION_BLOCKED"];
    const failedCalculationOnly = privateCandidate(
      "00000000-0000-4000-8000-000000000355",
      "00000000-0000-4000-8000-000000000655",
      "UNKNOWN",
      "Synthetic Failed Catalog Calculation",
    );
    failedCalculationOnly.hold_reason_codes = ["COVERAGE_PUBLICATION_ADVISORY"];
    failedCalculationOnly.calculation = privateCalculation({
      calculation_id: "00000000-0000-4000-8000-000000000905",
      conditional_amount: null,
      status: "FAILED",
    });

    renderWithProviders(
      <ActionFirstResult
        result={result({
          candidates: [advisoryOnly, blockedOnly, failedCalculationOnly],
          catalog_coverage: catalogCoverage({
            advisory_coverage_count: 2,
            benefit_coverage_count: 3,
            blocked_coverage_count: 1,
            published_coverage_count: 0,
          }),
          conditional_fixed_subtotals: [],
          evaluations: [],
        })}
        onStartClaim={vi.fn()}
        onOpenEvidence={vi.fn()}
        onReanalyze={vi.fn()}
      />,
    );

    expect(
      screen.queryByText("Synthetic Advisory Catalog Row"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("Synthetic Legacy Catalog Row"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("Synthetic Failed Catalog Calculation"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("가입·검색 가능 · 자동 규칙 미완료").parentElement,
    ).toHaveTextContent(/2개/);
    expect(screen.getByText("예외 확인").parentElement).toHaveTextContent(
      /1개/,
    );
  });

  it("hides all-unknown private rules from cards and stale recommendations", () => {
    const recommendedCoverage = "00000000-0000-4000-8000-000000000661";
    const unrelatedCoverage = "00000000-0000-4000-8000-000000000662";
    const recommended = privateCandidate(
      "00000000-0000-4000-8000-000000000361",
      recommendedCoverage,
      "UNKNOWN",
      "Synthetic Recommended Review Coverage",
    );
    const unrelated = privateCandidate(
      "00000000-0000-4000-8000-000000000362",
      unrelatedCoverage,
      "UNKNOWN",
      "Synthetic Unrelated Housing Coverage",
    );
    recommended.hold_reason_codes = ["COVERAGE_PUBLICATION_ADVISORY"];
    unrelated.hold_reason_codes = ["COVERAGE_PUBLICATION_ADVISORY"];
    const recommendedEvaluation = privateEvaluation(
      recommendedCoverage,
      "UNKNOWN",
    );
    recommendedEvaluation.reason_code = "ALL_UNKNOWN";
    const unrelatedEvaluation = privateEvaluation(unrelatedCoverage, "UNKNOWN");
    unrelatedEvaluation.reason_code = "ALL_UNKNOWN";

    renderWithProviders(
      <ActionFirstResult
        result={result({
          assistance: {
            ...result().assistance,
            recommendations: [
              {
                ...result().assistance.recommendations[0],
                coverage_label: "Synthetic Recommended Review Coverage",
                knowledge_coverage_id: recommendedCoverage,
              },
            ],
          },
          candidates: [recommended, unrelated],
          evaluations: [recommendedEvaluation, unrelatedEvaluation],
        })}
        onStartClaim={vi.fn()}
        onOpenEvidence={vi.fn()}
        onReanalyze={vi.fn()}
      />,
    );

    const needsInformation = screen
      .getByRole("heading", { name: "추가 확인 필요" })
      .closest("section");
    const recommendations = screen
      .getByRole("heading", { name: "관련 약관 추천" })
      .closest("section");
    const reviewed = screen
      .getByRole("heading", { name: "자동 판정 규칙이 준비된 담보" })
      .closest("section");
    expect(needsInformation).not.toBeNull();
    expect(recommendations).not.toBeNull();
    expect(reviewed).not.toBeNull();
    expect(
      within(needsInformation!).queryByText(
        "Synthetic Recommended Review Coverage",
      ),
    ).not.toBeInTheDocument();
    expect(
      within(needsInformation!).queryByText(
        "Synthetic Unrelated Housing Coverage",
      ),
    ).not.toBeInTheDocument();
    expect(
      within(recommendations!).queryByText(
        "Synthetic Recommended Review Coverage",
      ),
    ).not.toBeInTheDocument();
    expect(
      within(reviewed!).getByText("Synthetic Recommended Review Coverage"),
    ).toBeInTheDocument();
    expect(
      within(reviewed!).getByText("Synthetic Unrelated Housing Coverage"),
    ).toBeInTheDocument();
  });

  it("drops an all-unknown stale recommendation when a precondition makes the card no-match", () => {
    const coverageId = "00000000-0000-4000-8000-000000000664";
    const candidate = privateCandidate(
      "00000000-0000-4000-8000-000000000364",
      coverageId,
      "NO_MATCH",
      "Synthetic Precondition Mismatch Coverage",
    );
    const evaluation = privateEvaluation(coverageId, "UNKNOWN");
    evaluation.reason_code = "ALL_UNKNOWN";

    renderWithProviders(
      <ActionFirstResult
        result={result({
          assistance: {
            ...result().assistance,
            recommendations: [
              {
                ...result().assistance.recommendations[0],
                coverage_label: "Synthetic Precondition Mismatch Coverage",
                knowledge_coverage_id: coverageId,
              },
            ],
          },
          candidates: [candidate],
          evaluations: [evaluation],
        })}
        onStartClaim={vi.fn()}
        onOpenEvidence={vi.fn()}
        onReanalyze={vi.fn()}
      />,
    );

    const mismatch = screen
      .getByRole("heading", { name: "조건 불일치" })
      .closest("section");
    const recommendations = screen
      .getByRole("heading", { name: "관련 약관 추천" })
      .closest("section");
    expect(mismatch).not.toBeNull();
    expect(recommendations).not.toBeNull();
    expect(
      within(mismatch!).getByText("Synthetic Precondition Mismatch Coverage"),
    ).toBeInTheDocument();
    expect(
      within(recommendations!).queryByText(
        "Synthetic Precondition Mismatch Coverage",
      ),
    ).not.toBeInTheDocument();
  });

  it("keeps a related recommendation when the catalog coverage has no rule evaluation", () => {
    const coverageId = "00000000-0000-4000-8000-000000000663";
    const catalogOnly = privateCandidate(
      "00000000-0000-4000-8000-000000000363",
      coverageId,
      "UNKNOWN",
      "Synthetic Unevaluated Related Coverage",
    );
    catalogOnly.hold_reason_codes = ["COVERAGE_PUBLICATION_ADVISORY"];

    renderWithProviders(
      <ActionFirstResult
        result={result({
          assistance: {
            ...result().assistance,
            recommendations: [
              {
                ...result().assistance.recommendations[0],
                coverage_label: "Synthetic Unevaluated Related Coverage",
                knowledge_coverage_id: coverageId,
              },
            ],
          },
          candidates: [catalogOnly],
          evaluations: [],
        })}
        onStartClaim={vi.fn()}
        onOpenEvidence={vi.fn()}
        onReanalyze={vi.fn()}
      />,
    );

    expect(
      screen.getAllByText("Synthetic Unevaluated Related Coverage"),
    ).toHaveLength(1);
    expect(
      screen
        .getByText("Synthetic Unevaluated Related Coverage")
        .closest("article"),
    ).toHaveClass(/recommendationCard/);
  });

  it("distinguishes held policy estimates from confirmed calculations", () => {
    const conditional = privateCandidate(
      "00000000-0000-4000-8000-000000000353",
      "00000000-0000-4000-8000-000000000653",
      "UNKNOWN",
      "Synthetic Conditional Coverage",
    );
    conditional.hold_reason_codes = ["COVERAGE_PUBLICATION_ADVISORY"];
    conditional.calculation = privateCalculation({
      calculation_id: "00000000-0000-4000-8000-000000000903",
      confirmed_amount: null,
      hold_reason_code: "HUMAN_REVIEW_REQUIRED",
    });
    const confirmed = privateCandidate(
      "00000000-0000-4000-8000-000000000354",
      "00000000-0000-4000-8000-000000000654",
      "MATCH",
      "Synthetic Confirmed Coverage",
    );
    confirmed.calculation = privateCalculation({
      calculation_id: "00000000-0000-4000-8000-000000000904",
      confirmed_amount: "300000",
    });
    const unresolved = privateCandidate(
      "00000000-0000-4000-8000-000000000356",
      "00000000-0000-4000-8000-000000000656",
      "UNKNOWN",
      "Synthetic Unresolved Coverage",
    );
    unresolved.calculation = privateCalculation({
      calculation_id: "00000000-0000-4000-8000-000000000906",
      conditional_amount: null,
      confirmed_amount: "300000",
      status: "FAILED",
    });

    renderWithProviders(
      <ActionFirstResult
        result={result({
          candidates: [conditional, confirmed, unresolved],
          evaluations: [
            privateEvaluation("00000000-0000-4000-8000-000000000653"),
            privateEvaluation("00000000-0000-4000-8000-000000000654"),
            privateEvaluation(
              "00000000-0000-4000-8000-000000000656",
              "UNKNOWN",
            ),
          ],
        })}
        onStartClaim={vi.fn()}
        onOpenEvidence={vi.fn()}
        onReanalyze={vi.fn()}
      />,
    );

    const conditionalCard = renderedCandidateCard(
      "Synthetic Conditional Coverage",
    );
    const confirmedCard = renderedCandidateCard("Synthetic Confirmed Coverage");
    const unresolvedCard = renderedCandidateCard(
      "Synthetic Unresolved Coverage",
    );
    expect(
      within(conditionalCard!).getByText("조건부 약관 예상액"),
    ).toBeInTheDocument();
    expect(
      within(conditionalCard!).getByText("조건부 예상액: 300000 KRW"),
    ).toBeInTheDocument();
    expect(
      within(conditionalCard!).queryByText("확인된 계산 결과"),
    ).not.toBeInTheDocument();
    expect(
      within(confirmedCard!).getByText("확인된 계산 결과"),
    ).toBeInTheDocument();
    expect(
      within(confirmedCard!).queryByText("조건부 약관 예상액"),
    ).not.toBeInTheDocument();
    expect(
      within(unresolvedCard!).getByText("계산 다시 확인 필요"),
    ).toBeInTheDocument();
    expect(
      within(unresolvedCard!).queryByText(/확인된 계산 금액/),
    ).not.toBeInTheDocument();
  });

  it("labels a certificate insured amount as an estimate, not a confirmed payment", () => {
    const coverageId = "00000000-0000-4000-8000-000000000657";
    const certificateEstimate = privateCandidate(
      "00000000-0000-4000-8000-000000000357",
      coverageId,
      "MATCH",
      "Synthetic Certificate Amount Coverage",
    );
    certificateEstimate.calculation = privateCalculation({
      calculation_id: "00000000-0000-4000-8000-000000000907",
      calculation_publication_id: null,
      confirmed_amount: null,
      certificate_evidence: [
        {
          document_alias: "Sample Certificate",
          evidence_pages: [4],
        },
      ],
      steps: [
        {
          currency: "KRW",
          input_amount: "300000",
          operation: "certificate_insured_amount",
          output_amount: "300000",
          reason_code: "CERTIFICATE_INSURED_AMOUNT_ESTIMATE",
          rounding_rule: null,
          step_number: 1,
        },
      ],
    });

    renderWithProviders(
      <ActionFirstResult
        result={result({
          candidates: [certificateEstimate],
          evaluations: [privateEvaluation(coverageId)],
        })}
        onStartClaim={vi.fn()}
        onOpenEvidence={vi.fn()}
        onReanalyze={vi.fn()}
      />,
    );

    const card = renderedCandidateCard("Synthetic Certificate Amount Coverage");
    expect(within(card!).getByText("증권 기준 예상액")).toBeInTheDocument();
    expect(
      within(card!).getByText("예상 금액: 300000 KRW"),
    ).toBeInTheDocument();
    expect(within(card!).getByText(/증권 가입 금액 적용/)).toBeInTheDocument();
    expect(
      within(card!).getByText("증권 가입금액 직접 근거"),
    ).toBeInTheDocument();
    expect(within(card!).getByText("Sample Certificate")).toBeInTheDocument();
    expect(within(card!).getByText("4쪽")).toBeInTheDocument();
    expect(
      within(card!).queryByText("확인된 계산 결과"),
    ).not.toBeInTheDocument();
  });

  it("shows an estimate while separating certificate pages that need amount review", () => {
    const coverageId = "00000000-0000-4000-8000-000000000658";
    const certificateEstimate = privateCandidate(
      "00000000-0000-4000-8000-000000000358",
      coverageId,
      "MATCH",
      "Synthetic Certificate Review Coverage",
    );
    certificateEstimate.calculation = privateCalculation({
      calculation_id: "00000000-0000-4000-8000-000000000908",
      calculation_publication_id: null,
      certificate_amount_decision: "UNKNOWN",
      certificate_amount_evidence_state: "REVIEW_REQUIRED",
      certificate_evidence: [
        {
          document_alias: "Sample Certificate",
          evidence_pages: [6],
        },
      ],
      confirmed_amount: null,
      hold_reason_code: "CERTIFICATE_AMOUNT_EVIDENCE_REVIEW_REQUIRED",
    });

    renderWithProviders(
      <ActionFirstResult
        result={result({
          candidates: [certificateEstimate],
          evaluations: [privateEvaluation(coverageId)],
        })}
        onStartClaim={vi.fn()}
        onOpenEvidence={vi.fn()}
        onReanalyze={vi.fn()}
      />,
    );

    const card = renderedCandidateCard("Synthetic Certificate Review Coverage");
    expect(
      within(card!).getByText("예상 금액: 300000 KRW"),
    ).toBeInTheDocument();
    expect(
      within(card!).getByText("증권 담보 근거 · 가입금액 위치 확인 필요"),
    ).toBeInTheDocument();
    expect(
      within(card!).getByText(
        "가입금액이 적힌 위치를 직접 확인하기 전의 참고 예상액입니다.",
      ),
    ).toBeInTheDocument();
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
      engine_version: "private-knowledge-engine-v2",
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

  it("always identifies contract-level terms recommendations as indirect", () => {
    renderWithProviders(
      <ActionFirstResult
        result={result({
          assistance: {
            ...result().assistance,
            mode: "LLM_ASSISTED",
            model_label: "synthetic-model-v1",
            recommendations: [
              {
                ...result().assistance.recommendations[0],
                explanation_code: "RELATED_CLAUSE",
                reason_code: "CONTRACT_TERMS_TOKEN_OVERLAP",
              },
            ],
            state: "LLM_READY",
          },
        })}
        onStartClaim={vi.fn()}
        onOpenEvidence={vi.fn()}
        onReanalyze={vi.fn()}
      />,
    );

    expect(
      screen.getByText(
        "같은 계약의 약관에서 찾은 후보이며, 이 담보에 직접 적용된다는 뜻은 아닙니다.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("입력 내용과 관련 가능성이 있는 약관 조항입니다."),
    ).toBeInTheDocument();
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
