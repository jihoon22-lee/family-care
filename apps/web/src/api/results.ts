import type {
  CoverageDecisionResponse,
  EvidenceDetailResponse,
} from "./generated";
import { apiRequest } from "./http";

export type EventResult = CoverageDecisionResponse;
export type EvidenceResponse = EvidenceDetailResponse;

export function getEventResult(
  eventId: string,
  version: number,
  signal?: AbortSignal,
): Promise<EventResult> {
  return apiRequest<EventResult>(
    `/api/v1/medical-events/${encodeURIComponent(eventId)}/results/${encodeURIComponent(String(version))}`,
    { method: "GET", signal },
  );
}

/** Evidence is fetched on demand and remains outside persistent browser state. */
export function getEvidence(
  evidenceId: string,
  signal?: AbortSignal,
): Promise<EvidenceResponse> {
  return apiRequest<EvidenceResponse>(
    `/api/v1/evidence/${encodeURIComponent(evidenceId)}`,
    { method: "GET", signal },
  );
}
