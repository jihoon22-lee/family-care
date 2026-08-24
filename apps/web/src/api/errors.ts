export type ApiErrorCode =
  | "AUTHENTICATION_REQUIRED"
  | "INVALID_CANDIDATE_CORRECTION"
  | "INVALID_REQUEST"
  | "INVALID_RESPONSE"
  | "NETWORK_ERROR"
  | "RESOURCE_LIMIT_EXCEEDED"
  | "REVIEW_ITEM_NOT_FOUND"
  | "VERSION_CONFLICT"
  | "UNKNOWN_ERROR";

export class ApiError extends Error {
  readonly code: ApiErrorCode;
  readonly status: number;

  constructor(code: ApiErrorCode, status: number) {
    super(`FamilyCare API request failed: ${code}`);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

const KNOWN_CODES = new Set<ApiErrorCode>([
  "AUTHENTICATION_REQUIRED",
  "INVALID_CANDIDATE_CORRECTION",
  "INVALID_REQUEST",
  "INVALID_RESPONSE",
  "NETWORK_ERROR",
  "RESOURCE_LIMIT_EXCEEDED",
  "REVIEW_ITEM_NOT_FOUND",
  "VERSION_CONFLICT",
  "UNKNOWN_ERROR",
]);

export function safeErrorCode(value: unknown, status: number): ApiErrorCode {
  if (typeof value === "string" && KNOWN_CODES.has(value as ApiErrorCode)) {
    return value as ApiErrorCode;
  }
  if (status === 401) return "AUTHENTICATION_REQUIRED";
  if (status === 409) return "VERSION_CONFLICT";
  if (status === 422) return "INVALID_REQUEST";
  if (status === 503) return "RESOURCE_LIMIT_EXCEEDED";
  return "UNKNOWN_ERROR";
}
