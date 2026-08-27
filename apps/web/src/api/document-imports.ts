import type {
  BatchCreateRequest,
  BatchResponse,
  ImportSourceResponse,
} from "./generated";
import { apiRequest } from "./http";

export type DocumentBatch = BatchResponse;
export type DocumentBatchItem = BatchResponse["items"][number];
export type ImportSource = ImportSourceResponse;

export const listImportSources = (signal?: AbortSignal) =>
  apiRequest<ImportSourceResponse[]>("/api/v1/document-import-sources", {
    signal,
  });

export function createDocumentBatch(
  familyMemberId: string,
  sources: BatchCreateRequest["sources"],
  signal?: AbortSignal,
): Promise<BatchResponse> {
  const request: BatchCreateRequest = {
    family_member_id: familyMemberId,
    schema_version: "1",
    sources,
  };
  return apiRequest<BatchResponse>("/api/v1/document-batches", {
    body: JSON.stringify(request),
    method: "POST",
    signal,
  });
}

export const getDocumentBatch = (batchId: string, signal?: AbortSignal) =>
  apiRequest<BatchResponse>(
    `/api/v1/document-batches/${encodeURIComponent(batchId)}`,
    { signal },
  );

export async function handoffBatchPassword(
  batchId: string,
  password: string,
  signal?: AbortSignal,
): Promise<BatchResponse> {
  return apiRequest<BatchResponse>(
    `/api/v1/document-batches/${encodeURIComponent(batchId)}/password`,
    {
      body: JSON.stringify({ password }),
      method: "POST",
      signal,
    },
  );
}

export const cancelDocumentBatch = (batchId: string, signal?: AbortSignal) =>
  apiRequest<BatchResponse>(
    `/api/v1/document-batches/${encodeURIComponent(batchId)}/cancel`,
    { method: "POST", signal },
  );
