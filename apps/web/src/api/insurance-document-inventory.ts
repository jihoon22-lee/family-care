import type {
  DocumentSetCreateRequest,
  DocumentSetItemCreateRequest,
  ExpectedItemVersionRequest,
  InsuranceDocumentSetResponse,
  InsuranceDocumentSetItemMutationResponse,
  MemberInsuranceDocumentInventoryResponse,
} from "./generated";
import { ApiError } from "./errors";
import { apiRequest } from "./http";

function isInventoryResponse(
  value: unknown,
): value is MemberInsuranceDocumentInventoryResponse {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    candidate.schema_version === "1" &&
    typeof candidate.member_id === "string" &&
    typeof candidate.summary === "object" &&
    candidate.summary !== null &&
    Array.isArray(candidate.registered_policies) &&
    Array.isArray(candidate.unregistered_document_sets) &&
    Array.isArray(candidate.unpaired_components) &&
    Array.isArray(candidate.unreadable_sources)
  );
}

export async function getInsuranceDocumentInventory(
  memberId: string,
  signal?: AbortSignal,
): Promise<MemberInsuranceDocumentInventoryResponse> {
  const response = await apiRequest<unknown>(
    `/api/v1/family-members/${encodeURIComponent(memberId)}/insurance-document-inventory`,
    { signal },
  );
  if (!isInventoryResponse(response))
    throw new ApiError("INVALID_RESPONSE", 502);
  return response;
}

export function attachInsuranceDocumentSetItem(
  documentSetId: string,
  request: DocumentSetItemCreateRequest,
  signal?: AbortSignal,
): Promise<InsuranceDocumentSetItemMutationResponse> {
  return apiRequest<InsuranceDocumentSetItemMutationResponse>(
    `/api/v1/insurance-document-sets/${encodeURIComponent(documentSetId)}/items`,
    {
      body: JSON.stringify(request),
      method: "POST",
      signal,
    },
  );
}

export function createInsuranceDocumentSet(
  memberId: string,
  request: DocumentSetCreateRequest,
  signal?: AbortSignal,
): Promise<InsuranceDocumentSetResponse> {
  return apiRequest<InsuranceDocumentSetResponse>(
    `/api/v1/family-members/${encodeURIComponent(memberId)}/insurance-document-sets`,
    {
      body: JSON.stringify(request),
      method: "POST",
      signal,
    },
  );
}

export function detachInsuranceDocumentSetItem(
  itemId: string,
  request: ExpectedItemVersionRequest,
  signal?: AbortSignal,
): Promise<void> {
  return apiRequest<void>(
    `/api/v1/insurance-document-set-items/${encodeURIComponent(itemId)}`,
    {
      body: JSON.stringify(request),
      method: "DELETE",
      signal,
    },
  );
}
