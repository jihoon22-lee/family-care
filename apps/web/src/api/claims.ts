import type {
  ClaimCaseListResponse,
  ClaimCaseResponse,
  ClaimCreateRequest,
  ClaimStatusEventResponse,
  ClaimTransitionRequest,
  ClaimUpdateRequest,
  ChecklistUpdateRequest,
} from "./generated";
import { apiRequest } from "./http";

export type ClaimCase = ClaimCaseResponse;
export type ClaimStatus = ClaimCaseResponse["status"];
export type ClaimTransition = ClaimStatusEventResponse;

export interface ListClaimCasesOptions {
  eventId?: string;
  status?: ClaimStatus;
  cursor?: string;
  limit?: number;
}

export function listDeletedClaimCases(
  signal?: AbortSignal,
): Promise<ClaimCaseListResponse> {
  return apiRequest<ClaimCaseListResponse>("/api/v1/claims/trash", {
    method: "GET",
    signal,
  });
}

function claimPath(claimId: string, suffix = ""): string {
  return `/api/v1/claims/${encodeURIComponent(claimId)}${suffix}`;
}

export function listClaimCases(
  options: ListClaimCasesOptions = {},
  signal?: AbortSignal,
): Promise<ClaimCaseListResponse> {
  const params = new URLSearchParams();
  if (options.eventId) params.set("event_id", options.eventId);
  if (options.status) params.set("status", options.status);
  if (options.cursor) params.set("cursor", options.cursor);
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  const query = params.toString();
  return apiRequest<ClaimCaseListResponse>(
    `/api/v1/claims${query ? `?${query}` : ""}`,
    { method: "GET", signal },
  );
}

export function getClaimCase(
  claimId: string,
  signal?: AbortSignal,
): Promise<ClaimCaseResponse> {
  return apiRequest<ClaimCaseResponse>(claimPath(claimId), {
    method: "GET",
    signal,
  });
}

export function createClaimCase(
  eventId: string,
  input: ClaimCreateRequest,
  signal?: AbortSignal,
): Promise<ClaimCaseResponse> {
  return apiRequest<ClaimCaseResponse>(
    `/api/v1/medical-events/${encodeURIComponent(eventId)}/claims`,
    {
      body: JSON.stringify(input),
      method: "POST",
      signal,
    },
  );
}

export function updateClaimCase(
  claimId: string,
  input: ClaimUpdateRequest,
  signal?: AbortSignal,
): Promise<ClaimCaseResponse> {
  return apiRequest<ClaimCaseResponse>(claimPath(claimId), {
    body: JSON.stringify(input),
    method: "PATCH",
    signal,
  });
}

export function transitionClaimCase(
  claimId: string,
  input: ClaimTransitionRequest,
  signal?: AbortSignal,
): Promise<ClaimCaseResponse> {
  return apiRequest<ClaimCaseResponse>(claimPath(claimId, "/transitions"), {
    body: JSON.stringify(input),
    method: "POST",
    signal,
  });
}

export function updateClaimChecklist(
  claimId: string,
  itemId: string,
  input: ChecklistUpdateRequest,
  signal?: AbortSignal,
): Promise<ClaimCaseResponse> {
  return apiRequest<ClaimCaseResponse>(
    claimPath(claimId, `/checklist/${encodeURIComponent(itemId)}`),
    {
      body: JSON.stringify(input),
      method: "PATCH",
      signal,
    },
  );
}

export function deleteClaimCase(
  claimId: string,
  expectedVersion: number,
  signal?: AbortSignal,
): Promise<void> {
  return apiRequest<void>(claimPath(claimId), {
    body: JSON.stringify({ expected_version: expectedVersion }),
    method: "DELETE",
    signal,
  });
}

export function restoreClaimCase(
  claimId: string,
  expectedVersion: number,
  signal?: AbortSignal,
): Promise<ClaimCaseResponse> {
  return apiRequest<ClaimCaseResponse>(claimPath(claimId, "/restore"), {
    body: JSON.stringify({ expected_version: expectedVersion }),
    method: "POST",
    signal,
  });
}
