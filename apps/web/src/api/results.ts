import type {
  BenefitCalculationsResponse,
  CoverageDecisionResponse,
  EvidenceDetailResponse,
  GuidanceEvidenceDetail,
  GuidanceEvidenceRequest,
} from "./generated";
import { apiRequest } from "./http";

export type EventResult = CoverageDecisionResponse;
export type EvidenceResponse = EvidenceDetailResponse;
export type BenefitCalculations = BenefitCalculationsResponse;

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

export function getGuidanceEvidence(
  eventId: string,
  input: GuidanceEvidenceRequest,
  signal?: AbortSignal,
): Promise<GuidanceEvidenceDetail> {
  return apiRequest<GuidanceEvidenceDetail>(
    `/api/v1/medical-events/${encodeURIComponent(eventId)}/guidance-evidence`,
    { method: "POST", body: JSON.stringify(input), signal },
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

export function getBenefitCalculations(
  eventId: string,
  signal?: AbortSignal,
): Promise<BenefitCalculationsResponse> {
  return apiRequest<BenefitCalculationsResponse>(
    `/api/v1/medical-events/${encodeURIComponent(eventId)}/calculations`,
    { method: "GET", signal },
  );
}
