import { apiRequest } from "./http";
import { ApiError } from "./errors";

describe("API request boundary", () => {
  afterEach(() => {
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
