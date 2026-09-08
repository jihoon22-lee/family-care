import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  ClaimCaseResponse,
  ClaimChecklistItemResponse,
  ClaimSnapshotResponse,
  ClaimLocalGuidanceSnapshot,
} from "../../api/generated";
import {
  createClaimCase,
  listClaimCases,
  transitionClaimCase,
  updateClaimChecklist,
} from "../../api/claims";
import { renderWithProviders } from "../../test/renderWithProviders";
import { ClaimOutcomeForm } from "./ClaimOutcomeForm";
import { ClaimCasePage } from "./ClaimCasePage";
import { ChecklistEditor } from "./ChecklistEditor";
import { ClaimListPage } from "./ClaimListPage";
import { ClaimStatusStepper } from "./ClaimStatusStepper";

const EVENT_ID = "00000000-0000-4000-8000-000000000201";
const CLAIM_ID = "00000000-0000-4000-8000-000000000301";
const POLICY_ID = "00000000-0000-4000-8000-000000000401";
const RIDER_ID = "00000000-0000-4000-8000-000000000701";
const ITEM_ID = "00000000-0000-4000-8000-000000000501";

const SNAPSHOT: ClaimSnapshotResponse = {
  calculation: { calculation_ids: [], statuses: [], versions: [] },
  candidate: {
    aggregate_results: ["MATCH"],
    candidate_ids: ["00000000-0000-4000-8000-000000000601"],
    rider_ids: ["00000000-0000-4000-8000-000000000701"],
  },
  evidence: { content_sha256: ["a".repeat(64)], evidence_ids: [] },
  policy: {
    captured_at: "2026-08-25T09:00:00Z",
    policy_contract_id: POLICY_ID,
    rider_ids: [],
    status_codes: ["active"],
  },
  rules: {
    evaluator_versions: ["synthetic-decision-engine-v1"],
    reason_codes: ["SYNTHETIC_MATCH"],
    rule_version_ids: [],
  },
  snapshot_sha256: "b".repeat(64),
  snapshot_version: 1,
};

const CHECKLIST_ITEM: ClaimChecklistItemResponse = {
  conditional: false,
  document_kind: "claim_form",
  id: ITEM_ID,
  note_code: null,
  prepared: false,
  required: true,
  requirement_code: "CLAIM_FORM_REQUIRED",
  source_evidence_id: null,
  source_rule_version_id: null,
  version: 1,
};

const CLAIM: ClaimCaseResponse = {
  allowed_transitions: ["submitted", "denied", "paid", "partially_paid"],
  checklist: [CHECKLIST_ITEM],
  claimed_amount: null,
  currency: null,
  deleted: false,
  family_member_id: "00000000-0000-4000-8000-000000000101",
  id: CLAIM_ID,
  insurer_key: "synthetic-insurer",
  medical_event_id: EVENT_ID,
  outcome_reason_code: null,
  paid_amount: null,
  policy_contract_id: POLICY_ID,
  rider_id: RIDER_ID,
  receipt_number: null,
  schema_version: "1",
  snapshot: SNAPSHOT,
  status: "preparing",
  status_events: [],
  submitted_at: null,
  version: 1,
};

function savedGuidance(): ClaimLocalGuidanceSnapshot {
  const emptyCosts = {
    line_ids: [],
    known_line_ids: [],
    unknown_amount_line_ids: [],
    known_cost: null,
    total_cost: null,
    source_refs: [],
  };
  return {
    event_date: "2026-08-25",
    event_version: 2,
    medical_event_id: EVENT_ID,
    family_member_id: CLAIM.family_member_id,
    run_id: "synthetic-saved-run",
    versions: {},
    expenses: {
      event_id: EVENT_ID,
      event_version: 2,
      status: "AVAILABLE",
      reader_revision: "synthetic-expenses-v1",
      digest_sha256: "d".repeat(64),
      unassigned_line_ids: [],
      reason_codes: [],
      currencies: [
        {
          currency: "KRW",
          covered: { ...emptyCosts, known_cost: "50000", total_cost: "50000" },
          excluded: emptyCosts,
          coverage_review: emptyCosts,
          unconfirmed: emptyCosts,
        },
      ],
    },
    candidate: {
      ref: {
        kind: "OPERATIONAL_RIDER",
        contract_id: POLICY_ID,
        coverage_id: RIDER_ID,
      },
      contract_label: "Sample Saved Policy",
      coverage_label: "Sample Saved Coverage",
      benefit_kind: "FIXED",
      group: "CONDITIONAL",
      condition_result: "UNKNOWN",
      freshness: "DOCUMENT_CONTINUITY",
      reason_codes: ["DOCUMENTED_RELEVANT_COVERAGE"],
      assumptions: ["DOCUMENT_CONTINUITY_ASSUMED"],
      contract_amount: {
        amount: "100",
        currency: "KRW",
        amount_authority: "PROGRAM_VERIFIED",
        currency_authority: "PROGRAM_VERIFIED",
      },
      estimate: {
        kind: "POINT",
        amount: "300",
        currency: "KRW",
        formula: "가입금액 × 지급일수",
        reason_code: "DOCUMENT_BASED_ESTIMATE",
        trace: {
          publication_id: "synthetic-saved-publication",
          source_revision: "synthetic-v1",
          source_digest_sha256: "a".repeat(64),
          formula_digest_sha256: "b".repeat(64),
          runtime_revision: "synthetic-runtime",
          status: "COMPLETE",
          value: "300",
          unit: "MONEY",
          currency: "KRW",
          steps: [
            {
              step_number: 1,
              expression_path: "/calculation",
              operation: "multiply",
              operands: [
                {
                  expression_path: "/calculation/args/0",
                  kind: "LITERAL",
                  value: "100",
                  unit: "MONEY",
                  currency: "KRW",
                  status: "AVAILABLE",
                },
                {
                  expression_path: "/calculation/args/1",
                  kind: "LITERAL",
                  value: "3",
                  unit: "DAYS",
                  status: "AVAILABLE",
                },
              ],
              value: "300",
              unit: "MONEY",
              currency: "KRW",
              status: "AVAILABLE",
            },
          ],
        },
      },
    },
  };
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    headers: {
      "cache-control": "no-store",
      "content-type": "application/json",
    },
    status,
  });
}

function errorResponse(errorCode: string, status: number): Response {
  return jsonResponse(
    { error_code: errorCode, message: "Synthetic error" },
    status,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("claim API client", () => {
  it("uses strict no-store endpoints and never sends document fields", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ items: [CLAIM], next_cursor: null }),
      )
      .mockResolvedValueOnce(jsonResponse(CLAIM, 201))
      .mockResolvedValueOnce(
        jsonResponse({ ...CLAIM, status: "submitted", version: 2 }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          ...CLAIM,
          checklist: [{ ...CHECKLIST_ITEM, prepared: true, version: 2 }],
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await listClaimCases({ eventId: EVENT_ID, status: "preparing" });
    await createClaimCase(EVENT_ID, {
      rider_id: RIDER_ID,
    });
    await transitionClaimCase(CLAIM_ID, {
      expected_version: 1,
      occurred_at: "2026-08-26T09:00:00Z",
      target_status: "submitted",
    });
    await updateClaimChecklist(CLAIM_ID, ITEM_ID, {
      expected_version: 1,
      prepared: true,
      note_code: null,
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `/api/v1/claims?event_id=${EVENT_ID}&status=preparing`,
    );
    expect(fetchMock.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({ cache: "no-store", credentials: "include" }),
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      `/api/v1/medical-events/${EVENT_ID}/claims`,
    );
    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      `/api/v1/claims/${CLAIM_ID}/transitions`,
    );
    expect(fetchMock.mock.calls[3]?.[0]).toBe(
      `/api/v1/claims/${CLAIM_ID}/checklist/${ITEM_ID}`,
    );
    const requestBodies = fetchMock.mock.calls
      .map((call) => call[1]?.body)
      .filter((body): body is string => typeof body === "string")
      .join(" ");
    expect(requestBodies).not.toMatch(
      /file|path|ocr|document_text|medical_text/i,
    );
  });
});

describe("claim workflow controls", () => {
  it("opens a private claim label without operational contract identifiers", async () => {
    const privateClaim = {
      ...CLAIM,
      policy_contract_id: null,
      rider_id: null,
      insurer_key: null,
      insurer_display: "Sample Private Insurer",
      snapshot: { ...SNAPSHOT, local_guidance: savedGuidance() },
    };
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse({ items: [privateClaim], next_cursor: null }),
        ),
    );
    renderWithProviders(<ClaimListPage />);
    expect(
      await screen.findByRole("link", {
        name: /Sample Private Insurer.*Sample Saved Coverage/,
      }),
    ).toHaveAttribute("href", `/app/claims/${CLAIM_ID}`);
  });

  it("renders saved guidance and arithmetic separately from the actual payment without reanalysis", async () => {
    const saved = savedGuidance();
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        ...CLAIM,
        paid_amount: "250",
        currency: "KRW",
        snapshot: { ...SNAPSHOT, local_guidance: saved },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ClaimCasePage claimId={CLAIM_ID} />);
    const region = await screen.findByRole("region", {
      name: "청구 준비 때 저장한 안내",
    });
    expect(
      within(region).getByText("Sample Saved Coverage"),
    ).toBeInTheDocument();
    expect(within(region).getByText("300원")).toBeInTheDocument();
    expect(within(region).getByText("100원")).toBeInTheDocument();
    expect(
      within(region).getByRole("region", { name: "등록된 비용" }),
    ).toHaveTextContent("50,000원");
    expect(screen.getByText("250원")).toBeInTheDocument();
    expect(screen.getByText("실제 지급액")).toBeInTheDocument();
    fireEvent.click(within(region).getByText("계산 과정과 근거"));
    expect(within(region).getByText(/100원 × 3일 = 300원/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(`/api/v1/claims/${CLAIM_ID}`);
  });

  it("keeps saved payout cases and planned estimates with their own meanings", async () => {
    const saved = savedGuidance();
    const estimate = saved.candidate.estimate;
    saved.candidate.estimate = {
      kind: "UNAVAILABLE",
      reason_code: "MULTIPLE_PAYOUT_CASES",
    };
    saved.candidate.cases = [
      {
        case_key: "synthetic-case-a",
        benefit_kind: "FIXED",
        condition_result: "UNKNOWN",
        reason_codes: [],
        estimate,
      },
      {
        case_key: "synthetic-case-b",
        benefit_kind: "FIXED",
        condition_result: "UNKNOWN",
        reason_codes: [],
        estimate: {
          kind: "FORMULA",
          formula: "가입금액 × 예정 입원 일수",
          reason_code: "MISSING_DAYS",
        },
        scenarios: [
          {
            scenario_key: "c".repeat(64),
            kind: "PLANNED_CARE",
            hypotheses: [],
            estimate: {
              ...estimate,
              amount: "400",
              basis: "USER_SCENARIO",
              assumptions: ["PLANNED_CARE_ASSUMED"],
              trace: null,
            },
          },
        ],
      },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          ...CLAIM,
          snapshot: { ...SNAPSHOT, local_guidance: saved },
        }),
      ),
    );
    renderWithProviders(<ClaimCasePage claimId={CLAIM_ID} />);
    const region = await screen.findByRole("region", {
      name: "청구 준비 때 저장한 안내",
    });
    expect(within(region).getByText("300원")).toBeInTheDocument();
    expect(within(region).getByText("400원")).toBeInTheDocument();
    expect(within(region).getByText(/예정된 치료를 가정/)).toBeInTheDocument();
    expect(within(region).queryByText("700원")).not.toBeInTheDocument();
  });

  it("removes saved guidance from view when the claim session expires", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          ...CLAIM,
          snapshot: { ...SNAPSHOT, local_guidance: savedGuidance() },
        }),
      )
      .mockResolvedValueOnce(errorResponse("AUTHENTICATION_REQUIRED", 401));
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ClaimCasePage claimId={CLAIM_ID} />);
    await screen.findByRole("region", { name: "청구 준비 때 저장한 안내" });
    fireEvent.click(screen.getByRole("button", { name: "기록 저장" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("로그인");
    expect(
      screen.queryByRole("region", { name: "청구 준비 때 저장한 안내" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Sample Saved Coverage")).not.toBeInTheDocument();
  });

  it("renders only server-allowed transition actions", () => {
    const onTransition = vi.fn();
    renderWithProviders(
      <ClaimStatusStepper
        allowedTransitions={["submitted"]}
        onTransition={onTransition}
        status="preparing"
      />,
    );

    expect(
      screen.getByRole("button", { name: "제출 기록" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "지급 완료 기록" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "제출 기록" }));
    expect(onTransition).toHaveBeenCalledWith("submitted");
  });

  it("updates checklist metadata without exposing a file input", () => {
    const onUpdate = vi.fn();
    renderWithProviders(
      <ChecklistEditor items={[CHECKLIST_ITEM]} onUpdate={onUpdate} />,
    );

    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/파일|문서 업로드/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /청구서/ }));
    expect(onUpdate).toHaveBeenCalledWith(CHECKLIST_ITEM);
  });

  it("validates decimal payment input before invoking a transition", () => {
    const onSubmit = vi.fn();
    renderWithProviders(<ClaimOutcomeForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByRole("spinbutton", { name: "지급액" }), {
      target: { value: "-1" },
    });
    fireEvent.change(screen.getByLabelText("지급일"), {
      target: { value: "2026-08-26" },
    });
    fireEvent.click(screen.getByRole("button", { name: "부분 지급 기록" }));
    expect(screen.getByRole("alert")).toHaveTextContent("0 이상");
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.change(screen.getByRole("spinbutton", { name: "지급액" }), {
      target: { value: "120000.00" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "통화" }), {
      target: { value: "KRW" },
    });
    fireEvent.click(screen.getByRole("button", { name: "부분 지급 기록" }));
    expect(onSubmit).toHaveBeenCalledWith(
      "partially_paid",
      expect.objectContaining({
        amount: "120000.00",
        currency: "KRW",
        payment_date: "2026-08-26",
      }),
    );
  });

  it("preserves manual drafts on a version conflict and distinguishes transition errors", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(CLAIM))
      .mockResolvedValueOnce(errorResponse("INVALID_CLAIM_TRANSITION", 409))
      .mockResolvedValueOnce(errorResponse("VERSION_CONFLICT", 409));
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<ClaimCasePage claimId={CLAIM_ID} />);
    expect(
      await screen.findByRole("heading", { name: "synthetic-insurer" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "제출 기록" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "현재 상태에서는",
    );

    const receipt = screen.getByLabelText("보험사 접수 번호");
    fireEvent.change(receipt, { target: { value: "synthetic-receipt-001" } });
    fireEvent.click(screen.getByRole("button", { name: "기록 저장" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("다른 화면에서");
    expect(receipt).toHaveValue("synthetic-receipt-001");
  });

  it("keeps payment drafts visible when an outcome transition conflicts", async () => {
    const submittedClaim: ClaimCaseResponse = {
      ...CLAIM,
      allowed_transitions: ["paid", "partially_paid", "denied"],
      status: "submitted",
      version: 2,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(submittedClaim))
      .mockResolvedValueOnce(errorResponse("VERSION_CONFLICT", 409))
      .mockResolvedValueOnce(jsonResponse({ ...submittedClaim, version: 3 }))
      .mockResolvedValueOnce(
        jsonResponse({
          ...submittedClaim,
          allowed_transitions: ["closed"],
          paid_amount: "120000.00",
          status: "paid",
          version: 4,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<ClaimCasePage claimId={CLAIM_ID} />);
    expect(
      await screen.findByRole("heading", { name: "synthetic-insurer" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "지급 완료" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "지급액" }), {
      target: { value: "120000.00" },
    });
    fireEvent.change(screen.getByLabelText("지급일"), {
      target: { value: "2026-08-26" },
    });
    fireEvent.click(screen.getByRole("button", { name: "전액 지급 기록" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("다른 화면에서");
    expect(screen.getByRole("spinbutton", { name: "지급액" })).toHaveValue(
      120000,
    );
    expect(screen.getByLabelText("지급일")).toHaveValue("2026-08-26");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    fireEvent.click(screen.getByRole("button", { name: "전액 지급 기록" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    const retryBody = JSON.parse(
      String((fetchMock.mock.calls[3]?.[1] as RequestInit | undefined)?.body),
    ) as { expected_version: number; metadata: { amount: string } };
    expect(retryBody.expected_version).toBe(3);
    expect(retryBody.metadata.amount).toBe("120000.00");
  });

  it("clears sensitive metadata drafts after a successful update", async () => {
    const updatedClaim: ClaimCaseResponse = {
      ...CLAIM,
      claimed_amount: "1000.00",
      currency: "KRW",
      outcome_reason_code: "SYNTHETIC_REASON",
      receipt_number: "synthetic-receipt-003",
      version: 2,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(CLAIM))
      .mockResolvedValueOnce(jsonResponse(updatedClaim));
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<ClaimCasePage claimId={CLAIM_ID} />);
    expect(
      await screen.findByRole("heading", { name: "synthetic-insurer" }),
    ).toBeInTheDocument();
    const receipt = screen.getByLabelText("보험사 접수 번호");
    const amount = screen.getByLabelText("청구 금액");
    const currency = screen.getByLabelText("통화");
    const reason = screen.getByLabelText("결과 사유 코드");
    fireEvent.change(receipt, { target: { value: "synthetic-receipt-003" } });
    fireEvent.change(amount, { target: { value: "1000.00" } });
    fireEvent.change(currency, { target: { value: "KRW" } });
    fireEvent.change(reason, { target: { value: "SYNTHETIC_REASON" } });
    fireEvent.click(screen.getByRole("button", { name: "기록 저장" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await waitFor(() => {
      expect(receipt).toHaveValue("");
      expect(amount).toHaveValue(null);
      expect(currency).toHaveValue("");
      expect(reason).toHaveValue("");
    });
  });

  it("clears sensitive manual drafts after authentication expiry", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(CLAIM))
      .mockResolvedValueOnce(errorResponse("AUTHENTICATION_REQUIRED", 401));
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<ClaimCasePage claimId={CLAIM_ID} />);
    expect(
      await screen.findByRole("heading", { name: "synthetic-insurer" }),
    ).toBeInTheDocument();
    const receipt = screen.getByLabelText("보험사 접수 번호");
    const amount = screen.getByLabelText("청구 금액");
    const currency = screen.getByLabelText("통화");
    const reason = screen.getByLabelText("결과 사유 코드");
    fireEvent.change(receipt, { target: { value: "synthetic-receipt-002" } });
    fireEvent.change(amount, { target: { value: "1000.00" } });
    fireEvent.change(currency, { target: { value: "KRW" } });
    fireEvent.change(reason, { target: { value: "SYNTHETIC_REASON" } });
    fireEvent.click(screen.getByRole("button", { name: "기록 저장" }));
    await screen.findByRole("alert");
    expect(screen.queryByLabelText("보험사 접수 번호")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("청구 금액")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("통화")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("결과 사유 코드")).not.toBeInTheDocument();
  });

  it("restores an archived claim from the trash list", async () => {
    const deletedClaim = { ...CLAIM, deleted: true };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ items: [deletedClaim], next_cursor: null }),
      )
      .mockResolvedValueOnce(jsonResponse(CLAIM))
      .mockResolvedValueOnce(jsonResponse({ items: [], next_cursor: null }));
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<ClaimListPage deletedOnly />);
    expect(await screen.findByText(/synthetic-insurer/)).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /synthetic-insurer/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "복원" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls[1]?.[0]).toBe(
        `/api/v1/claims/${CLAIM_ID}/restore`,
      );
    });
  });
});
