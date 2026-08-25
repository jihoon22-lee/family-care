import { ApiError, safeErrorCode } from "./errors";
import {
  authHeaders,
  clearAuthState,
  loadCsrfToken,
} from "../features/identity/authApi";

export type ApiRequestInit = RequestInit & { csrfToken?: string };

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

function requestHeaders(init: ApiRequestInit): Record<string, string> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const method = (init.method ?? "GET").toUpperCase();
  if (!SAFE_METHODS.has(method)) {
    Object.assign(headers, authHeaders());
  }
  if (init.headers) {
    new Headers(init.headers).forEach((value, key) => {
      headers[key] = value;
    });
  }
  if (typeof init.body === "string" && !("Content-Type" in headers)) {
    headers["Content-Type"] = "application/json";
  }
  if (init.csrfToken) {
    headers["X-CSRF-Token"] = init.csrfToken;
  }
  return headers;
}

async function responseError(response: Response): Promise<ApiError> {
  let errorCode: unknown;
  if (response.headers.get("content-type")?.includes("application/json")) {
    try {
      const body: unknown = await response.json();
      if (typeof body === "object" && body !== null && "error_code" in body) {
        errorCode = (body as { error_code?: unknown }).error_code;
      }
    } catch {
      errorCode = undefined;
    }
  }
  const error = new ApiError(
    safeErrorCode(errorCode, response.status),
    response.status,
  );
  if (response.status === 401) clearAuthState();
  return error;
}

export async function apiRequest<T>(
  path: string,
  init: ApiRequestInit = {},
): Promise<T> {
  if (!path.startsWith("/api/v1/")) {
    throw new ApiError("INVALID_REQUEST", 400);
  }
  const { csrfToken: _csrfToken, ...requestInit } = init;
  void _csrfToken;
  let response: Response;
  try {
    response = await fetch(path, {
      ...requestInit,
      cache: "no-store",
      credentials: "include",
      headers: requestHeaders(init),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    void error;
    throw new ApiError("NETWORK_ERROR", 0);
  }
  if (!response.ok) {
    const error = await responseError(response);
    const method = (init.method ?? "GET").toUpperCase();
    if (
      error.code === "CSRF_REQUIRED" &&
      !SAFE_METHODS.has(method) &&
      init.csrfToken === undefined
    ) {
      const refreshedToken = await loadCsrfToken();
      return apiRequest<T>(path, { ...init, csrfToken: refreshedToken });
    }
    throw error;
  }
  if (response.status === 204) return undefined as T;
  if (!response.headers.get("content-type")?.includes("application/json")) {
    throw new ApiError("INVALID_RESPONSE", 502);
  }
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError("INVALID_RESPONSE", 502);
  }
}
