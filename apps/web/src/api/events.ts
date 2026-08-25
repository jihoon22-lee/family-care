import type {
  CoverageDecisionResponse,
  ExpectedVersionRequest,
  MedicalEventCreateRequest,
  MedicalEventResponse,
  MedicalEventUpdateRequest,
  StructureAcceptedResponse,
  StructuringJobResponse,
} from "./generated";
import { apiRequest } from "./http";

export type CreateMedicalEventRequest = MedicalEventCreateRequest;
export type UpdateMedicalEventRequest = MedicalEventUpdateRequest;
export type MedicalEvent = MedicalEventResponse;
export type AnalysisResponse = CoverageDecisionResponse;

function eventPath(eventId: string, suffix = ""): string {
  return `/api/v1/medical-events/${encodeURIComponent(eventId)}${suffix}`;
}

export function createMedicalEvent(
  input: CreateMedicalEventRequest,
  signal?: AbortSignal,
): Promise<MedicalEvent> {
  return apiRequest<MedicalEvent>("/api/v1/medical-events", {
    body: JSON.stringify(input),
    method: "POST",
    signal,
  });
}

export function getMedicalEvent(
  eventId: string,
  signal?: AbortSignal,
): Promise<MedicalEvent> {
  return apiRequest<MedicalEvent>(eventPath(eventId), {
    method: "GET",
    signal,
  });
}

export function updateMedicalEvent(
  eventId: string,
  input: UpdateMedicalEventRequest,
  signal?: AbortSignal,
): Promise<MedicalEvent> {
  return apiRequest<MedicalEvent>(eventPath(eventId), {
    body: JSON.stringify(input),
    method: "PATCH",
    signal,
  });
}

export function structureMedicalEvent(
  eventId: string,
  expectedVersion: number,
  signal?: AbortSignal,
): Promise<StructureAcceptedResponse> {
  const body: ExpectedVersionRequest = { expected_version: expectedVersion };
  return apiRequest<StructureAcceptedResponse>(
    eventPath(eventId, "/structure"),
    {
      body: JSON.stringify(body),
      method: "POST",
      signal,
    },
  );
}

/** Fetch a structuring job through the status URL returned by the API. */
export function getStructuringJob(
  statusUrl: string,
  signal?: AbortSignal,
): Promise<StructuringJobResponse> {
  return apiRequest<StructuringJobResponse>(statusUrl, {
    method: "GET",
    signal,
  });
}

/** Analyze synchronously; this endpoint does not return a polling job. */
export function analyzeMedicalEvent(
  eventId: string,
  signal?: AbortSignal,
): Promise<AnalysisResponse> {
  return apiRequest<AnalysisResponse>(eventPath(eventId, "/analyze"), {
    method: "POST",
    signal,
  });
}
