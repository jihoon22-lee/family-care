import type {
  CandidateConfirmationRequest,
  CandidateCorrectionRequest,
  CandidateRejectionRequest,
  FamilyMemberResponse,
  PolicyResponse,
  PolicyReviewItem,
  RiderResponse,
} from "./generated";
import { apiRequest } from "./http";

export const listFamilyMembers = (signal?: AbortSignal) =>
  apiRequest<FamilyMemberResponse[]>("/api/v1/family-members", { signal });

export const listPolicies = (signal?: AbortSignal) =>
  apiRequest<PolicyResponse[]>("/api/v1/policies", { signal });

export const listPolicyRiders = (policyId: string, signal?: AbortSignal) =>
  apiRequest<RiderResponse[]>(
    `/api/v1/policies/${encodeURIComponent(policyId)}/riders`,
    { signal },
  );

export const listReviewItems = (signal?: AbortSignal) =>
  apiRequest<PolicyReviewItem[]>(
    "/api/v1/review-items?domain=policy&status=NEEDS_REVIEW",
    { signal },
  );

export const getReviewItem = (reviewItemId: string, signal?: AbortSignal) =>
  apiRequest<PolicyReviewItem>(
    `/api/v1/review-items/${encodeURIComponent(reviewItemId)}`,
    { signal },
  );

export const correctCandidateField = (
  policyId: string,
  request: CandidateCorrectionRequest,
  signal?: AbortSignal,
) =>
  apiRequest<PolicyReviewItem>(
    `/api/v1/policies/${encodeURIComponent(policyId)}/candidate-fields/${encodeURIComponent(request.field_id)}`,
    { body: JSON.stringify(request), method: "PATCH", signal },
  );

export const confirmReviewItem = (
  reviewItemId: string,
  request: CandidateConfirmationRequest,
  signal?: AbortSignal,
) =>
  apiRequest<PolicyReviewItem>(
    `/api/v1/review-items/${encodeURIComponent(reviewItemId)}/confirm`,
    { body: JSON.stringify(request), method: "POST", signal },
  );

export const rejectReviewItem = (
  reviewItemId: string,
  request: CandidateRejectionRequest,
  signal?: AbortSignal,
) =>
  apiRequest<PolicyReviewItem>(
    `/api/v1/review-items/${encodeURIComponent(reviewItemId)}/reject`,
    { body: JSON.stringify(request), method: "POST", signal },
  );
