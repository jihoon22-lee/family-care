import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiRequest } from "./http";
import {
  cancelGuidanceReview,
  createGuidanceReview,
  getGuidanceReview,
  getCurrentGuidanceReview,
} from "./guidance-reviews";

vi.mock("./http", () => ({ apiRequest: vi.fn() }));
beforeEach(() => vi.resetAllMocks());
describe("guidance review API", () => {
  it("looks up existing reviews without POST and binds reads to the requested run", async () => {
    const signal = new AbortController().signal;
    const input = {
      decision_run_id: "synthetic-run-002",
      expected_event_version: 2,
    };
    await getCurrentGuidanceReview("synthetic-event", input, signal);
    expect(apiRequest).toHaveBeenLastCalledWith(
      "/api/v1/medical-events/synthetic-event/guidance-reviews/current?decision_run_id=synthetic-run-002&expected_event_version=2",
      { method: "GET", signal },
    );
    await getGuidanceReview("synthetic-job", signal, input.decision_run_id);
    expect(apiRequest).toHaveBeenLastCalledWith(
      "/api/v1/guidance-reviews/synthetic-job?decision_run_id=synthetic-run-002",
      { method: "GET", signal },
    );
    await cancelGuidanceReview("synthetic-job", signal, input.decision_run_id);
    expect(apiRequest).toHaveBeenLastCalledWith(
      "/api/v1/guidance-reviews/synthetic-job/cancel?decision_run_id=synthetic-run-002",
      { method: "POST", signal },
    );
  });
  it("uses the canonical decision run request and forwards abort signals", async () => {
    const signal = new AbortController().signal;
    const input = {
      decision_run_id: "synthetic-run-001",
      expected_event_version: 3,
    };
    await createGuidanceReview("synthetic/event", input, signal);
    expect(apiRequest).toHaveBeenCalledWith(
      "/api/v1/medical-events/synthetic%2Fevent/guidance-reviews",
      {
        method: "POST",
        body: JSON.stringify(input),
        signal,
      },
    );
  });
  it("reads and cancels only the requested job", async () => {
    const signal = new AbortController().signal;
    await getGuidanceReview("synthetic/job", signal);
    expect(apiRequest).toHaveBeenLastCalledWith(
      "/api/v1/guidance-reviews/synthetic%2Fjob",
      { method: "GET", signal },
    );
    await cancelGuidanceReview("synthetic/job", signal);
    expect(apiRequest).toHaveBeenLastCalledWith(
      "/api/v1/guidance-reviews/synthetic%2Fjob/cancel",
      { method: "POST", signal },
    );
  });
});
