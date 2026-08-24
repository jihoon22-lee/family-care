import type {
  ClauseHierarchyNodeResponse,
  ClauseHierarchyResponse,
  ClauseSearchHitResponse,
  ClauseSearchQuery,
  ClauseSearchResponse,
  TermsEditionResponse,
} from "./generated";
import { apiRequest } from "./http";

export const CLAUSE_SEARCH_NORMALIZATION_VERSION = "unicode-nfc-v1";

export type ClauseHierarchyNode = ClauseHierarchyNodeResponse & {
  children?: ClauseHierarchyNode[];
};
export type ClauseSearchHit = ClauseSearchHitResponse;
export type ClauseSearchRequest = ClauseSearchQuery;
export type {
  ClauseHierarchyResponse,
  ClauseSearchResponse,
  TermsEditionResponse,
};

export const listTermsEditions = (signal?: AbortSignal) =>
  apiRequest<TermsEditionResponse[]>("/api/v1/terms-editions", { signal });

export const getClauseHierarchy = (
  termsEditionId: string,
  signal?: AbortSignal,
) =>
  apiRequest<ClauseHierarchyResponse>(
    `/api/v1/terms-editions/${encodeURIComponent(termsEditionId)}/clauses`,
    { signal },
  );

export const searchClauses = (
  request: ClauseSearchRequest,
  signal?: AbortSignal,
) =>
  apiRequest<ClauseSearchResponse>("/api/v1/clauses/search", {
    body: JSON.stringify(request),
    method: "POST",
    signal,
  });
