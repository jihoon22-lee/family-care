import type {
  DocumentResolutionRequest,
  DocumentResolutionResponse,
  MemberInsuranceReconciliationResponse,
  OperationalLinkMutationResponse,
  OperationalLinkRequest,
} from "./generated";
import { ApiError } from "./errors";
import { apiRequest } from "./http";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isReconciliationResponse(
  value: unknown,
): value is MemberInsuranceReconciliationResponse {
  if (!isRecord(value) || !isRecord(value.summary)) return false;
  const summary = value.summary;
  const summaryFields = [
    "conflict_contracts",
    "documents_pending_contracts",
    "evidence_ready_contracts",
    "link_review_required_contracts",
    "orphan_operational_contracts",
    "total_contracts",
    "unresolved_unreadable_sources",
  ];
  return (
    value.schema_version === "1" &&
    typeof value.member_id === "string" &&
    typeof value.knowledge_run_id === "string" &&
    typeof value.generated_at === "string" &&
    summaryFields.every((field) => typeof summary[field] === "number") &&
    Array.isArray(value.contracts) &&
    value.contracts.every(
      (contract) =>
        isRecord(contract) &&
        typeof contract.knowledge_contract_id === "string" &&
        typeof contract.insurer_display === "string" &&
        typeof contract.product_display === "string" &&
        typeof contract.reconciliation_state === "string" &&
        isRecord(contract.operational_link) &&
        isNullableString(contract.operational_link.id) &&
        isNullableString(contract.operational_link.policy_contract_id),
    ) &&
    Array.isArray(value.orphan_operational_contracts) &&
    value.orphan_operational_contracts.every(
      (policy) =>
        isRecord(policy) &&
        typeof policy.policy_contract_id === "string" &&
        typeof policy.insurer_display === "string" &&
        typeof policy.product_display === "string",
    ) &&
    Array.isArray(value.unresolved_sources) &&
    value.unresolved_sources.every(
      (source) =>
        isRecord(source) &&
        typeof source.document_batch_item_id === "string" &&
        typeof source.display_label === "string" &&
        typeof source.processing_state === "string" &&
        isNullableString(source.current_resolution_id),
    )
  );
}

function isOperationalLinkResponse(
  value: unknown,
): value is OperationalLinkMutationResponse {
  return (
    isRecord(value) &&
    value.schema_version === "1" &&
    typeof value.id === "string" &&
    typeof value.knowledge_contract_id === "string" &&
    isNullableString(value.policy_contract_id) &&
    typeof value.decision === "string" &&
    typeof value.conflict === "boolean" &&
    value.authority === "USER_CONFIRMED_OPERATIONAL_IDENTITY"
  );
}

function isDocumentResolutionResponse(
  value: unknown,
): value is DocumentResolutionResponse {
  return (
    isRecord(value) &&
    value.schema_version === "1" &&
    typeof value.id === "string" &&
    typeof value.failed_item_id === "string" &&
    isNullableString(value.replacement_item_id) &&
    typeof value.resolution === "string" &&
    value.authority === "USER_CONFIRMED_DOCUMENT_RESOLUTION"
  );
}

export async function getInsuranceReconciliation(
  memberId: string,
  signal?: AbortSignal,
): Promise<MemberInsuranceReconciliationResponse> {
  const response = await apiRequest<unknown>(
    `/api/v1/family-members/${encodeURIComponent(memberId)}/insurance-reconciliation`,
    { signal },
  );
  if (!isReconciliationResponse(response))
    throw new ApiError("INVALID_RESPONSE", 502);
  return response;
}

export async function confirmOperationalLink(
  contractId: string,
  request: OperationalLinkRequest,
  signal?: AbortSignal,
): Promise<OperationalLinkMutationResponse> {
  const response = await apiRequest<unknown>(
    `/api/v1/private-knowledge/current/contracts/${encodeURIComponent(contractId)}/operational-link`,
    {
      body: JSON.stringify(request),
      method: "POST",
      signal,
    },
  );
  if (!isOperationalLinkResponse(response))
    throw new ApiError("INVALID_RESPONSE", 502);
  return response;
}

export async function confirmDocumentResolution(
  itemId: string,
  request: DocumentResolutionRequest,
  signal?: AbortSignal,
): Promise<DocumentResolutionResponse> {
  const response = await apiRequest<unknown>(
    `/api/v1/document-batch-items/${encodeURIComponent(itemId)}/resolution`,
    {
      body: JSON.stringify(request),
      method: "POST",
      signal,
    },
  );
  if (!isDocumentResolutionResponse(response))
    throw new ApiError("INVALID_RESPONSE", 502);
  return response;
}
