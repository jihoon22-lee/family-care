import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GuidanceCandidate, GuidanceReviewJob } from "../../api/generated";
import { ApiError } from "../../api/errors";
import {
  cancelGuidanceReview,
  createGuidanceReview,
  getGuidanceReview,
} from "../../api/guidance-reviews";
import { authStore } from "../identity/authStore";
import { GuidanceReviewPanel } from "./GuidanceReviewPanel";

vi.mock("../../api/guidance-reviews", () => ({
  createGuidanceReview: vi.fn(),
  getGuidanceReview: vi.fn(),
  cancelGuidanceReview: vi.fn(),
}));
const props = {
  eventId: "synthetic-event-001",
  eventVersion: 1,
  decisionRunId: "synthetic-run-001",
};
function job(state: GuidanceReviewJob["state"] = "queued"): GuidanceReviewJob {
  return {
    id: "synthetic-review-001",
    created_at: new Date().toISOString(),
    decision_run_id: props.decisionRunId,
    medical_event_id: props.eventId,
    event_version: 1,
    state,
    http_attempts: 0,
  };
}
function reviewedJob(): GuidanceReviewJob {
  const candidate: GuidanceCandidate = {
    ref: {
      kind: "PRIVATE_KNOWLEDGE_COVERAGE",
      contract_id: "synthetic-policy-001",
      coverage_id: "synthetic-coverage-001",
    },
    contract_label: "Sample Policy",
    coverage_label: "Sample Coverage",
    benefit_kind: "FIXED",
    group: "PRIMARY",
    condition_result: "MATCH",
    freshness: "DOCUMENT_CONTINUITY",
    reason_codes: [],
    estimate: {
      kind: "POINT",
      amount: "120000",
      currency: "KRW",
      reason_code: "DOCUMENT_BASED_ESTIMATE",
    },
  };
  return {
    ...job("partial"),
    result: {
      source_digest: "synthetic-source-digest",
      reason_codes: ["REVIEW_PARTIAL_INTERPRETATION"],
      scope: {
        complete: false,
        total_coverages: 2,
        indexed_coverages: 1,
        total_packets: 3,
        reviewed_packets: 1,
        unreviewed_packets: 1,
        omitted_packets: 1,
        expected_regions: 12,
        supplied_regions: 8,
        unsupplied_regions: 4,
        coverages: [
          {
            ref: candidate.ref,
            contract_label: "Sample Policy",
            coverage_label: "Sample Coverage",
            source_state: "PARTIAL",
            reviewed_packets: 1,
            total_packets: 3,
          },
        ],
      },
      findings: [
        {
          coverage: candidate.ref,
          kind: "ADDITIONAL_CANDIDATE",
          status: "OPINION",
          reason_codes: ["SYNTHETIC_REASON"],
          evidence: [
            {
              kind: "REVIEW_SOURCE_CITATION",
              packet_id: "synthetic-packet",
              citation_id: "synthetic-citation",
              document_version_id: "synthetic-document",
              terms_edition_id: "synthetic-terms",
              source_node_id: "synthetic-node",
              page_start: 7,
              page_end: 8,
              start: 0,
              end: 10,
              source_layer: "native",
              bbox: [0, 0, 1, 1],
              source_sha256: "synthetic-digest",
              quote: "합성 약관의 보장 조건",
            },
          ],
        },
      ],
      differences: [
        {
          coverage: candidate.ref,
          change: "ADDED",
          before: null,
          after: candidate,
        },
      ],
      guidance: {
        event_date: "2026-09-01",
        medical_event_id: props.eventId,
        event_version: 1,
        family_member_id: "synthetic-member",
        outcome: "CANDIDATES",
        candidates: [candidate],
        support: {
          evaluated_coverages: 1,
          total_coverages: 2,
          unsupported_coverages: 1,
        },
        versions: {},
      },
    },
    usage: {
      input_tokens: null,
      output_tokens: null,
      total_tokens: null,
      requests_reserved: 1,
      reserved_input_tokens: 100,
      reserved_output_tokens: 100,
      usage_complete: false,
    },
  };
}
beforeEach(() => vi.resetAllMocks());
afterEach(() => vi.useRealTimers());

describe("optional guidance review", () => {
  it("does not request anything on mount and submits the current run once on repeated clicks", async () => {
    let resolve!: (value: GuidanceReviewJob) => void;
    vi.mocked(createGuidanceReview).mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    render(<GuidanceReviewPanel {...props} />);
    expect(createGuidanceReview).not.toHaveBeenCalled();
    expect(getGuidanceReview).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "AI 선택 검수 시작" }));
    fireEvent.click(screen.getByRole("button", { name: "AI 선택 검수 시작" }));
    expect(createGuidanceReview).toHaveBeenCalledTimes(1);
    expect(createGuidanceReview).toHaveBeenCalledWith(
      props.eventId,
      { decision_run_id: props.decisionRunId, expected_event_version: 1 },
      expect.any(AbortSignal),
    );
    await act(async () => resolve(job("completed")));
    expect(screen.getByText("검수가 완료되었습니다.")).toBeVisible();
  });

  it("polls only its requested job, cancels it, and stops polling", async () => {
    vi.useFakeTimers();
    vi.mocked(createGuidanceReview).mockResolvedValue(job());
    vi.mocked(getGuidanceReview).mockResolvedValue(job("running"));
    vi.mocked(cancelGuidanceReview).mockResolvedValue(job("cancelled"));
    render(<GuidanceReviewPanel {...props} />);
    await act(async () =>
      fireEvent.click(
        screen.getByRole("button", { name: "AI 선택 검수 시작" }),
      ),
    );
    await act(async () => vi.advanceTimersByTimeAsync(1500));
    expect(getGuidanceReview).toHaveBeenCalledWith(
      "synthetic-review-001",
      expect.any(AbortSignal),
    );
    expect(screen.getByText(/이미 전송된 요청의 비용/)).toBeVisible();
    await act(async () =>
      fireEvent.click(screen.getByRole("button", { name: "검수 취소" })),
    );
    const count = vi.mocked(getGuidanceReview).mock.calls.length;
    await act(async () => vi.advanceTimersByTimeAsync(5000));
    expect(getGuidanceReview).toHaveBeenCalledTimes(count);
    expect(screen.getByText("검수를 취소했습니다.")).toBeVisible();
  });

  it("keeps the original answer when the provider is unavailable and never exposes backend messages", async () => {
    vi.mocked(createGuidanceReview).mockRejectedValue(
      new ApiError("RESOURCE_LIMIT_EXCEEDED", 503),
    );
    render(
      <>
        <p>기존 로컬 안내</p>
        <GuidanceReviewPanel {...props} />
      </>,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "AI 선택 검수 시작" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "검수를 지금 시작할 수 없습니다",
    );
    expect(screen.getByText("기존 로컬 안내")).toBeVisible();
    expect(screen.queryByText(/RESOURCE_LIMIT_EXCEEDED/)).toBeNull();
  });

  it("ignores a late result after the event or run changes", async () => {
    let resolve!: (value: GuidanceReviewJob) => void;
    vi.mocked(createGuidanceReview).mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const view = render(<GuidanceReviewPanel {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "AI 선택 검수 시작" }));
    const signal = vi.mocked(createGuidanceReview).mock.calls[0][2];
    view.rerender(
      <GuidanceReviewPanel
        {...props}
        eventVersion={2}
        decisionRunId="synthetic-run-002"
      />,
    );
    expect(signal?.aborted).toBe(true);
    await act(async () => resolve(job("completed")));
    expect(screen.queryByText("검수가 완료되었습니다.")).toBeNull();
    expect(
      screen.getByRole("button", { name: "AI 선택 검수 시작" }),
    ).toBeEnabled();
  });

  it("aborts requests and removes review state when the session expires", async () => {
    let resolve!: (value: GuidanceReviewJob) => void;
    vi.mocked(createGuidanceReview).mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    render(<GuidanceReviewPanel {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "AI 선택 검수 시작" }));
    const signal = vi.mocked(createGuidanceReview).mock.calls[0][2];
    act(() => authStore.clear());
    await act(async () => resolve(job("completed")));
    expect(signal?.aborted).toBe(true);
    expect(screen.queryByText("검수가 완료되었습니다.")).toBeNull();
    expect(
      screen.getByRole("button", { name: "AI 선택 검수 시작" }),
    ).toBeDisabled();
  });

  it("rejects mismatched and stale jobs instead of displaying their findings", async () => {
    vi.mocked(createGuidanceReview).mockResolvedValue({
      ...job("completed"),
      event_version: 2,
      stale: true,
    });
    render(<GuidanceReviewPanel {...props} />);
    await userEvent.click(
      screen.getByRole("button", { name: "AI 선택 검수 시작" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "현재 사건과 다른 검수 결과",
    );
    expect(screen.queryByText("검수가 완료되었습니다.")).toBeNull();
  });

  it("separates partial scope, AI opinion, and program differences without creating review claim actions", async () => {
    vi.mocked(createGuidanceReview).mockResolvedValue(reviewedJob());
    render(<GuidanceReviewPanel {...props} />);
    const user = userEvent.setup();
    await user.tab();
    expect(
      screen.getByRole("button", { name: "AI 선택 검수 시작" }),
    ).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(
      await screen.findByText("일부 자료의 검수가 완료되었습니다."),
    ).toBeVisible();
    expect(screen.getByText(/미검수 원문 묶음 1개/)).toBeVisible();
    expect(screen.getByText(/확인하지 못한 원문 범위는 4개/)).toBeVisible();
    expect(
      screen.getByText(/일부 조항의 해석은 검증하지 못했습니다/),
    ).toBeVisible();
    await user.click(screen.getByText("AI 검수 의견과 근거"));
    expect(screen.getByText("AI 의견 · 프로그램 결과에 미반영")).toBeVisible();
    expect(screen.getByText("합성 약관의 보장 조건")).toBeVisible();
    await user.click(screen.getByText("프로그램 재평가 결과와 변경점"));
    expect(screen.getByText("추가된 후보 · Sample Coverage")).toBeVisible();
    expect(screen.queryByRole("button", { name: /청구 준비/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "다시 확인" })).toBeNull();
    expect(screen.queryByText("SYNTHETIC_REASON")).toBeNull();
    await user.click(screen.getByText("검수 사용량"));
    expect(
      screen.getByText("전체 사용량은 아직 확인되지 않았습니다."),
    ).toBeVisible();
  });

  it("pauses polling after a read failure and resumes with GET instead of submitting a duplicate", async () => {
    vi.useFakeTimers();
    vi.mocked(createGuidanceReview).mockResolvedValue(job());
    vi.mocked(getGuidanceReview)
      .mockRejectedValueOnce(new Error("synthetic backend failure"))
      .mockResolvedValueOnce(job("completed"));
    render(<GuidanceReviewPanel {...props} />);
    await act(async () =>
      fireEvent.click(
        screen.getByRole("button", { name: "AI 선택 검수 시작" }),
      ),
    );
    await act(async () => vi.advanceTimersByTimeAsync(5000));
    expect(getGuidanceReview).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "상태 다시 확인" }));
    await act(async () => vi.advanceTimersByTimeAsync(1500));
    expect(getGuidanceReview).toHaveBeenCalledTimes(2);
    expect(createGuidanceReview).toHaveBeenCalledTimes(1);
    expect(screen.getByText("검수가 완료되었습니다.")).toBeVisible();
  });
});
