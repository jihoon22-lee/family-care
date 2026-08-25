import { ApiError, safeErrorCode } from "../../api/errors";
import type { AuthSessionResponse } from "../../api/generated";
import { authStore, type AuthUser } from "./authStore";

export type AuthSession = AuthSessionResponse;

interface JsonObject {
  [key: string]: unknown;
}

let csrfToken: string | null = null;

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringField(value: unknown): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new ApiError("INVALID_RESPONSE", 502);
  }
  return value;
}

function booleanField(value: unknown): boolean {
  return value === true;
}

function userPayload(value: unknown): JsonObject {
  if (!isObject(value)) throw new ApiError("INVALID_RESPONSE", 502);
  const nested = value.user;
  if (isObject(nested)) return nested;
  return value;
}

function normalizeUser(value: unknown): AuthUser {
  const payload = userPayload(value);
  return {
    user_id: stringField(payload.id ?? payload.user_id),
    username: stringField(payload.username),
    display_name: stringField(payload.display_name ?? payload.displayName),
    needs_reauthentication: booleanField(
      payload.needs_reauthentication ?? payload.needsReauthentication,
    ),
  };
}

function normalizeSession(value: unknown): AuthSession {
  if (!isObject(value)) throw new ApiError("INVALID_RESPONSE", 502);
  return {
    session_id: stringField(value.id ?? value.session_id),
    device_label: stringField(value.device_label ?? value.deviceLabel),
    created_at: stringField(value.created_at ?? value.createdAt),
    last_seen_at: stringField(value.last_seen_at ?? value.lastSeenAt),
    expires_at: stringField(value.expires_at ?? value.expiresAt),
    current: booleanField(value.current ?? value.is_current),
  };
}

function requestHeaders(
  body: string | undefined,
  includeCsrf: boolean,
): Record<string, string> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (includeCsrf && csrfToken !== null) headers["X-CSRF-Token"] = csrfToken;
  return headers;
}

async function readErrorCode(response: Response): Promise<unknown> {
  if (!response.headers.get("content-type")?.includes("application/json")) {
    return undefined;
  }
  try {
    const body: unknown = await response.json();
    return isObject(body) ? body.error_code : undefined;
  } catch {
    return undefined;
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  options: { includeCsrf?: boolean } = {},
): Promise<T> {
  const body = typeof init.body === "string" ? init.body : undefined;
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      cache: "no-store",
      credentials: "include",
      headers: requestHeaders(body, options.includeCsrf ?? false),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new ApiError("NETWORK_ERROR", 0);
  }

  if (!response.ok) {
    const code = safeErrorCode(await readErrorCode(response), response.status);
    if (response.status === 401) clearAuthState();
    throw new ApiError(code, response.status);
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

function jsonBody(value: JsonObject): string {
  return JSON.stringify(value);
}

async function requestWithCsrf<T>(path: string, init: RequestInit): Promise<T> {
  if (csrfToken === null) await loadCsrfToken();
  return request<T>(path, init, { includeCsrf: true });
}

export function authHeaders(): Record<string, string> {
  return csrfToken === null ? {} : { "X-CSRF-Token": csrfToken };
}

export async function login(
  username: string,
  password: string,
): Promise<AuthUser> {
  const response = await request<unknown>(
    "/api/v1/auth/login",
    {
      body: jsonBody({
        device_label: "FamilyCare Web",
        password,
        username,
      }),
      method: "POST",
    },
    { includeCsrf: false },
  );
  if (isObject(response) && typeof response.csrf_token === "string") {
    csrfToken = response.csrf_token;
  }
  const user = normalizeUser(response);
  authStore.setAuthenticated(user);
  return user;
}

export async function logout(): Promise<void> {
  try {
    await requestWithCsrf<void>("/api/v1/auth/logout", { method: "POST" });
  } finally {
    clearAuthState();
  }
}

export async function loadCurrentUser(): Promise<AuthUser> {
  const user = normalizeUser(
    await request<unknown>("/api/v1/auth/me", { method: "GET" }),
  );
  await loadCsrfToken();
  authStore.setAuthenticated(user);
  return user;
}

export async function loadCsrfToken(): Promise<string> {
  const response = await request<unknown>("/api/v1/auth/csrf", {
    method: "GET",
  });
  if (!isObject(response) || typeof response.csrf_token !== "string") {
    throw new ApiError("INVALID_RESPONSE", 502);
  }
  csrfToken = response.csrf_token;
  return csrfToken;
}

export async function reauthenticate(password: string): Promise<void> {
  await requestWithCsrf<void>("/api/v1/auth/reauthenticate", {
    body: jsonBody({ password }),
    method: "POST",
  });
  const current = authStore.getSnapshot().user;
  if (current)
    authStore.setAuthenticated({ ...current, needs_reauthentication: false });
}

export async function changePassword(newPassword: string): Promise<void> {
  await requestWithCsrf<void>("/api/v1/auth/password", {
    body: jsonBody({ new_password: newPassword }),
    method: "POST",
  });
  clearAuthState();
}

export async function listSessions(): Promise<AuthSession[]> {
  const response = await request<unknown>("/api/v1/auth/sessions", {
    method: "GET",
  });
  const rows = isObject(response) ? response.sessions : response;
  if (!Array.isArray(rows)) throw new ApiError("INVALID_RESPONSE", 502);
  return rows.map(normalizeSession);
}

export async function revokeSession(sessionId: string): Promise<void> {
  await requestWithCsrf<void>(
    `/api/v1/auth/sessions/${encodeURIComponent(sessionId)}/revoke`,
    { method: "POST" },
  );
}

export function clearAuthState(): void {
  csrfToken = null;
  authStore.clear();
}
