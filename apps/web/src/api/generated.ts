// GENERATED FILE: do not edit; source packages/contracts/openapi/familycare.v1.json
// Candidate schemas remain available before Task 3 registers their API routes.

export const API_PATHS = [
  "/api/v1/analysis-jobs/{job_id}",
  "/api/v1/auth/csrf",
  "/api/v1/auth/login",
  "/api/v1/auth/logout",
  "/api/v1/auth/me",
  "/api/v1/auth/password",
  "/api/v1/auth/reauthenticate",
  "/api/v1/auth/sessions",
  "/api/v1/auth/sessions/{session_id}/revoke",
  "/api/v1/claims",
  "/api/v1/claims/trash",
  "/api/v1/claims/{claim_id}",
  "/api/v1/claims/{claim_id}/checklist/{item_id}",
  "/api/v1/claims/{claim_id}/restore",
  "/api/v1/claims/{claim_id}/transitions",
  "/api/v1/clauses/search",
  "/api/v1/coverage-rules/{rule_id}/publish",
  "/api/v1/coverage-rules/{rule_id}/versions",
  "/api/v1/document-batches",
  "/api/v1/document-batches/{batch_id}",
  "/api/v1/document-batches/{batch_id}/cancel",
  "/api/v1/document-batches/{batch_id}/password",
  "/api/v1/document-import-sources",
  "/api/v1/documents/analysis",
  "/api/v1/evidence/{evidence_id}",
  "/api/v1/family-members",
  "/api/v1/family-members/trash",
  "/api/v1/family-members/{member_id}",
  "/api/v1/family-members/{member_id}/insurance-document-components",
  "/api/v1/family-members/{member_id}/insurance-document-inventory",
  "/api/v1/family-members/{member_id}/insurance-document-sets",
  "/api/v1/family-members/{member_id}/restore",
  "/api/v1/insurance-document-set-items/{item_id}",
  "/api/v1/insurance-document-sets/{document_set_id}",
  "/api/v1/insurance-document-sets/{document_set_id}/items",
  "/api/v1/medical-event-structuring-jobs/{job_id}",
  "/api/v1/medical-events",
  "/api/v1/medical-events/trash",
  "/api/v1/medical-events/{event_id}",
  "/api/v1/medical-events/{event_id}/analyze",
  "/api/v1/medical-events/{event_id}/calculations",
  "/api/v1/medical-events/{event_id}/claims",
  "/api/v1/medical-events/{event_id}/receipt-lines",
  "/api/v1/medical-events/{event_id}/receipt-lines/{line_id}",
  "/api/v1/medical-events/{event_id}/restore",
  "/api/v1/medical-events/{event_id}/results/{version}",
  "/api/v1/medical-events/{event_id}/structure",
  "/api/v1/policies",
  "/api/v1/policies/trash",
  "/api/v1/policies/{policy_id}",
  "/api/v1/policies/{policy_id}/candidate-fields/{field_id}",
  "/api/v1/policies/{policy_id}/restore",
  "/api/v1/policies/{policy_id}/riders",
  "/api/v1/private-knowledge/current",
  "/api/v1/private-knowledge/current/contracts",
  "/api/v1/private-knowledge/current/contracts/{contract_id}",
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
    method: "GET",
    path: "/api/v1/auth/csrf",
    operationId: "issue_csrf_api_v1_auth_csrf_get",
  },
  {
    method: "POST",
    path: "/api/v1/auth/login",
    operationId: "login_api_v1_auth_login_post",
  },
  {
    method: "POST",
    path: "/api/v1/auth/logout",
    operationId: "logout_api_v1_auth_logout_post",
  },
  {
    method: "GET",
    path: "/api/v1/auth/me",
    operationId: "current_user_api_v1_auth_me_get",
  },
  {
    method: "POST",
    path: "/api/v1/auth/password",
    operationId: "change_password_api_v1_auth_password_post",
  },
  {
    method: "POST",
    path: "/api/v1/auth/reauthenticate",
    operationId: "reauthenticate_api_v1_auth_reauthenticate_post",
  },
  {
    method: "GET",
    path: "/api/v1/auth/sessions",
    operationId: "list_sessions_api_v1_auth_sessions_get",
  },
  {
    method: "POST",
    path: "/api/v1/auth/sessions/{session_id}/revoke",
    operationId: "revoke_session_api_v1_auth_sessions__session_id__revoke_post",
  },
  {
    method: "GET",
    path: "/api/v1/claims",
    operationId: "list_claim_cases_api_v1_claims_get",
  },
  {
    method: "GET",
    path: "/api/v1/claims/trash",
    operationId: "list_deleted_claim_cases_api_v1_claims_trash_get",
  },
  {
    method: "DELETE",
    path: "/api/v1/claims/{claim_id}",
    operationId: "delete_claim_case_api_v1_claims__claim_id__delete",
  },
  {
    method: "GET",
    path: "/api/v1/claims/{claim_id}",
    operationId: "get_claim_case_api_v1_claims__claim_id__get",
  },
  {
    method: "PATCH",
    path: "/api/v1/claims/{claim_id}",
    operationId: "update_claim_case_api_v1_claims__claim_id__patch",
  },
  {
    method: "PATCH",
    path: "/api/v1/claims/{claim_id}/checklist/{item_id}",
    operationId:
      "update_claim_checklist_api_v1_claims__claim_id__checklist__item_id__patch",
  },
  {
    method: "POST",
    path: "/api/v1/claims/{claim_id}/restore",
    operationId: "restore_claim_case_api_v1_claims__claim_id__restore_post",
  },
  {
    method: "POST",
    path: "/api/v1/claims/{claim_id}/transitions",
    operationId:
      "transition_claim_case_api_v1_claims__claim_id__transitions_post",
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
    path: "/api/v1/document-batches",
    operationId: "create_batch_api_v1_document_batches_post",
  },
  {
    method: "GET",
    path: "/api/v1/document-batches/{batch_id}",
    operationId: "get_batch_api_v1_document_batches__batch_id__get",
  },
  {
    method: "POST",
    path: "/api/v1/document-batches/{batch_id}/cancel",
    operationId: "cancel_batch_api_v1_document_batches__batch_id__cancel_post",
  },
  {
    method: "POST",
    path: "/api/v1/document-batches/{batch_id}/password",
    operationId:
      "handoff_password_api_v1_document_batches__batch_id__password_post",
  },
  {
    method: "GET",
    path: "/api/v1/document-import-sources",
    operationId: "list_import_sources_api_v1_document_import_sources_get",
  },
  {
    method: "POST",
    path: "/api/v1/documents/analysis",
    operationId: "submit_document_analysis_api_v1_documents_analysis_post",
  },
  {
    method: "GET",
    path: "/api/v1/evidence/{evidence_id}",
    operationId: "get_evidence_api_v1_evidence__evidence_id__get",
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
    path: "/api/v1/family-members/{member_id}/insurance-document-components",
    operationId:
      "create_insurance_document_component_api_v1_family_members__member_id__insurance_document_components_post",
  },
  {
    method: "GET",
    path: "/api/v1/family-members/{member_id}/insurance-document-inventory",
    operationId:
      "get_member_insurance_document_inventory_api_v1_family_members__member_id__insurance_document_inventory_get",
  },
  {
    method: "POST",
    path: "/api/v1/family-members/{member_id}/insurance-document-sets",
    operationId:
      "create_insurance_document_set_api_v1_family_members__member_id__insurance_document_sets_post",
  },
  {
    method: "POST",
    path: "/api/v1/family-members/{member_id}/restore",
    operationId:
      "restore_family_member_api_v1_family_members__member_id__restore_post",
  },
  {
    method: "DELETE",
    path: "/api/v1/insurance-document-set-items/{item_id}",
    operationId:
      "detach_insurance_document_set_item_api_v1_insurance_document_set_items__item_id__delete",
  },
  {
    method: "DELETE",
    path: "/api/v1/insurance-document-sets/{document_set_id}",
    operationId:
      "delete_insurance_document_set_api_v1_insurance_document_sets__document_set_id__delete",
  },
  {
    method: "POST",
    path: "/api/v1/insurance-document-sets/{document_set_id}/items",
    operationId:
      "attach_insurance_document_set_item_api_v1_insurance_document_sets__document_set_id__items_post",
  },
  {
    method: "GET",
    path: "/api/v1/medical-event-structuring-jobs/{job_id}",
    operationId:
      "get_structuring_job_api_v1_medical_event_structuring_jobs__job_id__get",
  },
  {
    method: "POST",
    path: "/api/v1/medical-events",
    operationId: "create_medical_event_api_v1_medical_events_post",
  },
  {
    method: "GET",
    path: "/api/v1/medical-events/trash",
    operationId: "list_deleted_medical_events_api_v1_medical_events_trash_get",
  },
  {
    method: "DELETE",
    path: "/api/v1/medical-events/{event_id}",
    operationId: "delete_medical_event_api_v1_medical_events__event_id__delete",
  },
  {
    method: "GET",
    path: "/api/v1/medical-events/{event_id}",
    operationId: "get_medical_event_api_v1_medical_events__event_id__get",
  },
  {
    method: "PATCH",
    path: "/api/v1/medical-events/{event_id}",
    operationId: "update_medical_event_api_v1_medical_events__event_id__patch",
  },
  {
    method: "POST",
    path: "/api/v1/medical-events/{event_id}/analyze",
    operationId:
      "analyze_medical_event_api_v1_medical_events__event_id__analyze_post",
  },
  {
    method: "GET",
    path: "/api/v1/medical-events/{event_id}/calculations",
    operationId:
      "get_benefit_calculations_api_v1_medical_events__event_id__calculations_get",
  },
  {
    method: "POST",
    path: "/api/v1/medical-events/{event_id}/claims",
    operationId:
      "create_claim_case_api_v1_medical_events__event_id__claims_post",
  },
  {
    method: "GET",
    path: "/api/v1/medical-events/{event_id}/receipt-lines",
    operationId:
      "list_receipt_lines_api_v1_medical_events__event_id__receipt_lines_get",
  },
  {
    method: "POST",
    path: "/api/v1/medical-events/{event_id}/receipt-lines",
    operationId:
      "create_receipt_line_api_v1_medical_events__event_id__receipt_lines_post",
  },
  {
    method: "DELETE",
    path: "/api/v1/medical-events/{event_id}/receipt-lines/{line_id}",
    operationId:
      "delete_receipt_line_api_v1_medical_events__event_id__receipt_lines__line_id__delete",
  },
  {
    method: "PATCH",
    path: "/api/v1/medical-events/{event_id}/receipt-lines/{line_id}",
    operationId:
      "update_receipt_line_api_v1_medical_events__event_id__receipt_lines__line_id__patch",
  },
  {
    method: "POST",
    path: "/api/v1/medical-events/{event_id}/restore",
    operationId:
      "restore_medical_event_api_v1_medical_events__event_id__restore_post",
  },
  {
    method: "GET",
    path: "/api/v1/medical-events/{event_id}/results/{version}",
    operationId:
      "get_decision_result_api_v1_medical_events__event_id__results__version__get",
  },
  {
    method: "POST",
    path: "/api/v1/medical-events/{event_id}/structure",
    operationId:
      "structure_medical_event_api_v1_medical_events__event_id__structure_post",
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
    path: "/api/v1/private-knowledge/current",
    operationId:
      "get_current_private_knowledge_api_v1_private_knowledge_current_get",
  },
  {
    method: "GET",
    path: "/api/v1/private-knowledge/current/contracts",
    operationId:
      "list_current_private_knowledge_contracts_api_v1_private_knowledge_current_contracts_get",
  },
  {
    method: "GET",
    path: "/api/v1/private-knowledge/current/contracts/{contract_id}",
    operationId:
      "get_current_private_knowledge_contract_api_v1_private_knowledge_current_contracts__contract_id__get",
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

export type AuthErrorResponseErrorCode =
  | "AUTHENTICATION_REQUIRED"
  | "AUTH_FAILED"
  | "AUTH_RATE_LIMITED"
  | "AUTH_STORE_UNAVAILABLE"
  | "CSRF_REQUIRED"
  | "INVALID_REQUEST"
  | "ORIGIN_REQUIRED"
  | "REAUTHENTICATION_REQUIRED"
  | "SESSION_NOT_FOUND";

export type CandidateErrorResponseErrorCode =
  "INVALID_CANDIDATE_CORRECTION" | "REVIEW_ITEM_NOT_FOUND" | "VERSION_CONFLICT";

export type ClaimErrorResponseErrorCode =
  | "AUTHENTICATION_REQUIRED"
  | "CLAIM_CHECKLIST_ITEM_NOT_FOUND"
  | "CLAIM_INVALID"
  | "CLAIM_NOT_FOUND"
  | "INVALID_CLAIM_TRANSITION"
  | "INVALID_REQUEST"
  | "RESOURCE_LIMIT_EXCEEDED"
  | "VERSION_CONFLICT";

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

export interface AnalysisAssistanceResponse {
  mode: "STRUCTURED_SEARCH" | "LLM_ASSISTED" | "NONE";
  model_label: string | null;
  outcome_code: string;
  recommendations: Array<AnalysisRecommendationResponse>;
  state: "SEARCH_READY" | "LLM_PENDING" | "LLM_READY";
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

export interface AnalysisRecommendationResponse {
  citation: AssistanceCitationResponse;
  clause_label: string;
  contract_label: string;
  coverage_label: string;
  excerpt: string;
  explanation_code: string | null;
  question_code: string | null;
  rank: number;
  reason_code: string;
  recommendation_id: string;
}

export interface AssistanceCitationResponse {
  fact_id: string;
  kind: "FACT_CITATION";
  page_end: number;
  page_start: number;
  source_clause_id: string;
  terms_section_id: string;
}

export interface AuthErrorResponse {
  error_code:
    | "AUTHENTICATION_REQUIRED"
    | "AUTH_FAILED"
    | "AUTH_RATE_LIMITED"
    | "AUTH_STORE_UNAVAILABLE"
    | "CSRF_REQUIRED"
    | "INVALID_REQUEST"
    | "ORIGIN_REQUIRED"
    | "REAUTHENTICATION_REQUIRED"
    | "SESSION_NOT_FOUND";
  fields?: Array<string> | null;
  message: string;
}

export interface AuthSessionResponse {
  created_at: string;
  current: boolean;
  device_label: string;
  expires_at: string;
  last_seen_at: string;
  session_id: string;
}

export interface AuthUserResponse {
  display_name: string;
  needs_reauthentication: boolean;
  user_id: string;
  username: string;
}

export interface BatchCreateRequest {
  family_member_id: string;
  schema_version: "1";
  sources: Array<BatchSourceRequest>;
}

export interface BatchItemResponse {
  attempts: number;
  display_label: string;
  document_kind:
    "application" | "policy" | "product_explanation" | "supporting" | "terms";
  error_code:
    | "ARCHIVE_INTEGRITY_ERROR"
    | "ARCHIVE_KEY_UNAVAILABLE"
    | "ARCHIVE_WRITE_FAILED"
    | "DOCUMENT_NOT_FOUND"
    | "DOCUMENT_PATH_ESCAPE"
    | "DOCUMENT_TOO_LARGE"
    | "EXTRACTION_TIMEOUT"
    | "INVALID_REQUEST"
    | "OCR_FAILED"
    | "OCR_OUTPUT_LIMIT_EXCEEDED"
    | "OCR_TIMEOUT"
    | "OCR_UNAVAILABLE"
    | "PAGE_LIMIT_EXCEEDED"
    | "PASSWORD_INVALID"
    | "PASSWORD_REQUIRED"
    | "PDF_CORRUPT"
    | "RESOURCE_LIMIT_EXCEEDED"
    | "SOURCE_CHANGED"
    | "TEMP_CLEANUP_FAILED"
    | "UNSUPPORTED_FILE_TYPE"
    | null;
  ocr_pages_processed: number;
  ocr_state:
    "completed" | "failed" | "native_only" | "pending" | "running" | "warning";
  ocr_warning_codes: Array<"LOW_CONFIDENCE" | "NO_TEXT_DETECTED">;
  source_id: string;
  state:
    | "cancelled"
    | "password_required"
    | "permanently_failed"
    | "queued"
    | "retryable_failed"
    | "running"
    | "succeeded";
}

export interface BatchResponse {
  batch_id: string;
  family_member_id: string;
  items: Array<BatchItemResponse>;
  schema_version: "1";
  state:
    "cancelled" | "created" | "failed" | "partial" | "running" | "succeeded";
}

export interface BatchSourceRequest {
  document_kind:
    "application" | "policy" | "product_explanation" | "supporting" | "terms";
  source_id: string;
}

export interface BenefitCalculationResponse {
  additional: MoneyResponse | null;
  applied_limit: MoneyResponse | null;
  applied_rate: string | null;
  calculation_id: string | null;
  claim_candidate_id: string | null;
  confirmed: MoneyResponse | null;
  created_at: string | null;
  currency: string | null;
  deductible: MoneyResponse | null;
  engine_version: string;
  evidence_ids: Array<string>;
  excluded: MoneyResponse | null;
  excluded_reason_codes: Array<string>;
  hold_reason_codes: Array<string>;
  kind: "fixed" | "indemnity";
  rounding_rule: "half_up" | "half_even" | "up" | "down" | null;
  rule_version_id: string;
  schema_version: "1";
  status: "computed" | "partial" | "unknown";
  steps: Array<CalculationStepResponse>;
  version: number | null;
}

export interface BenefitCalculationsResponse {
  calculations: Array<BenefitCalculationResponse>;
  schema_version: "1";
}

export interface CalculationSnapshotResponse {
  calculation_ids?: Array<string>;
  statuses?: Array<"computed" | "partial" | "unknown">;
  versions?: Array<number>;
}

export interface CalculationStepResponse {
  input_amount: MoneyResponse | null;
  operation: string;
  output_amount: MoneyResponse | null;
  reason_code: string;
  rounding_rule: "half_up" | "half_even" | "up" | "down" | null;
  step_number: number;
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

export interface CandidateSnapshotResponse {
  aggregate_results?: Array<"MATCH" | "NO_MATCH" | "UNKNOWN">;
  candidate_ids?: Array<string>;
  rider_ids?: Array<string>;
}

export type CandidateStatus =
  "AI_VERIFIED" | "NEEDS_REVIEW" | "USER_CONFIRMED" | "rejected";

export type CandidateVersionId = string;

export interface CatalogCoverageResponse {
  benefit_coverage_count: number;
  blocked_coverage_count: number;
  contract_count: number;
  not_applicable_coverage_count: number;
  published_coverage_count: number;
}

export interface ChangePasswordRequest {
  new_password: string;
}

export interface ChecklistUpdateRequest {
  expected_version: number;
  note_code?: string | null;
  prepared: boolean;
}

export type ClaimCandidateResponse =
  OperationalCandidateResponse | PrivateKnowledgeCandidateResponse;

export interface ClaimCaseListResponse {
  items: Array<ClaimCaseResponse>;
  next_cursor?: string | null;
  schema_version?: "1";
}

export interface ClaimCaseResponse {
  allowed_transitions: Array<
    | "preparing"
    | "submitted"
    | "supplementation_requested"
    | "paid"
    | "partially_paid"
    | "denied"
    | "closed"
  >;
  checklist: Array<ClaimChecklistItemResponse>;
  claimed_amount: string | null;
  currency: string | null;
  deleted: boolean;
  family_member_id: string;
  id: string;
  insurer_key: string;
  medical_event_id: string;
  outcome_reason_code: string | null;
  paid_amount: string | null;
  policy_contract_id: string;
  receipt_number: string | null;
  rider_id: string;
  schema_version?: "1";
  snapshot: ClaimSnapshotResponse;
  status:
    | "preparing"
    | "submitted"
    | "supplementation_requested"
    | "paid"
    | "partially_paid"
    | "denied"
    | "closed";
  status_events: Array<ClaimStatusEventResponse>;
  submitted_at: string | null;
  version: number;
}

export interface ClaimChecklistItemResponse {
  conditional: boolean;
  document_kind: string;
  id: string;
  note_code: string | null;
  prepared: boolean;
  required: boolean;
  requirement_code: string;
  source_evidence_id: string | null;
  source_rule_version_id: string | null;
  version: number;
}

export interface ClaimCreateRequest {
  rider_id: string;
}

export interface ClaimErrorResponse {
  error_code:
    | "AUTHENTICATION_REQUIRED"
    | "CLAIM_CHECKLIST_ITEM_NOT_FOUND"
    | "CLAIM_INVALID"
    | "CLAIM_NOT_FOUND"
    | "INVALID_CLAIM_TRANSITION"
    | "INVALID_REQUEST"
    | "RESOURCE_LIMIT_EXCEEDED"
    | "VERSION_CONFLICT";
  fields?: Array<string> | null;
  message: string;
}

export interface ClaimSnapshotResponse {
  calculation: CalculationSnapshotResponse;
  candidate: CandidateSnapshotResponse;
  evidence: EvidenceSnapshotResponse;
  policy: PolicySnapshotResponse;
  rules: RuleSnapshotResponse;
  snapshot_sha256: string;
  snapshot_version: number;
}

export interface ClaimStatusEventResponse {
  from_status:
    | "preparing"
    | "submitted"
    | "supplementation_requested"
    | "paid"
    | "partially_paid"
    | "denied"
    | "closed"
    | null;
  occurred_at: string;
  reason_code: string | null;
  to_status:
    | "preparing"
    | "submitted"
    | "supplementation_requested"
    | "paid"
    | "partially_paid"
    | "denied"
    | "closed";
}

export interface ClaimTransitionRequest {
  expected_version: number;
  metadata?: Record<string, unknown>;
  occurred_at: string;
  target_status:
    | "preparing"
    | "submitted"
    | "supplementation_requested"
    | "paid"
    | "partially_paid"
    | "denied"
    | "closed";
}

export interface ClaimUpdateRequest {
  claimed_amount?: string | null;
  currency?: string | null;
  expected_version: number;
  outcome_reason_code?: string | null;
  receipt_number?: string | null;
}

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

export interface ComponentCreateRequest {
  document_batch_item_id: string;
  evidence_id?: string | null;
  page_end: number;
  page_start: number;
  review_state?: "SUGGESTED" | "USER_CONFIRMED" | "CONFLICT" | "REJECTED";
  role:
    "policy" | "terms" | "product_explanation" | "application" | "supporting";
}

export interface ConditionalFixedSubtotalResponse {
  amount: string;
  calculated_candidate_count: number;
  currency: string;
  unresolved_candidate_count: number;
}

export interface CoverageDecisionResponse {
  analysis_completeness: "COMPLETE" | "PARTIAL" | "UNAVAILABLE";
  assistance: AnalysisAssistanceResponse;
  candidates: Array<ClaimCandidateResponse>;
  catalog_coverage: CatalogCoverageResponse;
  conditional_fixed_subtotals: Array<ConditionalFixedSubtotalResponse>;
  engine_version: string;
  evaluations: Array<RuleEvaluationResponse>;
  event_version: number;
  indemnity_summary: IndemnitySummaryResponse;
  knowledge_snapshot_version: KnowledgeSnapshotVersionResponse;
  medical_event_id: string;
  policy_snapshot_at: string;
  rule_set_version: string;
  run_id: string;
  schema_version: "2";
  source_failure_codes: Array<string>;
  stale: boolean;
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

export interface CsrfResponse {
  csrf_token: string;
}

export interface CurrentKnowledgeResponse {
  counts: KnowledgeEntityCounts;
  executable_fact_count: number;
  executable_mapping_count: number;
  run_id: string;
  schema_version: "1";
  unsafe_operational_binding_count: number;
}

export interface DecisionErrorResponse {
  error_code: string;
  message: string;
}

export interface DocumentAnalysisRequest {
  document_kind:
    | "amendment"
    | "application"
    | "claim"
    | "policy"
    | "product_explanation"
    | "supporting"
    | "terms";
  extractor_config: ExtractorConfigRequest;
  schema_version: "1";
  source_key: string;
}

export interface DocumentSetCreateRequest {
  display_label: string;
  insurer_display?: string | null;
  policy_contract_id?: string | null;
  product_display?: string | null;
}

export interface DocumentSetItemCreateRequest {
  evidence_id?: string | null;
  expected_set_version: number;
  insurance_document_component_id: string;
  match_state: "SUGGESTED" | "USER_CONFIRMED" | "CONFLICT" | "REJECTED";
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

export interface EvidenceDetailResponse {
  bbox: [number, number, number, number] | null;
  bounded_excerpt: string;
  clause_label?: string | null;
  document_label: string;
  document_version_id: string;
  evidence_id: string;
  physical_page: number;
  review_state: "AI_VERIFIED" | "NEEDS_REVIEW" | "USER_CONFIRMED";
  schema_version?: "1";
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

export interface EvidenceSnapshotResponse {
  content_sha256?: Array<string>;
  evidence_ids?: Array<string>;
}

export interface ExpectedItemVersionRequest {
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

export interface FactInput {
  confirmation: "user" | "ai_structured" | "unconfirmed" | "conflicting";
  value: string | number | null;
}

export interface FactIssueResponse {
  code:
    | "INVENTED_FIELD"
    | "INVALID_VALUE"
    | "INVALID_STATE"
    | "DUPLICATE_FIELD"
    | "INVENTED_QUESTION"
    | "INVENTED_EVIDENCE"
    | "UNSUPPORTED_SOURCE"
    | "INVALID_CONFIDENCE";
}

export interface FactResponse {
  confirmation: "user" | "ai_structured" | "unconfirmed" | "conflicting";
  value: string | number | null;
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

export interface HTTPValidationError {
  detail?: Array<ValidationError>;
}

export interface HealthResponse {
  service?: "api";
  status: "ok" | "ready" | "unavailable";
  version?: string;
}

export interface ImportSourceResponse {
  display_label: string;
  encrypted: boolean;
  size_bytes: number;
  source_id: string;
}

export interface IndemnitySummaryResponse {
  calculated_candidate_count: number;
  candidate_count: number;
  status: "NONE" | "CALCULATED" | "UNKNOWN";
  unresolved_candidate_count: number;
}

export interface InsuranceDocumentComponentResponse {
  document_batch_item_id: string;
  id: string;
  page_end: number;
  page_start: number;
  review_state: "SUGGESTED" | "USER_CONFIRMED" | "CONFLICT" | "REJECTED";
  role:
    "policy" | "terms" | "product_explanation" | "application" | "supporting";
  version: number;
}

export interface InsuranceDocumentErrorResponse {
  error_code: string;
  fields?: Array<string> | null;
  message: string;
}

export interface InsuranceDocumentSetItemMutationResponse {
  id: string;
  insurance_document_component_id: string;
  insurance_document_set_id: string;
  match_state: "SUGGESTED" | "USER_CONFIRMED" | "CONFLICT" | "REJECTED";
  role:
    "policy" | "terms" | "product_explanation" | "application" | "supporting";
  version: number;
}

export interface InsuranceDocumentSetResponse {
  display_label: string;
  id: string;
  insurer_display: string | null;
  member_id: string;
  policy_contract_id: string | null;
  product_display: string | null;
  version: number;
}

export interface InventoryComponentResponse {
  document_batch_item_id: string | null;
  duplicate_state:
    "UNIQUE" | "SAME_MEMBER_DUPLICATE" | "CROSS_MEMBER_COPY_POSSIBLE";
  id: string | null;
  page_end: number;
  page_start: number;
  processing_state:
    "READY" | "PENDING" | "PASSWORD_REQUIRED" | "OCR_REQUIRED" | "FAILED";
  review_state: "SUGGESTED" | "USER_CONFIRMED" | "CONFLICT" | "REJECTED";
  role:
    "policy" | "terms" | "product_explanation" | "application" | "supporting";
}

export interface InventorySetItemResponse {
  component: InventoryComponentResponse;
  id: string | null;
  match_state: "SUGGESTED" | "USER_CONFIRMED" | "CONFLICT" | "REJECTED";
  version: number;
}

export interface InventorySummaryResponse {
  application_documents: number;
  certificate_and_terms: number;
  certificate_backed_policies: number;
  certificate_only: number;
  pairing_conflicts: number;
  product_explanation_documents: number;
  terms_only_documents: number;
  unreadable_documents: number;
}

export interface KnowledgeBenefitCalculationResponse {
  applied_limit: string | null;
  applied_rate: string | null;
  calculation_id: string;
  calculation_publication_id: string | null;
  conditional_amount: string | null;
  confirmed_amount: string | null;
  currency: string | null;
  deductible_amount: string | null;
  excluded_amount: string | null;
  hold_reason_code: string | null;
  kind: "FIXED" | "INDEMNITY" | "UNKNOWN";
  rounding_rule: string | null;
  status: "CALCULATED" | "UNKNOWN" | "NOT_APPLICABLE" | "FAILED";
  steps: Array<KnowledgeCalculationStepResponse>;
}

export interface KnowledgeCalculationStepResponse {
  currency: string | null;
  input_amount: string | null;
  operation: string;
  output_amount: string | null;
  reason_code: string;
  rounding_rule: string | null;
  step_number: number;
}

export interface KnowledgeContractDetailResponse {
  contract: KnowledgeContractListItemResponse;
  coverage_mappings: Array<KnowledgeCoverageMappingResponse>;
  coverages: Array<KnowledgeCoverageResponse>;
  next_section_cursor: string | null;
  schema_version: "1";
  terms_assignments: Array<KnowledgeTermsAssignmentResponse>;
  terms_sections: Array<KnowledgeTermsSectionResponse>;
}

export interface KnowledgeContractListItemResponse {
  certificate_decision: "MATCH" | "NO_MATCH" | "UNKNOWN";
  contract_document_completeness:
    | "CERTIFICATE_AND_TERMS"
    | "CERTIFICATE_ONLY"
    | "CERTIFICATE_REVIEW_REQUIRED_AND_TERMS"
    | "UNVERIFIED";
  contract_end: string | null;
  contract_start: string | null;
  coverage_count: number;
  current_status: "active" | "inactive" | "lapsed" | "terminated" | "unknown";
  current_status_as_of: string | null;
  current_status_authority: "USER_CONFIRMED_CURRENT_ENROLLMENT" | null;
  current_status_decision: "MATCH" | "NO_MATCH" | "UNKNOWN";
  document_identity_decision: "MATCH" | "NO_MATCH" | "UNKNOWN";
  edition_applicability_decision: "MATCH" | "NO_MATCH" | "UNKNOWN";
  enrollment_match_count: number;
  enrollment_no_match_count: number;
  enrollment_unknown_count: number;
  family_alias: string;
  family_member_id: string | null;
  id: string;
  insurer_display: string;
  product_display: string;
  semantic_fact_count: number;
  semantic_section_count: number;
  subject_binding_decision: "MATCH" | "NO_MATCH" | "UNKNOWN";
  subject_id: string;
  terms_overall_decision: "MATCH" | "NO_MATCH" | "UNKNOWN";
  terms_source_count: number;
}

export interface KnowledgeContractPageResponse {
  items: Array<KnowledgeContractListItemResponse>;
  next_cursor: string | null;
  schema_version: "1";
}

export interface KnowledgeCoverageMappingResponse {
  coverage_id: string;
  document_identity_decision: "MATCH" | "NO_MATCH" | "UNKNOWN";
  edition_applicability_decision: "MATCH" | "NO_MATCH" | "UNKNOWN";
  enrollment_decision: "MATCH" | "NO_MATCH" | "UNKNOWN";
  executable: false;
  mapping_applicability: "APPLICABLE" | "NOT_APPLICABLE" | "UNKNOWN";
  overall_decision: "MATCH" | "NO_MATCH" | "UNKNOWN";
  reason_codes: Array<string>;
  section_mapping_decision: "MATCH" | "NO_MATCH" | "UNKNOWN";
  terms_section_id: string | null;
}

export interface KnowledgeCoverageResponse {
  benefit_type: "FIXED" | "INDEMNITY" | "UNKNOWN" | "NOT_APPLICABLE";
  component_classification:
    "BENEFIT_COVERAGE" | "NON_BENEFIT_CONTRACT_COMPONENT" | "UNKNOWN";
  component_role: "MAIN_CONTRACT" | "RIDER";
  coverage_end: string | null;
  coverage_start: string | null;
  currency?: string | null;
  current_status: "active" | "inactive" | "lapsed" | "terminated" | "unknown";
  display_name: string;
  enrollment_decision: "MATCH" | "NO_MATCH" | "UNKNOWN";
  id: string;
  insured_amount: string | null;
  renewal_state: "YES" | "NO" | "UNKNOWN" | "NOT_APPLICABLE";
}

export interface KnowledgeEntityCounts {
  contracts: number;
  coverage_terms_mappings: number;
  coverages: number;
  document_bindings: number;
  fact_citations: number;
  facts: number;
  semantic_reviews: number;
  source_clauses: number;
  subjects: number;
  terms_assignment_sources: number;
  terms_assignments: number;
  terms_sections: number;
}

export interface KnowledgeFactCitationResponse {
  clause_label?: string | null;
  clause_title?: string | null;
  page_end: number;
  page_start: number;
  source_document_ref: string;
}

export interface KnowledgeFactConditionsResponse {
  confidence: "high" | "medium";
  decision_impact: string;
  details_ko: Array<string>;
  unresolved_reference: boolean;
}

export interface KnowledgeFactResponse {
  citations: Array<KnowledgeFactCitationResponse>;
  conditions: KnowledgeFactConditionsResponse;
  executable: false;
  fact_type:
    | "PAYMENT_TRIGGER"
    | "DEFINITION"
    | "EXCLUSION"
    | "WAITING_PERIOD"
    | "REDUCTION"
    | "FREQUENCY"
    | "AMOUNT"
    | "RENEWAL"
    | "REQUIRED_DOCUMENT"
    | "TERMINATION"
    | "CROSS_REFERENCE"
    | "OTHER";
  id: string;
  numeric_terms: Array<string>;
  review_state:
    "DIRECT_REVIEWED" | "NEEDS_REVIEW" | "USER_CONFIRMED" | "UNKNOWN";
  statement: string;
}

export interface KnowledgeSnapshotVersionResponse {
  catalog_import_run_id: string | null;
  event_fact_schema_version: string;
  rule_import_run_id: string | null;
}

export interface KnowledgeTermsAssignmentResponse {
  document_identity_decision: "MATCH" | "NO_MATCH" | "UNKNOWN";
  edition_applicability_decision: "MATCH" | "NO_MATCH" | "UNKNOWN";
  id: string;
  overall_decision: "MATCH" | "NO_MATCH" | "UNKNOWN";
  reason_codes: Array<string>;
  selected_source_count: number;
}

export interface KnowledgeTermsSectionResponse {
  confidence: "high" | "medium";
  facts: Array<KnowledgeFactResponse>;
  found_categories: Array<string>;
  heading: string;
  id: string;
  missing_categories: Array<string>;
  page_end: number;
  page_start: number;
  review_state:
    "DIRECT_REVIEWED" | "NEEDS_REVIEW" | "USER_CONFIRMED" | "UNKNOWN";
  section_summary: string;
  warnings: Array<string>;
}

export interface LoginRequest {
  device_label: string;
  password: string;
  username: string;
}

export interface LoginResponse {
  csrf_token: string;
  expires_at: string;
  user: AuthUserResponse;
}

export interface MedicalEventCreateRequest {
  event_date?: string | null;
  facts?: Record<string, unknown>;
  family_member_id: string;
  mode: "pre_visit" | "post_treatment";
  situation: string;
  visit_date?: string | null;
}

export interface MedicalEventResponse {
  deleted: boolean;
  event_date: string | null;
  facts: Record<string, unknown>;
  family_member_id: string;
  id: string;
  mode: "pre_visit" | "post_treatment";
  optional_questions?: Array<OptionalQuestionResponse>;
  situation: string;
  structured_facts?: Array<StructuredFactResponse>;
  version: number;
  visit_date: string | null;
}

export interface MedicalEventUpdateRequest {
  event_date?: string | null;
  expected_version: number;
  facts?: Record<string, unknown> | null;
  mode?: "pre_visit" | "post_treatment" | null;
  situation?: string | null;
  structured_facts?: Array<StructuredFactInput> | null;
  visit_date?: string | null;
}

export interface MemberInsuranceDocumentInventoryResponse {
  member_id: string;
  registered_policies: Array<RegisteredPolicyInventoryResponse>;
  schema_version: "1";
  summary: InventorySummaryResponse;
  unpaired_components: Array<InventoryComponentResponse>;
  unreadable_sources: Array<UnreadableSourceResponse>;
  unregistered_document_sets: Array<UnregisteredDocumentSetResponse>;
}

export interface MoneyResponse {
  amount: string;
  currency: string;
}

export interface OperationalCandidateResponse {
  aggregate_result: "MATCH" | "NO_MATCH" | "UNKNOWN";
  benefit_kind: "FIXED" | "INDEMNITY" | "UNKNOWN";
  calculation: null;
  candidate_id: string;
  claim_start_ready: boolean;
  contract_label: string;
  coverage_label: string;
  hold_reason_codes: Array<string>;
  questions: Array<QuestionResponse>;
  required_match_count: number;
  required_no_match_count: number;
  required_unknown_count: number;
  source: OperationalCandidateSourceResponse;
}

export interface OperationalCandidateSourceResponse {
  kind: "OPERATIONAL_RIDER";
  rider_id: string;
}

export interface OperationalEvaluationResponse {
  citations: Array<OperationalEvidenceCitationResponse>;
  conflicting_fields: Array<string>;
  engine_version: string;
  evaluation_id: string;
  fact_paths: Array<string>;
  missing_fields: Array<string>;
  reason_code: string;
  required: boolean;
  result: "MATCH" | "NO_MATCH" | "UNKNOWN";
  source: OperationalEvaluationSourceResponse;
}

export interface OperationalEvaluationSourceResponse {
  kind: "OPERATIONAL_RIDER";
  rider_id: string;
  rule_version_id: string;
}

export interface OperationalEvidenceCitationResponse {
  bbox: [number, number, number, number] | null;
  content_sha256: string;
  document_version_id: string;
  evidence_id: string;
  extraction_id: string;
  kind: "OPERATIONAL_EVIDENCE";
  physical_page: number;
  review_state: "AI_VERIFIED" | "NEEDS_REVIEW" | "USER_CONFIRMED";
}

export interface OptionalQuestionResponse {
  field_id:
    | "event_date"
    | "visit_date"
    | "condition_class"
    | "diagnosis_label"
    | "treatment_kind"
    | "admission"
    | "outpatient"
    | "pharmacy"
    | "diagnosis_code"
    | "procedure_code"
    | "anatomical_site_code"
    | "pathology_code"
    | "treatment_setting"
    | "treatment_context"
    | "separately_billed_treatment";
  question_code:
    | "event_date"
    | "visit_date"
    | "condition_class"
    | "diagnosis_label"
    | "treatment_kind"
    | "admission"
    | "outpatient"
    | "pharmacy"
    | "diagnosis_code"
    | "procedure_code"
    | "anatomical_site_code"
    | "pathology_code"
    | "treatment_setting"
    | "treatment_context"
    | "separately_billed_treatment";
}

export interface PasswordRequest {
  password: string;
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

export interface PolicySnapshotResponse {
  captured_at?: string | null;
  policy_contract_id: string;
  rider_ids?: Array<string>;
  status_codes?: Array<string>;
}

export interface PolicyUpdateRequest {
  coverage_end_date?: string | null;
  expected_version: number;
  status?: "active" | "inactive" | "expired" | "cancelled" | "unknown" | null;
  status_evidence_id?: string | null;
}

export type PositiveVersion = number;

export interface PrivateKnowledgeCandidateResponse {
  aggregate_result: "MATCH" | "NO_MATCH" | "UNKNOWN";
  benefit_kind: "FIXED" | "INDEMNITY" | "UNKNOWN";
  calculation: KnowledgeBenefitCalculationResponse | null;
  candidate_id: string;
  claim_start_ready: false;
  contract_label: string;
  coverage_label: string;
  hold_reason_codes: Array<string>;
  questions: Array<QuestionResponse>;
  required_match_count: number;
  required_no_match_count: number;
  required_unknown_count: number;
  source: PrivateKnowledgeCandidateSourceResponse;
}

export interface PrivateKnowledgeCandidateSourceResponse {
  kind: "PRIVATE_KNOWLEDGE_COVERAGE";
  knowledge_contract_id: string;
  knowledge_coverage_id: string;
}

export interface PrivateKnowledgeCitationResponse {
  evidence_purpose: string;
  fact_id: string | null;
  kind: "PRIVATE_KNOWLEDGE_CITATION";
  page_end: number;
  page_start: number;
  source_clause_id: string | null;
  terms_section_id: string;
}

export interface PrivateKnowledgeErrorResponse {
  error_code: string;
  fields?: Array<string> | null;
  message: string;
}

export interface PrivateKnowledgeEvaluationResponse {
  citations: Array<PrivateKnowledgeCitationResponse>;
  conflicting_fields: Array<string>;
  engine_version: string;
  evaluation_id: string;
  fact_paths: Array<string>;
  missing_fields: Array<string>;
  reason_code: string;
  required: boolean;
  result: "MATCH" | "NO_MATCH" | "UNKNOWN";
  source: PrivateKnowledgeEvaluationSourceResponse;
}

export interface PrivateKnowledgeEvaluationSourceResponse {
  kind: "PRIVATE_KNOWLEDGE_COVERAGE";
  knowledge_coverage_id: string;
  rule_publication_id: string;
}

export interface QuestionResponse {
  field_path: string;
  reason_code: string;
}

export interface ReauthenticateRequest {
  password: string;
}

export interface ReceiptLineCreateRequest {
  amount: string;
  category: "outpatient" | "inpatient" | "pharmacy";
  confirmation_level: "user" | "ai_structured" | "unconfirmed";
  coverage_category: "covered" | "possible_excluded" | "excluded" | "unknown";
  currency: string;
  note_code?: string | null;
}

export interface ReceiptLineDeleteRequest {
  expected_version: number;
}

export interface ReceiptLineResponse {
  amount: string;
  category: "outpatient" | "inpatient" | "pharmacy";
  confirmation_level: "user" | "ai_structured" | "unconfirmed";
  coverage_category: "covered" | "possible_excluded" | "excluded" | "unknown";
  currency: string;
  deleted?: boolean;
  id: string;
  note_code?: string | null;
  version: number;
}

export interface ReceiptLineUpdateRequest {
  amount?: string | null;
  category?: "outpatient" | "inpatient" | "pharmacy" | null;
  confirmation_level?: "user" | "ai_structured" | "unconfirmed" | null;
  coverage_category?:
    "covered" | "possible_excluded" | "excluded" | "unknown" | null;
  currency?: string | null;
  expected_version: number;
  note_code?: string | null;
}

export interface ReceiptLinesResponse {
  receipt_lines: Array<ReceiptLineResponse>;
  schema_version: "1";
}

export interface RegisteredPolicyInventoryResponse {
  completeness: "CERTIFICATE_AND_TERMS" | "CERTIFICATE_ONLY";
  document_set_id: string | null;
  document_set_version: number | null;
  documents: Array<RoleDocumentSummaryResponse>;
  has_application: boolean;
  has_product_explanation: boolean;
  insurer_display: string;
  missing_document_roles: Array<
    "policy" | "terms" | "product_explanation" | "application" | "supporting"
  >;
  policy_id: string;
  product_display: string;
  rider_count: number;
  status: "active" | "inactive" | "expired" | "cancelled" | "unknown";
}

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

export interface RoleDocumentSummaryResponse {
  bundled_source: boolean;
  component_count: number;
  items: Array<InventorySetItemResponse>;
  role:
    "policy" | "terms" | "product_explanation" | "application" | "supporting";
  source_count: number;
}

export type RuleEvaluationResponse =
  OperationalEvaluationResponse | PrivateKnowledgeEvaluationResponse;

export interface RuleSnapshotResponse {
  evaluator_versions?: Array<string>;
  reason_codes?: Array<string>;
  rule_version_ids?: Array<string>;
}

export interface StructureAcceptedResponse {
  job_id: string;
  schema_version?: "1";
  state: "queued";
  status_url: string;
}

export interface StructuredFactInput {
  field_id:
    | "event_date"
    | "visit_date"
    | "condition_class"
    | "diagnosis_label"
    | "treatment_kind"
    | "admission"
    | "outpatient"
    | "pharmacy"
    | "diagnosis_code"
    | "procedure_code"
    | "anatomical_site_code"
    | "pathology_code"
    | "treatment_setting"
    | "treatment_context"
    | "separately_billed_treatment";
  value: string | boolean | null;
}

export interface StructuredFactResponse {
  confidence: "high" | "medium" | "low";
  evidence_ids: Array<string>;
  fact_id: string;
  field_id:
    | "event_date"
    | "visit_date"
    | "condition_class"
    | "diagnosis_label"
    | "treatment_kind"
    | "admission"
    | "outpatient"
    | "pharmacy"
    | "diagnosis_code"
    | "procedure_code"
    | "anatomical_site_code"
    | "pathology_code"
    | "treatment_setting"
    | "treatment_context"
    | "separately_billed_treatment";
  source: "user" | "ai" | "system";
  state: "confirmed" | "ambiguous" | "missing" | "conflict";
  value: string | boolean | null;
}

export interface StructuringJobResponse {
  attempts: number;
  error_code:
    | "STRUCTURING_AUTHENTICATION_FAILED"
    | "STRUCTURING_INVALID_RESPONSE"
    | "STRUCTURING_PROVIDER_TIMEOUT"
    | "STRUCTURING_RATE_LIMITED"
    | "STRUCTURING_UNAVAILABLE"
    | null;
  facts: Array<StructuredFactResponse>;
  issues: Array<FactIssueResponse>;
  job_id: string;
  questions: Array<OptionalQuestionResponse>;
  schema_version?: "1";
  state:
    | "queued"
    | "running"
    | "succeeded"
    | "retryable_failed"
    | "permanently_failed"
    | "cancelled";
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

export interface UnreadableSourceResponse {
  display_label: string;
  document_batch_item_id: string;
  processing_state: "PASSWORD_REQUIRED" | "OCR_REQUIRED" | "FAILED";
  source_kind:
    "policy" | "terms" | "product_explanation" | "application" | "supporting";
}

export interface UnregisteredDocumentSetResponse {
  component_count: number;
  display_label: string;
  enrollment_confirmed: boolean;
  has_application: boolean;
  has_product_explanation: boolean;
  id: string;
  insurer_display: string | null;
  items: Array<InventorySetItemResponse>;
  primary_classification:
    | "TERMS_ONLY"
    | "PRODUCT_EXPLANATION_ONLY"
    | "APPLICATION_ONLY"
    | "POLICY_UNREVIEWED"
    | "SUPPORTING_ONLY";
  product_display: string | null;
  source_count: number;
  version: number;
}

export interface ValidationError {
  ctx?: Record<string, unknown>;
  input?: unknown;
  loc: Array<string | number>;
  msg: string;
  type: string;
}

export interface familycare_api__claims__schemas__ExpectedVersionRequest {
  expected_version: number;
}

export interface familycare_api__clauses__schemas__ExpectedVersionRequest {
  expected_version: number;
}

export interface familycare_api__decisions__schemas__ExpectedVersionRequest {
  expected_version: number;
}

export interface familycare_api__policies__schemas__ExpectedVersionRequest {
  expected_version: number;
}
