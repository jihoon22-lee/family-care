import type {
  CandidateCorrectionRequest,
  CoverageRulePublishRequest,
  CoverageRuleVersionResponse,
  CoverageRuleVersionsResponse,
  ExpectedVersionRequest,
  PolicyReviewItem,
  RiderClauseLinkRejectionRequest,
  RiderClauseLinkResponse,
} from "./generated";
import { apiRequest } from "./http";

export type RuleReviewDomain = "rider_clause" | "coverage_rule";
export type RuleReviewStatus =
  "NEEDS_REVIEW" | "AI_VERIFIED" | "USER_CONFIRMED";

export const listRuleReviewItems = (
  domain: RuleReviewDomain,
  status: RuleReviewStatus,
  signal?: AbortSignal,
) =>
  apiRequest<PolicyReviewItem[]>(
    `/api/v1/review-items?domain=${domain}&status=${status}`,
    { signal },
  );

export const correctRuleReviewField = (
  reviewItemId: string,
  request: CandidateCorrectionRequest,
  signal?: AbortSignal,
) =>
  apiRequest<PolicyReviewItem>(
    `/api/v1/review-items/${encodeURIComponent(reviewItemId)}/fields/${encodeURIComponent(request.field_id)}`,
    { body: JSON.stringify(request), method: "PATCH", signal },
  );

export const listRiderClauseLinks = (riderId: string, signal?: AbortSignal) =>
  apiRequest<RiderClauseLinkResponse[]>(
    `/api/v1/riders/${encodeURIComponent(riderId)}/clause-links`,
    { signal },
  );

export const confirmRiderClauseLink = (
  linkId: string,
  request: ExpectedVersionRequest,
  signal?: AbortSignal,
) =>
  apiRequest<RiderClauseLinkResponse>(
    `/api/v1/rider-clause-links/${encodeURIComponent(linkId)}/confirm`,
    { body: JSON.stringify(request), method: "POST", signal },
  );

export const rejectRiderClauseLink = (
  linkId: string,
  request: RiderClauseLinkRejectionRequest,
  signal?: AbortSignal,
) =>
  apiRequest<RiderClauseLinkResponse>(
    `/api/v1/rider-clause-links/${encodeURIComponent(linkId)}/reject`,
    { body: JSON.stringify(request), method: "POST", signal },
  );

export const listCoverageRuleVersions = (
  ruleId: string,
  signal?: AbortSignal,
) =>
  apiRequest<CoverageRuleVersionsResponse>(
    `/api/v1/coverage-rules/${encodeURIComponent(ruleId)}/versions`,
    { signal },
  );

export const publishCoverageRule = (
  ruleId: string,
  request: CoverageRulePublishRequest,
  signal?: AbortSignal,
) =>
  apiRequest<CoverageRuleVersionResponse>(
    `/api/v1/coverage-rules/${encodeURIComponent(ruleId)}/publish`,
    { body: JSON.stringify(request), method: "POST", signal },
  );
