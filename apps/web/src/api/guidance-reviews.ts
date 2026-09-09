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
): Promise<GuidanceReviewJob> {
  return apiRequest<GuidanceReviewJob>(
    `/api/v1/guidance-reviews/${encodeURIComponent(jobId)}`,
    { method: "GET", signal },
  );
}

export function cancelGuidanceReview(
  jobId: string,
  signal?: AbortSignal,
): Promise<GuidanceReviewJob> {
  return apiRequest<GuidanceReviewJob>(
    `/api/v1/guidance-reviews/${encodeURIComponent(jobId)}/cancel`,
    { method: "POST", signal },
  );
}
