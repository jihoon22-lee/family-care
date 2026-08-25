import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  BenefitCalculationResponse,
  BenefitCalculationsResponse,
  ClaimCandidateResponse,
  CoverageDecisionResponse,
  MedicalEventResponse,
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
    candidate_id: candidateId,
    hold_reason_codes: holdReasonCodes,
    questions: [],
    required_match_count: aggregateResult === "MATCH" ? 1 : 0,
    required_no_match_count: aggregateResult === "NO_MATCH" ? 1 : 0,
    required_unknown_count: aggregateResult === "UNKNOWN" ? 1 : 0,
    rider_id: riderId,
    rider_label:
      riderId === RIDER_A
        ? "Sample Rider A"
        : riderId === RIDER_B
          ? "Sample Rider B"
          : "Sample Rider C",
    rider_type: "fixed",
  };
}

function evaluation(
  riderId: string,
  result: RuleEvaluationResponse["result"],
  reasonCode: string,
  overrides: Partial<RuleEvaluationResponse> = {},
): RuleEvaluationResponse {
  return {
    conflicting_fields: [],
    engine_version: "synthetic-decision-engine-v1",
    evaluation_id: `synthetic-evaluation-${riderId.slice(-3)}`,
    evidence: [],
    fact_paths: [],
    missing_fields: [],
    reason_code: reasonCode,
    required: true,
    result,
    rider_id: riderId,
    rule_version_id: `synthetic-rule-${riderId.slice(-3)}`,
    ...overrides,
  };
}

function result(
  overrides: Partial<CoverageDecisionResponse> = {},
): CoverageDecisionResponse {
  return {
    candidates: [
      candidate(CANDIDATE_A, RIDER_A, "MATCH"),
      candidate(CANDIDATE_B, RIDER_B, "UNKNOWN", ["RULE_READER_UNAVAILABLE"]),
      candidate(CANDIDATE_C, RIDER_C, "NO_MATCH"),
    ],
    engine_version: "synthetic-decision-engine-v1",
    evaluations: [
      evaluation(RIDER_A, "MATCH", "SYNTHETIC_MATCH", {
        evidence: [
          {
            bbox: null,
            content_sha256: "a".repeat(64),
            document_version_id: "synthetic-document-version-001",
            evidence_id: EVIDENCE_ID,
            extraction_id: "synthetic-extraction-001",
            physical_page: 4,
            review_state: "USER_CONFIRMED",
          },
        ],
      }),
      evaluation(RIDER_B, "UNKNOWN", "RULE_RUNTIME_INVALID"),
      evaluation(RIDER_C, "NO_MATCH", "SYNTHETIC_NO_MATCH"),
    ],
    event_version: 2,
    medical_event_id: EVENT_ID,
    policy_snapshot_at: "2026-08-25T09:00:00Z",
    rule_set_version: "synthetic-rule-set-v1",
    run_id: "00000000-0000-4000-8000-000000000601",
    schema_version: "1",
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
      "지금 할 일",
      "청구 검토 대상",
      "추가 확인 필요",
      "조건 불일치",
    ]);
    expect(screen.queryByText(/지급 확정|지급 가능/)).not.toBeInTheDocument();
    expect(screen.getAllByText("Sample Rider A")).not.toHaveLength(0);
    expect(screen.queryByText(RIDER_A)).not.toBeInTheDocument();
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
    installFetch(
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
    expect(screen.queryByText(/합계|더하기|총액/)).not.toBeInTheDocument();
  });
});
