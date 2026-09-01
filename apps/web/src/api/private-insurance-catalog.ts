import type { KnowledgeContractDetailResponse } from "./generated";
import { ApiError } from "./errors";
import { apiRequest } from "./http";

function isContractDetail(
  value: unknown,
): value is KnowledgeContractDetailResponse {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    candidate.schema_version === "1" &&
    typeof candidate.contract === "object" &&
    candidate.contract !== null &&
    Array.isArray(candidate.coverages) &&
    Array.isArray(candidate.terms_assignments) &&
    Array.isArray(candidate.coverage_mappings) &&
    Array.isArray(candidate.terms_sections)
  );
}

export async function getPrivateInsuranceContract(
  contractId: string,
  signal?: AbortSignal,
): Promise<KnowledgeContractDetailResponse> {
  const response = await apiRequest<unknown>(
    `/api/v1/private-knowledge/current/contracts/${encodeURIComponent(contractId)}?section_limit=50`,
    { signal },
  );
  if (!isContractDetail(response)) throw new ApiError("INVALID_RESPONSE", 502);
  return response;
}
