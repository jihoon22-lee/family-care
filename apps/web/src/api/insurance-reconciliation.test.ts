import { afterEach, describe, expect, it, vi } from "vitest";

import type { MemberInsuranceReconciliationResponse } from "./generated";
import {
  confirmDocumentResolution,
  confirmOperationalLink,
  getInsuranceReconciliation,
} from "./insurance-reconciliation";

const MEMBER_ID = "synthetic-member-a";
const CONTRACT_ID = "synthetic-contract-001";
const POLICY_ID = "synthetic-policy-001";
const ITEM_ID = "synthetic-item-001";

const RECONCILIATION: MemberInsuranceReconciliationResponse = {
  contracts: [],
  generated_at: "2026-09-01T01:02:03Z",
  knowledge_run_id: "synthetic-run-001",
  member_id: MEMBER_ID,
  orphan_operational_contracts: [],
  schema_version: "1",
  summary: {
    conflict_contracts: 0,
    documents_pending_contracts: 0,
    evidence_ready_contracts: 0,
    link_review_required_contracts: 0,
    orphan_operational_contracts: 0,
    total_contracts: 0,
    unresolved_unreadable_sources: 1,
  },
  unresolved_sources: [
    {
      current_resolution_id: null,
      display_label: "보험증권 문서",
      document_batch_item_id: ITEM_ID,
      processing_state: "PASSWORD_REQUIRED",
      source_kind: "policy",
    },
  ],
};

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
    status,
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("insurance reconciliation API", () => {
  it("loads the member-scoped no-store projection and rejects malformed success", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(RECONCILIATION));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getInsuranceReconciliation(MEMBER_ID)).resolves.toEqual(
      RECONCILIATION,
    );
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/family-members/${MEMBER_ID}/insurance-reconciliation`,
      expect.objectContaining({ cache: "no-store", credentials: "include" }),
    );

    fetchMock.mockResolvedValueOnce(jsonResponse({ schema_version: "1" }));
    await expect(getInsuranceReconciliation(MEMBER_ID)).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
      status: 502,
    });
  });

  it("confirms a manual identity link with the exact expected current ID", async () => {
    const response = {
      authority: "USER_CONFIRMED_OPERATIONAL_IDENTITY",
      confirmed_at: "2026-09-01T01:02:03Z",
      conflict: false,
      decision: "MATCH",
      id: "synthetic-link-001",
      knowledge_contract_id: CONTRACT_ID,
      policy_contract_id: POLICY_ID,
      reason_code: "USER_CONFIRMED_SAME_CONTRACT",
      schema_version: "1",
    } as const;
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(response));
    vi.stubGlobal("fetch", fetchMock);

    await confirmOperationalLink(CONTRACT_ID, {
      conflict: false,
      decision: "MATCH",
      expected_current_link_id: null,
      policy_contract_id: POLICY_ID,
      reason_code: "USER_CONFIRMED_SAME_CONTRACT",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/private-knowledge/current/contracts/${CONTRACT_ID}/operational-link`,
      expect.objectContaining({
        body: JSON.stringify({
          conflict: false,
          decision: "MATCH",
          expected_current_link_id: null,
          policy_contract_id: POLICY_ID,
          reason_code: "USER_CONFIRMED_SAME_CONTRACT",
        }),
        cache: "no-store",
        credentials: "include",
        method: "POST",
      }),
    );
  });

  it("dismisses a document task with its current resolution ID", async () => {
    const response = {
      authority: "USER_CONFIRMED_DOCUMENT_RESOLUTION",
      confirmed_at: "2026-09-01T01:02:03Z",
      failed_item_id: ITEM_ID,
      id: "synthetic-resolution-002",
      reason_code: "USER_DISMISSED_STALE_FAILURE",
      replacement_item_id: null,
      resolution: "DISMISSED",
      schema_version: "1",
    } as const;
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(response));
    vi.stubGlobal("fetch", fetchMock);

    await confirmDocumentResolution(ITEM_ID, {
      expected_current_resolution_id: "synthetic-resolution-001",
      reason_code: "USER_DISMISSED_STALE_FAILURE",
      replacement_item_id: null,
      resolution: "DISMISSED",
    });

    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      expected_current_resolution_id: "synthetic-resolution-001",
      reason_code: "USER_DISMISSED_STALE_FAILURE",
      replacement_item_id: null,
      resolution: "DISMISSED",
    });
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `/api/v1/document-batch-items/${ITEM_ID}/resolution`,
    );
  });
});
