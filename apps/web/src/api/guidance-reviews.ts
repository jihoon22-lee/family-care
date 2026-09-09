import type { GuidanceReviewJob, GuidanceReviewRequest } from "./generated";
import { apiRequest } from "./http";

export function createGuidanceReview(
  eventId: string,
  input: GuidanceReviewRequest,
  signal?: AbortSignal,
): Promise<GuidanceReviewJob> {
  return apiRequest<GuidanceReviewJob>(
    `/api/v1/medical-events/${encodeURIComponent(eventId)}/guidance-reviews`,
    {
      method: "POST",
      body: JSON.stringify(input),
      signal,
    },
  );
}

export function getGuidanceReview(
  jobId: string,
  signal?: AbortSignal,
  decisionRunId?: string,
): Promise<GuidanceReviewJob> {
  return apiRequest<GuidanceReviewJob>(
    `/api/v1/guidance-reviews/${encodeURIComponent(jobId)}${decisionRunId ? `?decision_run_id=${encodeURIComponent(decisionRunId)}` : ""}`,
    { method: "GET", signal },
  );
}

export function cancelGuidanceReview(
  jobId: string,
  signal?: AbortSignal,
  decisionRunId?: string,
): Promise<GuidanceReviewJob> {
  return apiRequest<GuidanceReviewJob>(
    `/api/v1/guidance-reviews/${encodeURIComponent(jobId)}/cancel${decisionRunId ? `?decision_run_id=${encodeURIComponent(decisionRunId)}` : ""}`,
    { method: "POST", signal },
  );
}

export function getCurrentGuidanceReview(
  eventId: string,
  input: GuidanceReviewRequest,
  signal?: AbortSignal,
): Promise<GuidanceReviewJob | null> {
  const query = new URLSearchParams({
    decision_run_id: input.decision_run_id,
    expected_event_version: String(input.expected_event_version),
  });
  return apiRequest<GuidanceReviewJob | null>(
    `/api/v1/medical-events/${encodeURIComponent(eventId)}/guidance-reviews/current?${query}`,
    { method: "GET", signal },
  );
}
