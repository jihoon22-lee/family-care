import { apiRequest } from "./http";
import { ApiError } from "./errors";
import { clearAuthState, login } from "../features/identity/authApi";

describe("API request boundary", () => {
  afterEach(() => {
    clearAuthState();
    vi.unstubAllGlobals();
  });

  it("uses same-origin credentials and disables browser caching", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), {
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      apiRequest<{ status: string }>("/api/v1/family-members"),
    ).resolves.toEqual({
      status: "ok",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/family-members",
      expect.objectContaining({
        cache: "no-store",
        credentials: "include",
        headers: expect.objectContaining({ Accept: "application/json" }),
      }),
    );
  });

  it("attaches the in-memory session CSRF token to business writes", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            csrf_token: "synthetic-csrf-a",
            user: {
              display_name: "Admin A",
              needs_reauthentication: false,
              user_id: "synthetic-user-a",
              username: "admin-a",
            },
          }),
          { headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: "created" }), {
          headers: { "Content-Type": "application/json" },
          status: 201,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await login("admin-a", "synthetic-auth-secret-a");
    await apiRequest("/api/v1/medical-events", {
      body: JSON.stringify({ situation: "Synthetic situation" }),
      method: "POST",
    });

    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/v1/medical-events",
      expect.objectContaining({
        headers: expect.objectContaining({
          "X-CSRF-Token": "synthetic-csrf-a",
        }),
      }),
    );
  });

  it("keeps response details and request values outside public errors", async () => {
    const privateValue = "synthetic-private-response-detail";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error_code: "VERSION_CONFLICT",
            message: privateValue,
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    const error = await apiRequest("/api/v1/review-items/synthetic/confirm", {
      method: "POST",
      body: JSON.stringify({ value: privateValue }),
    }).catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ code: "VERSION_CONFLICT", status: 409 });
    expect(String(error)).not.toContain(privateValue);
  });

  it("preserves sanitized claim transition codes without response details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error_code: "INVALID_CLAIM_TRANSITION",
            message: "synthetic private transition detail",
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    const error = await apiRequest("/api/v1/claims/synthetic/transitions", {
      method: "POST",
      body: JSON.stringify({ target_status: "paid" }),
    }).catch((reason: unknown) => reason);

    expect(error).toMatchObject({
      code: "INVALID_CLAIM_TRANSITION",
      status: 409,
    });
    expect(String(error)).not.toContain("synthetic private transition detail");
  });

  it("rejects cross-origin and non-API request targets before fetch", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      apiRequest("https://example.invalid/api/v1/policies"),
    ).rejects.toMatchObject({
      code: "INVALID_REQUEST",
    });
    await expect(apiRequest("/documents/private.pdf")).rejects.toMatchObject({
      code: "INVALID_REQUEST",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
