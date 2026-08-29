import { afterEach, describe, expect, it, vi } from "vitest";

import {
  attachInsuranceDocumentSetItem,
  createInsuranceDocumentComponent,
  detachInsuranceDocumentSetItem,
} from "./insurance-document-inventory";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    headers: {
      "cache-control": "no-store",
      "content-type": "application/json",
    },
    status,
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("insurance document inventory mutations", () => {
  it("creates a user-confirmed page-range component with the generated request contract", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        {
          document_batch_item_id: "synthetic-batch-item-001",
          id: "synthetic-component-001",
          page_end: 7,
          page_start: 2,
          review_state: "USER_CONFIRMED",
          role: "terms",
          version: 1,
        },
        201,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await createInsuranceDocumentComponent("synthetic-member-a", {
      document_batch_item_id: "synthetic-batch-item-001",
      page_end: 7,
      page_start: 2,
      review_state: "USER_CONFIRMED",
      role: "terms",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/family-members/synthetic-member-a/insurance-document-components",
      expect.objectContaining({
        body: JSON.stringify({
          document_batch_item_id: "synthetic-batch-item-001",
          page_end: 7,
          page_start: 2,
          review_state: "USER_CONFIRMED",
          role: "terms",
        }),
        cache: "no-store",
        credentials: "include",
        method: "POST",
      }),
    );
  });

  it("posts USER_CONFIRMED with the generated set-item request contract", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        {
          id: "synthetic-item-001",
          insurance_document_component_id: "synthetic-component-001",
          insurance_document_set_id: "synthetic-set-001",
          match_state: "USER_CONFIRMED",
          role: "terms",
          version: 2,
        },
        201,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await attachInsuranceDocumentSetItem("synthetic-set-001", {
      expected_set_version: 7,
      insurance_document_component_id: "synthetic-component-001",
      match_state: "USER_CONFIRMED",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/insurance-document-sets/synthetic-set-001/items",
      expect.objectContaining({
        body: JSON.stringify({
          expected_set_version: 7,
          insurance_document_component_id: "synthetic-component-001",
          match_state: "USER_CONFIRMED",
        }),
        cache: "no-store",
        credentials: "include",
        method: "POST",
      }),
    );
  });

  it("deletes a set item with an expected version and inherits apiRequest auth semantics", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await detachInsuranceDocumentSetItem("synthetic-item-001", {
      expected_version: 4,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/insurance-document-set-items/synthetic-item-001",
      expect.objectContaining({
        body: JSON.stringify({ expected_version: 4 }),
        cache: "no-store",
        credentials: "include",
        method: "DELETE",
      }),
    );
  });
});
