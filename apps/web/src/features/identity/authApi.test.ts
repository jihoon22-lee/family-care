import { afterEach, describe, expect, it, vi } from "vitest";

import {
  authHeaders,
  changePassword,
  clearAuthState,
  listSessions,
  loadCsrfToken,
  loadCurrentUser,
  login,
  logout,
  reauthenticate,
  revokeSession,
} from "./authApi";

const CURRENT_USER = {
  display_name: "Admin A",
  needs_reauthentication: false,
  user_id: "synthetic-user-a",
  username: "admin-a",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "application/json",
    },
    status,
  });
}

describe("local authentication API boundary", () => {
  afterEach(() => {
    clearAuthState();
    vi.unstubAllGlobals();
  });

  it("logs in with cookie credentials and never writes browser storage", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(CURRENT_USER));
    vi.stubGlobal("fetch", fetchMock);

    await expect(login("admin-a", "synthetic-auth-secret-a")).resolves.toEqual(
      CURRENT_USER,
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/auth/login",
      expect.objectContaining({
        cache: "no-store",
        credentials: "include",
        method: "POST",
      }),
    );
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      device_label: "FamilyCare Web",
      password: "synthetic-auth-secret-a",
      username: "admin-a",
    });
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(document.cookie).toBe("");
  });

  it("normalizes the server user projection and keeps its CSRF token in memory", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        csrf_token: "synthetic-csrf-a",
        expires_at: "2026-09-01T00:00:00Z",
        user: {
          display_name: "Admin A",
          user_id: "synthetic-user-a",
          username: "admin-a",
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(login("admin-a", "synthetic-auth-secret-a")).resolves.toEqual(
      CURRENT_USER,
    );
    expect(authHeaders()).toEqual({ "X-CSRF-Token": "synthetic-csrf-a" });
  });

  it("keeps the CSRF token in module memory and sends it on state changes", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ csrf_token: "synthetic-csrf-a" }))
      .mockResolvedValueOnce(jsonResponse(undefined, 204));
    vi.stubGlobal("fetch", fetchMock);

    await expect(loadCsrfToken()).resolves.toBe("synthetic-csrf-a");
    expect(authHeaders()).toEqual({ "X-CSRF-Token": "synthetic-csrf-a" });
    await logout();

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/v1/auth/csrf",
      expect.objectContaining({ cache: "no-store", credentials: "include" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/v1/auth/logout",
      expect.objectContaining({
        cache: "no-store",
        credentials: "include",
        headers: expect.objectContaining({
          "X-CSRF-Token": "synthetic-csrf-a",
        }),
        method: "POST",
      }),
    );
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it("uses the narrow authenticated route set for current user and sessions", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(CURRENT_USER))
      .mockResolvedValueOnce(
        jsonResponse([
          {
            created_at: "2026-08-25T00:00:00Z",
            current: true,
            device_label: "FamilyCare Web",
            expires_at: "2026-09-01T00:00:00Z",
            last_seen_at: "2026-08-26T00:00:00Z",
            session_id: "synthetic-session-a",
          },
        ]),
      )
      .mockResolvedValueOnce(jsonResponse({ csrf_token: "synthetic-csrf-a" }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await loadCurrentUser();
    await expect(listSessions()).resolves.toEqual([
      {
        created_at: "2026-08-25T00:00:00Z",
        current: true,
        device_label: "FamilyCare Web",
        expires_at: "2026-09-01T00:00:00Z",
        last_seen_at: "2026-08-26T00:00:00Z",
        session_id: "synthetic-session-a",
      },
    ]);
    await reauthenticate("synthetic-auth-secret-a");
    await revokeSession("synthetic-session-b");
    await changePassword("synthetic-auth-secret-b");

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/auth/me",
      "/api/v1/auth/sessions",
      "/api/v1/auth/csrf",
      "/api/v1/auth/reauthenticate",
      "/api/v1/auth/sessions/synthetic-session-b/revoke",
      "/api/v1/auth/password",
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect(init).toEqual(
        expect.objectContaining({ cache: "no-store", credentials: "include" }),
      );
    }
  });

  it("does not expose response details when an auth request fails", async () => {
    const privateDetail = "synthetic-private-auth-detail";
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse(
            { error_code: "AUTHENTICATION_REQUIRED", message: privateDetail },
            401,
          ),
        ),
    );

    const error = await login("admin-a", "synthetic-auth-secret-a").catch(
      (reason: unknown) => reason,
    );

    expect(String(error)).not.toContain(privateDetail);
    expect(String(error)).not.toContain("synthetic-auth-secret-a");
  });
});
