// GENERATED FILE: do not edit; source packages/contracts/openapi/familycare.v1.json
// Candidate schemas remain available before Task 3 registers their API routes.

export const API_PATHS = [
  "/api/v1/analysis-jobs/{job_id}",
  "/api/v1/clauses/search",
  "/api/v1/coverage-rules/{rule_id}/publish",
  "/api/v1/coverage-rules/{rule_id}/versions",
  "/api/v1/documents/analysis",
  "/api/v1/family-members",
  "/api/v1/family-members/trash",
  "/api/v1/family-members/{member_id}",
  "/api/v1/family-members/{member_id}/restore",
  "/api/v1/policies",
  "/api/v1/policies/trash",
  "/api/v1/policies/{policy_id}",
  "/api/v1/policies/{policy_id}/candidate-fields/{field_id}",
  "/api/v1/policies/{policy_id}/restore",
  "/api/v1/policies/{policy_id}/riders",
  "/api/v1/review-items",
  "/api/v1/review-items/{review_item_id}",
  "/api/v1/review-items/{review_item_id}/candidate-fields/{field_id}",
  "/api/v1/review-items/{review_item_id}/confirm",
  "/api/v1/review-items/{review_item_id}/fields/{field_id}",
  "/api/v1/review-items/{review_item_id}/reject",
  "/api/v1/rider-clause-links/{link_id}/confirm",
  "/api/v1/rider-clause-links/{link_id}/reject",
  "/api/v1/riders/{rider_id}/clause-links",
  "/api/v1/terms-editions",
  "/api/v1/terms-editions/{terms_edition_id}/clauses",
  "/health/live",
  "/health/ready",
] as const;

export type ApiPath = (typeof API_PATHS)[number];

export const API_OPERATIONS = [
  {
    method: "GET",
    path: "/api/v1/analysis-jobs/{job_id}",
    operationId: "get_analysis_job_api_v1_analysis_jobs__job_id__get",
  },
  {
    method: "POST",
    path: "/api/v1/clauses/search",
    operationId: "search_clauses_api_v1_clauses_search_post",
  },
  {
    method: "POST",
    path: "/api/v1/coverage-rules/{rule_id}/publish",
    operationId:
      "publish_coverage_rule_api_v1_coverage_rules__rule_id__publish_post",
  },
  {
    method: "GET",
    path: "/api/v1/coverage-rules/{rule_id}/versions",
    operationId:
      "list_coverage_rule_versions_api_v1_coverage_rules__rule_id__versions_get",
  },
  {
    method: "POST",
    path: "/api/v1/documents/analysis",
    operationId: "submit_document_analysis_api_v1_documents_analysis_post",
  },
  {
    method: "GET",
    path: "/api/v1/family-members",
    operationId: "list_family_members_api_v1_family_members_get",
  },
  {
    method: "POST",
    path: "/api/v1/family-members",
    operationId: "create_family_member_api_v1_family_members_post",
  },
  {
    method: "GET",
    path: "/api/v1/family-members/trash",
    operationId: "list_deleted_family_members_api_v1_family_members_trash_get",
  },
  {
    method: "DELETE",
    path: "/api/v1/family-members/{member_id}",
    operationId:
      "delete_family_member_api_v1_family_members__member_id__delete",
  },
  {
    method: "GET",
    path: "/api/v1/family-members/{member_id}",
    operationId: "get_family_member_api_v1_family_members__member_id__get",
  },
  {
    method: "PATCH",
    path: "/api/v1/family-members/{member_id}",
    operationId: "update_family_member_api_v1_family_members__member_id__patch",
  },
  {
    method: "POST",
    path: "/api/v1/family-members/{member_id}/restore",
    operationId:
      "restore_family_member_api_v1_family_members__member_id__restore_post",
  },
  {
    method: "GET",
    path: "/api/v1/policies",
    operationId: "list_policies_api_v1_policies_get",
  },
  {
    method: "POST",
    path: "/api/v1/policies",
    operationId: "create_policy_api_v1_policies_post",
  },
  {
    method: "GET",
    path: "/api/v1/policies/trash",
    operationId: "list_deleted_policies_api_v1_policies_trash_get",
  },
  {
    method: "DELETE",
    path: "/api/v1/policies/{policy_id}",
    operationId: "delete_policy_api_v1_policies__policy_id__delete",
  },
  {
    method: "GET",
    path: "/api/v1/policies/{policy_id}",
    operationId: "get_policy_api_v1_policies__policy_id__get",
  },
  {
    method: "PATCH",
    path: "/api/v1/policies/{policy_id}",
    operationId: "update_policy_api_v1_policies__policy_id__patch",
  },
  {
    method: "PATCH",
    path: "/api/v1/policies/{policy_id}/candidate-fields/{field_id}",
    operationId:
      "correct_candidate_field_api_v1_policies__policy_id__candidate_fields__field_id__patch",
  },
  {
    method: "POST",
    path: "/api/v1/policies/{policy_id}/restore",
    operationId: "restore_policy_api_v1_policies__policy_id__restore_post",
  },
  {
    method: "GET",
    path: "/api/v1/policies/{policy_id}/riders",
    operationId: "list_policy_riders_api_v1_policies__policy_id__riders_get",
  },
  {
    method: "GET",
    path: "/api/v1/review-items",
    operationId: "list_review_items_api_v1_review_items_get",
  },
  {
    method: "GET",
    path: "/api/v1/review-items/{review_item_id}",
    operationId: "get_review_item_api_v1_review_items__review_item_id__get",
  },
  {
    method: "PATCH",
    path: "/api/v1/review-items/{review_item_id}/candidate-fields/{field_id}",
    operationId:
      "correct_review_item_field_api_v1_review_items__review_item_id__candidate_fields__field_id__patch",
  },
  {
    method: "POST",
    path: "/api/v1/review-items/{review_item_id}/confirm",
    operationId:
      "confirm_candidate_api_v1_review_items__review_item_id__confirm_post",
  },
  {
    method: "PATCH",
    path: "/api/v1/review-items/{review_item_id}/fields/{field_id}",
    operationId:
      "correct_typed_review_item_field_api_v1_review_items__review_item_id__fields__field_id__patch",
  },
  {
    method: "POST",
    path: "/api/v1/review-items/{review_item_id}/reject",
    operationId:
      "reject_candidate_api_v1_review_items__review_item_id__reject_post",
  },
  {
    method: "POST",
    path: "/api/v1/rider-clause-links/{link_id}/confirm",
    operationId:
      "confirm_rider_clause_link_api_v1_rider_clause_links__link_id__confirm_post",
  },
  {
    method: "POST",
    path: "/api/v1/rider-clause-links/{link_id}/reject",
    operationId:
      "reject_rider_clause_link_api_v1_rider_clause_links__link_id__reject_post",
  },
  {
    method: "GET",
    path: "/api/v1/riders/{rider_id}/clause-links",
    operationId:
      "list_rider_clause_links_api_v1_riders__rider_id__clause_links_get",
  },
  {
    method: "GET",
    path: "/api/v1/terms-editions",
    operationId: "list_terms_editions_api_v1_terms_editions_get",
  },
  {
    method: "GET",
    path: "/api/v1/terms-editions/{terms_edition_id}/clauses",
    operationId:
      "get_clause_hierarchy_api_v1_terms_editions__terms_edition_id__clauses_get",
  },
  {
    method: "GET",
    path: "/health/live",
    operationId: "liveness_health_live_get",
  },
  {
    method: "GET",
    path: "/health/ready",
    operationId: "readiness_endpoint_health_ready_get",
  },
] as const;

export type ApiOperation = (typeof API_OPERATIONS)[number];

export type CandidateErrorResponseErrorCode =
  "INVALID_CANDIDATE_CORRECTION" | "REVIEW_ITEM_NOT_FOUND" | "VERSION_CONFLICT";

export type ClauseErrorResponseErrorCode =
  | "AUTHENTICATION_REQUIRED"
  | "CLAUSE_NOT_FOUND"
  | "EVIDENCE_INVALID"
  | "INVALID_REQUEST"
  | "POLICY_STATE_CONFLICT"
  | "RESOURCE_LIMIT_EXCEEDED"
  | "SEARCH_INDEX_VERSION_MISMATCH"
  | "TERMS_EDITION_NOT_FOUND"
  | "VERSION_CONFLICT";

export type ErrorResponseErrorCode =
  | "ANALYSIS_JOB_NOT_FOUND"
  | "DOCUMENT_NOT_FOUND"
  | "DOCUMENT_PATH_ESCAPE"
  | "DOCUMENT_TOO_LARGE"
  | "EXTRACTION_TIMEOUT"
  | "INVALID_REQUEST"
  | "PAGE_LIMIT_EXCEEDED"
  | "PASSWORD_INVALID"
  | "PASSWORD_REQUIRED"
  | "PDF_CORRUPT"
  | "RESOURCE_LIMIT_EXCEEDED"
  | "TEMP_CLEANUP_FAILED"
  | "UNSUPPORTED_FILE_TYPE";

export type PolicyErrorResponseErrorCode =
  | "AUTHENTICATION_REQUIRED"
  | "EVIDENCE_INVALID"
  | "FAMILY_MEMBER_NOT_FOUND"
  | "INVALID_REQUEST"
  | "POLICY_NOT_FOUND"
  | "POLICY_STATE_CONFLICT"
  | "RESOURCE_LIMIT_EXCEEDED"
  | "VERSION_CONFLICT";

export type AggregateId = string;

export interface AnalysisAcceptedResponse {
  job_id: string;
  schema_version: "1";
  state: "queued";
  status_url: string;
}

export interface AnalysisJobStatusResponse {
  attempts: number;
  document_id: string;
  error_code?:
    | "ANALYSIS_JOB_NOT_FOUND"
    | "DOCUMENT_NOT_FOUND"
    | "DOCUMENT_PATH_ESCAPE"
    | "DOCUMENT_TOO_LARGE"
    | "EXTRACTION_TIMEOUT"
    | "INVALID_REQUEST"
    | "PAGE_LIMIT_EXCEEDED"
    | "PASSWORD_INVALID"
    | "PASSWORD_REQUIRED"
    | "PDF_CORRUPT"
    | "RESOURCE_LIMIT_EXCEEDED"
    | "TEMP_CLEANUP_FAILED"
    | "UNSUPPORTED_FILE_TYPE"
    | null;
  extraction_summary?: ExtractionSummaryResponse | null;
  job_id: string;
  schema_version: "1";
  state:
    | "cancelled"
    | "permanently_failed"
    | "queued"
    | "retryable_failed"
    | "running"
    | "succeeded";
}

export interface CandidateConfirmationRequest {
  expected_version: number;
}

export interface CandidateCorrectionRequest {
  evidence_id: string;
  expected_version: number;
  field_id:
    | "benefit_type"
    | "clause_id"
    | "contract_end"
    | "contract_start"
    | "coverage_end"
    | "coverage_start"
    | "currency"
    | "date_boundary"
    | "decimal_boundary"
    | "fact_field"
    | "insurer"
    | "link_review_state"
    | "policy_status"
    | "product_name"
    | "renewable"
    | "required"
    | "rider_id"
    | "rider_key"
    | "rider_name"
    | "rider_status"
    | "rule_kind"
    | "rule_operator"
    | "sum_assured"
    | "terms_edition_id"
    | "unit";
  value: CandidateScalar;
}

export type CandidateErrorCode =
  "VERSION_CONFLICT" | "REVIEW_ITEM_NOT_FOUND" | "INVALID_CANDIDATE_CORRECTION";

export interface CandidateErrorResponse {
  error_code:
    | "INVALID_CANDIDATE_CORRECTION"
    | "REVIEW_ITEM_NOT_FOUND"
    | "VERSION_CONFLICT";
  message: string;
}

export interface CandidateEvidenceRef {
  bbox: [number, number, number, number] | null;
  bounded_excerpt: string;
  document_label: string;
  document_version_id: string;
  evidence_id: string;
  page: number;
}

export interface CandidateField {
  evidence_ids: Array<string>;
  field_id:
    | "benefit_type"
    | "clause_id"
    | "contract_end"
    | "contract_start"
    | "coverage_end"
    | "coverage_start"
    | "currency"
    | "date_boundary"
    | "decimal_boundary"
    | "fact_field"
    | "insurer"
    | "link_review_state"
    | "policy_status"
    | "product_name"
    | "renewable"
    | "required"
    | "rider_id"
    | "rider_key"
    | "rider_name"
    | "rider_status"
    | "rule_kind"
    | "rule_operator"
    | "sum_assured"
    | "terms_edition_id"
    | "unit";
  value: CandidateScalar;
}

export type CandidateIssueCode =
  | "MISSING_EVIDENCE"
  | "CONFLICTING_EVIDENCE"
  | "TERMS_ONLY_RIDER"
  | "UNSUPPORTED_STRUCTURE"
  | "LOW_CONFIDENCE"
  | "INVALID_UNIT"
  | "INVALID_DATE"
  | "WRONG_EDITION"
  | "STALE_EVIDENCE"
  | "UNSUPPORTED_DSL"
  | "COMMON_SPECIAL_TERMS_CONFLICT";

export type CandidateKind =
  | "policy_contract"
  | "policy_party"
  | "rider"
  | "rider_clause"
  | "coverage_rule";

export type CandidateRejectionReason =
  | "NOT_ENROLLED"
  | "TERMS_ONLY_RIDER"
  | "DUPLICATE_CANDIDATE"
  | "INVALID_EVIDENCE"
  | "UNSUPPORTED_STRUCTURE";

export interface CandidateRejectionRequest {
  expected_version: number;
  reason_code:
    | "DUPLICATE_CANDIDATE"
    | "INVALID_EVIDENCE"
    | "NOT_ENROLLED"
    | "TERMS_ONLY_RIDER"
    | "UNSUPPORTED_STRUCTURE";
}

export type CandidateScalar = string | number | boolean | null;

export type CandidateStatus =
  "AI_VERIFIED" | "NEEDS_REVIEW" | "USER_CONFIRMED" | "rejected";

export type CandidateVersionId = string;

export interface ClauseErrorResponse {
  error_code:
    | "AUTHENTICATION_REQUIRED"
    | "CLAUSE_NOT_FOUND"
    | "EVIDENCE_INVALID"
    | "INVALID_REQUEST"
    | "POLICY_STATE_CONFLICT"
    | "RESOURCE_LIMIT_EXCEEDED"
    | "SEARCH_INDEX_VERSION_MISMATCH"
    | "TERMS_EDITION_NOT_FOUND"
    | "VERSION_CONFLICT";
  fields?: Array<string> | null;
  message: string;
}

export interface ClauseEvidenceResponse {
  bbox: [number, number, number, number] | null;
  content_sha256: string;
  document_version_id: string;
  evidence_id: string;
  page_number: number;
}

export interface ClauseHierarchyNodeResponse {
  clause_id: string;
  clause_type: string;
  evidence: Array<ClauseEvidenceResponse>;
  excerpt: string;
  label: string;
  normalization_version: string;
  parent_clause_id: string | null;
  physical_page_end: number;
  physical_page_start: number;
}

export interface ClauseHierarchyResponse {
  clauses: Array<ClauseHierarchyNodeResponse>;
  terms_edition_id: string;
}

export interface ClauseSearchHitResponse {
  clause_id: string;
  evidence: Array<ClauseEvidenceResponse>;
  excerpt: string;
  label: string;
  normalization_version: string;
  physical_page_end: number;
  physical_page_start: number;
  relevance: number;
  terms_edition_id: string;
}

export interface ClauseSearchQuery {
  effective_on?: string | null;
  insurer_key?: string | null;
  limit?: number;
  product_key?: string | null;
  q: string;
  terms_edition_id?: string | null;
}

export interface ClauseSearchResponse {
  hits: Array<ClauseSearchHitResponse>;
  normalization_version: "unicode-nfc-v1";
  query_matched_count: number;
  schema_version: "1";
}

export interface CoverageRulePublishRequest {
  expected_version: number;
  version_id: string;
}

export interface CoverageRuleVersionResponse {
  evidence: Array<ClauseEvidenceResponse>;
  executable: boolean;
  generator_version: string;
  input_field_paths: Array<string>;
  required: boolean;
  result_reason_code: string;
  review_state: "AI_VERIFIED" | "NEEDS_REVIEW" | "USER_CONFIRMED";
  rule_kind: string;
  schema_version: "coverage-rule-v1";
  verifier_version: string;
  version_id: string;
  version_number: number;
}

export interface CoverageRuleVersionsResponse {
  expected_version: number;
  rule_id: string;
  versions: Array<CoverageRuleVersionResponse>;
}

export interface DocumentAnalysisRequest {
  document_kind:
    "amendment" | "application" | "claim" | "policy" | "supporting" | "terms";
  extractor_config: ExtractorConfigRequest;
  schema_version: "1";
  source_key: string;
}

export type DocumentVersionId = string;

export interface ErrorResponse {
  error_code:
    | "ANALYSIS_JOB_NOT_FOUND"
    | "DOCUMENT_NOT_FOUND"
    | "DOCUMENT_PATH_ESCAPE"
    | "DOCUMENT_TOO_LARGE"
    | "EXTRACTION_TIMEOUT"
    | "INVALID_REQUEST"
    | "PAGE_LIMIT_EXCEEDED"
    | "PASSWORD_INVALID"
    | "PASSWORD_REQUIRED"
    | "PDF_CORRUPT"
    | "RESOURCE_LIMIT_EXCEEDED"
    | "TEMP_CLEANUP_FAILED"
    | "UNSUPPORTED_FILE_TYPE";
  fields?: Array<string> | null;
  message: string;
}

export type EvidenceId = string;

export interface EvidenceRef {
  bbox: Array<number> | null;
  bounded_excerpt: string;
  document_label: string;
  document_version_id: DocumentVersionId;
  evidence_id: EvidenceId;
  page: number;
}

export interface EvidenceResponse {
  bbox: [number, number, number, number] | null;
  content_sha256: string;
  document_version_id: string;
  evidence_id: string;
  physical_page: number;
  review_state: "AI_VERIFIED" | "NEEDS_REVIEW" | "USER_CONFIRMED";
}

export interface ExpectedVersionRequest {
  expected_version: number;
}

export interface ExtractionSummaryResponse {
  block_count: number;
  cell_count: number;
  page_count: number;
  table_count: number;
}

export interface ExtractorConfigRequest {
  profile: "quality-v1";
  quality_rule_version: "quality-v1";
  table_strategy: "auto" | "lines" | "text";
}

export interface FamilyMemberCreateRequest {
  display_name: string;
  internal_alias: string;
}

export interface FamilyMemberResponse {
  deleted: boolean;
  display_name: string;
  id: string;
  internal_alias: string;
  version: number;
}

export interface FamilyMemberUpdateRequest {
  display_name?: string | null;
  expected_version: number;
  internal_alias?: string | null;
}

export interface HealthResponse {
  service?: "api";
  status: "ok" | "ready" | "unavailable";
  version?: string;
}

export interface PolicyCandidate {
  aggregate_id: AggregateId | null;
  candidate_kind: CandidateKind;
  candidate_version_id: CandidateVersionId;
  evidence: Array<EvidenceRef>;
  expected_version: PositiveVersion;
  fields: Array<CandidateField>;
  issues: Array<ReviewIssue>;
  status: CandidateStatus;
}

export interface PolicyCandidateBatch {
  candidates: Array<PolicyCandidate>;
  schema_version: "1";
}

export type PolicyCandidateFieldId =
  | "insurer"
  | "product_name"
  | "contract_start"
  | "contract_end"
  | "policy_status"
  | "rider_name"
  | "rider_key"
  | "benefit_type"
  | "sum_assured"
  | "currency"
  | "coverage_start"
  | "coverage_end"
  | "renewable"
  | "rider_status"
  | "rider_id"
  | "terms_edition_id"
  | "clause_id"
  | "link_review_state"
  | "rule_kind"
  | "rule_operator"
  | "fact_field"
  | "unit"
  | "decimal_boundary"
  | "date_boundary"
  | "required";

export interface PolicyCreateRequest {
  contract_date?: string | null;
  coverage_end_date?: string | null;
  coverage_start_date?: string | null;
  insurer_display: string;
  insurer_key: string;
  parties: Array<PolicyPartyCreateRequest>;
  product_display: string;
  product_key: string;
  source_document_version_id: string;
  source_evidence_id: string;
  status?: "active" | "inactive" | "expired" | "cancelled" | "unknown";
  status_evidence_id?: string | null;
}

export interface PolicyErrorResponse {
  error_code:
    | "AUTHENTICATION_REQUIRED"
    | "EVIDENCE_INVALID"
    | "FAMILY_MEMBER_NOT_FOUND"
    | "INVALID_REQUEST"
    | "POLICY_NOT_FOUND"
    | "POLICY_STATE_CONFLICT"
    | "RESOURCE_LIMIT_EXCEEDED"
    | "VERSION_CONFLICT";
  fields?: Array<string> | null;
  message: string;
}

export interface PolicyPartyCreateRequest {
  effective_from?: string | null;
  effective_to?: string | null;
  evidence_id: string;
  family_member_id: string;
  role:
    "policyholder" | "primary_insured" | "additional_insured" | "beneficiary";
}

export interface PolicyPartyResponse {
  effective_from: string | null;
  effective_to: string | null;
  evidence: EvidenceResponse;
  family_member_id: string;
  id: string;
  role:
    "policyholder" | "primary_insured" | "additional_insured" | "beneficiary";
  version: number;
}

export interface PolicyResponse {
  contract_date: string | null;
  coverage_end_date: string | null;
  coverage_start_date: string | null;
  deleted: boolean;
  id: string;
  insurer_display: string;
  insurer_key: string;
  parties: Array<PolicyPartyResponse>;
  product_display: string;
  product_key: string;
  source_document_version_id: string;
  source_evidence: EvidenceResponse;
  status: "active" | "inactive" | "expired" | "cancelled" | "unknown";
  status_evidence: EvidenceResponse | null;
  version: number;
}

export interface PolicyReviewItem {
  aggregate_id: string | null;
  candidate_kind:
    | "coverage_rule"
    | "policy_contract"
    | "policy_party"
    | "rider"
    | "rider_clause";
  candidate_version_id: string;
  evidence: Array<CandidateEvidenceRef>;
  expected_version: number;
  fields: Array<CandidateField>;
  issues: Array<ReviewIssue>;
  review_item_id: string;
  status: "AI_VERIFIED" | "NEEDS_REVIEW" | "USER_CONFIRMED" | "rejected";
}

export interface PolicyUpdateRequest {
  coverage_end_date?: string | null;
  expected_version: number;
  status?: "active" | "inactive" | "expired" | "cancelled" | "unknown" | null;
  status_evidence_id?: string | null;
}

export type PositiveVersion = number;

export interface ReviewIssue {
  code:
    | "COMMON_SPECIAL_TERMS_CONFLICT"
    | "CONFLICTING_EVIDENCE"
    | "INVALID_DATE"
    | "INVALID_UNIT"
    | "LOW_CONFIDENCE"
    | "MISSING_EVIDENCE"
    | "STALE_EVIDENCE"
    | "TERMS_ONLY_RIDER"
    | "UNSUPPORTED_DSL"
    | "UNSUPPORTED_STRUCTURE"
    | "WRONG_EDITION";
  field_id:
    | "benefit_type"
    | "clause_id"
    | "contract_end"
    | "contract_start"
    | "coverage_end"
    | "coverage_start"
    | "currency"
    | "date_boundary"
    | "decimal_boundary"
    | "fact_field"
    | "insurer"
    | "link_review_state"
    | "policy_status"
    | "product_name"
    | "renewable"
    | "required"
    | "rider_id"
    | "rider_key"
    | "rider_name"
    | "rider_status"
    | "rule_kind"
    | "rule_operator"
    | "sum_assured"
    | "terms_edition_id"
    | "unit"
    | null;
}

export type ReviewItemId = string;

export interface RiderClauseLinkRejectionRequest {
  expected_version: number;
  reason_code:
    "USER_REJECTED" | "WRONG_CLAUSE" | "WRONG_EDITION" | "NOT_APPLICABLE";
}

export interface RiderClauseLinkResponse {
  applicability_reason_code: string;
  clause_id: string;
  clause_label?: string | null;
  evidence: Array<ClauseEvidenceResponse>;
  link_id: string;
  review_state: "AI_VERIFIED" | "NEEDS_REVIEW" | "USER_CONFIRMED" | "rejected";
  rider_id: string;
  rider_label?: string | null;
  terms_edition_id: string;
  version: number;
}

export interface RiderResponse {
  benefit_type: "fixed" | "indemnity";
  coverage_end_date: string | null;
  coverage_start_date: string | null;
  currency: string | null;
  display_name: string;
  id: string;
  insured_amount: string | null;
  normalized_key: string;
  policy_contract_id: string;
  renewable: boolean | null;
  source_evidence: EvidenceResponse;
  status: "active" | "inactive" | "expired" | "cancelled" | "unknown";
  status_evidence: EvidenceResponse | null;
  version: number;
}

export interface TermsEditionResponse {
  applicability_end: string | null;
  applicability_start: string | null;
  content_sha256: string;
  document_version_id: string;
  id: string;
  insurer_display: string;
  insurer_key: string;
  normalization_version: string;
  product_display: string;
  product_key: string;
  version: number;
}
