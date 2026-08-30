import type {
  KnowledgeContractDetailResponse,
  KnowledgeContractPageResponse,
} from "./generated";
import { ApiError } from "./errors";
import { apiRequest } from "./http";

function isContractPage(
  value: unknown,
): value is KnowledgeContractPageResponse {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    candidate.schema_version === "1" &&
    Array.isArray(candidate.items) &&
    (candidate.next_cursor === null ||
      typeof candidate.next_cursor === "string") &&
    candidate.items.every(
      (item) =>
        typeof item === "object" &&
        item !== null &&
        typeof (item as Record<string, unknown>).id === "string" &&
        typeof (item as Record<string, unknown>).product_display === "string" &&
        typeof (item as Record<string, unknown>).coverage_count === "number",
    )
  );
}

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

export async function getPrivateInsuranceCatalog(
  memberId: string,
  signal?: AbortSignal,
): Promise<KnowledgeContractPageResponse> {
  const query = new URLSearchParams({
    family_member_id: memberId,
    limit: "100",
  });
  const response = await apiRequest<unknown>(
    `/api/v1/private-knowledge/current/contracts?${query.toString()}`,
    { signal },
  );
  if (!isContractPage(response)) throw new ApiError("INVALID_RESPONSE", 502);
  return response;
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
