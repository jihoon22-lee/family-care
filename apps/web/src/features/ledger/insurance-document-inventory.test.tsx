import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  InventoryComponentResponse,
  InventorySetItemResponse,
  MemberInsuranceDocumentInventoryResponse,
} from "../../api/generated";
import { getInsuranceDocumentInventory } from "../../api/insurance-document-inventory";
import { createMockApi, jsonResponse } from "../../test/mockApi";
import { renderWithProviders } from "../../test/renderWithProviders";
import { LedgerPage } from "./LedgerPage";
import { InsuranceDocumentInventory } from "./InsuranceDocumentInventory";

const MEMBER_ID = "synthetic-member-a";

function component(
  overrides: Partial<InventoryComponentResponse> = {},
): InventoryComponentResponse {
  return {
    document_batch_item_id: "synthetic-batch-item-001",
    duplicate_state: "UNIQUE",
    id: "synthetic-component-001",
    page_end: 2,
    page_start: 1,
    processing_state: "READY",
    review_state: "USER_CONFIRMED",
    role: "policy",
    ...overrides,
  };
}

function item(
  overrides: Partial<InventorySetItemResponse> = {},
): InventorySetItemResponse {
  return {
    component: component(),
    id: "synthetic-item-001",
    match_state: "USER_CONFIRMED",
    version: 1,
    ...overrides,
  };
}

const INVENTORY: MemberInsuranceDocumentInventoryResponse = {
  member_id: MEMBER_ID,
  registered_policies: [
    {
      completeness: "CERTIFICATE_ONLY",
      document_set_id: "synthetic-set-001",
      document_set_version: 2,
      documents: [
        {
          bundled_source: true,
          component_count: 2,
          items: [item()],
          role: "policy",
          source_count: 1,
        },
        {
          bundled_source: false,
          component_count: 1,
          items: [
            item({
              component: component({
                id: "synthetic-component-terms-001",
                page_end: 8,
                page_start: 3,
                review_state: "SUGGESTED",
                role: "terms",
              }),
              id: "synthetic-item-terms-001",
              match_state: "SUGGESTED",
            }),
          ],
          role: "terms",
          source_count: 1,
        },
        {
          bundled_source: false,
          component_count: 1,
          items: [
            item({
              component: component({
                id: "synthetic-component-explanation-001",
                role: "product_explanation",
              }),
              id: "synthetic-item-explanation-001",
            }),
          ],
          role: "product_explanation",
          source_count: 1,
        },
        {
          bundled_source: false,
          component_count: 1,
          items: [
            item({
              component: component({
                id: "synthetic-component-application-001",
                role: "application",
              }),
              id: "synthetic-item-application-001",
            }),
          ],
          role: "application",
          source_count: 1,
        },
      ],
      has_application: true,
      has_product_explanation: true,
      insurer_display: "Sample Insurer",
      missing_document_roles: ["terms"],
      policy_id: "synthetic-policy-001",
      product_display: "Sample Policy",
      rider_count: 2,
      status: "unknown",
    },
  ],
  schema_version: "1",
  summary: {
    application_documents: 1,
    certificate_and_terms: 0,
    certificate_backed_policies: 1,
    certificate_only: 1,
    pairing_conflicts: 1,
    product_explanation_documents: 2,
    terms_only_documents: 2,
    unreadable_documents: 1,
  },
  unpaired_components: [
    component({
      duplicate_state: "SAME_MEMBER_DUPLICATE",
      id: "synthetic-component-unpaired-001",
      page_end: 4,
      page_start: 3,
      review_state: "CONFLICT",
      role: "terms",
    }),
  ],
  unregistered_document_sets: [
    {
      component_count: 2,
      display_label: "Sample Terms Bundle",
      enrollment_confirmed: false,
      has_application: false,
      has_product_explanation: true,
      id: "synthetic-set-unregistered-001",
      insurer_display: "Sample Insurer",
      items: [
        item({
          component: component({
            id: "synthetic-component-unregistered-terms-001",
            role: "terms",
          }),
          id: "synthetic-item-unregistered-terms-001",
          match_state: "SUGGESTED",
        }),
        item({
          component: component({
            id: "synthetic-component-unregistered-explanation-001",
            role: "product_explanation",
          }),
          id: "synthetic-item-unregistered-explanation-001",
          match_state: "SUGGESTED",
        }),
      ],
      primary_classification: "TERMS_ONLY",
      product_display: "Sample Terms Product",
      source_count: 2,
      version: 1,
    },
  ],
  unreadable_sources: [
    {
      display_label: "보험증권 문서",
      document_batch_item_id: "synthetic-batch-item-unreadable-001",
      processing_state: "PASSWORD_REQUIRED",
      source_kind: "policy",
    },
  ],
};

const ATTACHABLE_INVENTORY: MemberInsuranceDocumentInventoryResponse = {
  ...INVENTORY,
  unpaired_components: [
    component({
      id: "synthetic-component-unpaired-001",
      review_state: "USER_CONFIRMED",
      role: "terms",
    }),
  ],
};

const REVIEWABLE_INVENTORY: MemberInsuranceDocumentInventoryResponse = {
  ...INVENTORY,
  unpaired_components: [
    component({
      document_batch_item_id: "synthetic-batch-item-review-001",
      id: null,
      page_end: 7,
      page_start: 1,
      review_state: "SUGGESTED",
      role: "supporting",
    }),
  ],
};

const ATTACHABLE_WITHOUT_SET_INVENTORY: MemberInsuranceDocumentInventoryResponse =
  {
    ...ATTACHABLE_INVENTORY,
    registered_policies: ATTACHABLE_INVENTORY.registered_policies.map(
      (policy) => ({
        ...policy,
        document_set_id: null,
        document_set_version: null,
      }),
    ),
  };

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/app/ledger");
});

describe("insurance document inventory", () => {
  it("renders six summary states and separates registered policy documents from unconfirmed sets", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(INVENTORY)));

    renderWithProviders(<InsuranceDocumentInventory memberId={MEMBER_ID} />);

    expect(
      await screen.findByRole("heading", { name: "앱 업로드·문서 연결 현황" }),
    ).toBeInTheDocument();
    for (const label of [
      "앱 근거 연결 보험",
      "증권+약관",
      "증권만",
      "미연결 약관",
      "상품설명서",
      "판독 필요",
    ]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(
      screen.getByRole("heading", { name: "앱 근거 연결 계약" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Sample Policy")).toBeInTheDocument();
    expect(screen.getByText("묶음 문서")).toBeInTheDocument();
    expect(screen.getByText("원본 1개 · 구간 2개")).toBeInTheDocument();
    expect(screen.getByText("약관 보완 필요")).toBeInTheDocument();
    expect(screen.getAllByText("상품설명서 있음").length).toBeGreaterThan(0);
    expect(screen.getByText("청약서 있음")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "가입 확인 안 된 문서" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("앱 계약 연결 대기").length).toBeGreaterThan(0);
    expect(screen.getByText("Sample Terms Bundle")).toBeInTheDocument();
    expect(screen.getAllByText("암호 해제 필요").length).toBeGreaterThan(0);
    expect(screen.getByText("보험증권 문서")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /누락 문서 추가/ }),
    ).toHaveAttribute("href", `/app/documents/import?member=${MEMBER_ID}`);
  });

  it("lets the user refresh a loaded inventory", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(INVENTORY));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<InsuranceDocumentInventory memberId={MEMBER_ID} />);

    await screen.findByText("Sample Policy");
    await user.click(
      screen.getByRole("button", { name: "문서 현황 새로고침" }),
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it("revalidates a loaded inventory when the window regains focus", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(INVENTORY));
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<InsuranceDocumentInventory memberId={MEMBER_ID} />);

    await screen.findByText("Sample Policy");
    window.dispatchEvent(new Event("focus"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it("keeps the ledger visible when the inventory request fails", async () => {
    const baseMock = createMockApi();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      if (url.includes("insurance-document-inventory")) {
        return Promise.resolve(
          jsonResponse({ message: "synthetic failure" }, 503),
        );
      }
      return baseMock(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<LedgerPage memberId={MEMBER_ID} />);

    expect(
      await screen.findByRole("heading", { name: "Sample Policy" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("alert", { name: /문서 현황/ }),
    ).toHaveTextContent(/문서 현황.*불러오지 못했/);
    expect(
      screen.getByRole("heading", { name: "청구 근거 연결 계약" }),
    ).toBeInTheDocument();
  });

  it("uses a path-only no-store request for a member inventory", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(INVENTORY));
    vi.stubGlobal("fetch", fetchMock);

    await getInsuranceDocumentInventory(MEMBER_ID);

    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/family-members/${MEMBER_ID}/insurance-document-inventory`,
      expect.objectContaining({ cache: "no-store", credentials: "include" }),
    );
  });

  it("rejects an invalid success payload instead of crashing the ledger", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({})));

    await expect(
      getInsuranceDocumentInventory(MEMBER_ID),
    ).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
      status: 502,
    });
  });

  it("does not expose attach controls before review confirmation", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(INVENTORY)));

    renderWithProviders(<InsuranceDocumentInventory memberId={MEMBER_ID} />);

    expect(await screen.findByText("검수 후 연결 가능")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /문서 연결$/ }),
    ).not.toBeInTheDocument();
  });

  it("reviews a successful source into a user-confirmed role and page range", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(REVIEWABLE_INVENTORY))
      .mockResolvedValueOnce(
        jsonResponse(
          {
            document_batch_item_id: "synthetic-batch-item-review-001",
            id: "synthetic-component-reviewed-001",
            page_end: 7,
            page_start: 2,
            review_state: "USER_CONFIRMED",
            role: "terms",
            version: 1,
          },
          201,
        ),
      )
      .mockResolvedValueOnce(jsonResponse(ATTACHABLE_INVENTORY));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<InsuranceDocumentInventory memberId={MEMBER_ID} />);

    expect(
      await screen.findByText("가져오기 분류 제안: 보조자료"),
    ).toBeInTheDocument();
    const role = screen.getByRole("combobox", { name: "검수할 문서 역할" });
    expect(role).toHaveValue("");
    await user.selectOptions(role, "terms");
    const pageStart = screen.getByRole("spinbutton", { name: "시작 페이지" });
    const pageEnd = screen.getByRole("spinbutton", { name: "마지막 페이지" });
    expect(pageStart).toHaveAttribute("max", "7");
    expect(pageEnd).toHaveAttribute("max", "7");
    await user.clear(pageStart);
    await user.type(pageStart, "2");
    await user.clear(pageEnd);
    await user.type(pageEnd, "7");
    await user.click(screen.getByRole("button", { name: "검수 내용 확정" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      `/api/v1/family-members/${MEMBER_ID}/insurance-document-components`,
    );
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      document_batch_item_id: "synthetic-batch-item-review-001",
      page_end: 7,
      page_start: 2,
      review_state: "USER_CONFIRMED",
      role: "terms",
    });
  });

  it("attaches an unpaired component with USER_CONFIRMED and reloads the inventory", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(ATTACHABLE_INVENTORY))
      .mockResolvedValueOnce(
        jsonResponse(
          {
            id: "synthetic-item-attached-001",
            insurance_document_component_id: "synthetic-component-unpaired-001",
            insurance_document_set_id: "synthetic-set-001",
            match_state: "USER_CONFIRMED",
            role: "terms",
            version: 1,
          },
          201,
        ),
      )
      .mockResolvedValueOnce(jsonResponse(INVENTORY));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<InsuranceDocumentInventory memberId={MEMBER_ID} />);

    const target = await screen.findByRole("combobox", {
      name: /문서를 연결할 보험/i,
    });
    expect(target).toHaveValue("synthetic-set-001");
    await user.click(screen.getByRole("button", { name: /약관.*문서 연결$/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/v1/insurance-document-sets/synthetic-set-001/items",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      expected_set_version: 2,
      insurance_document_component_id: "synthetic-component-unpaired-001",
      match_state: "USER_CONFIRMED",
    });
  });

  it("creates a registered document set before the first attachment", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(ATTACHABLE_WITHOUT_SET_INVENTORY))
      .mockResolvedValueOnce(
        jsonResponse(
          {
            display_label: "Sample Policy",
            id: "synthetic-set-created-001",
            insurer_display: "Sample Insurer",
            member_id: MEMBER_ID,
            policy_contract_id: "synthetic-policy-001",
            product_display: "Sample Policy",
            version: 1,
          },
          201,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          {
            id: "synthetic-item-attached-002",
            insurance_document_component_id: "synthetic-component-unpaired-001",
            insurance_document_set_id: "synthetic-set-created-001",
            match_state: "USER_CONFIRMED",
            role: "terms",
            version: 1,
          },
          201,
        ),
      )
      .mockResolvedValueOnce(jsonResponse(INVENTORY));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<InsuranceDocumentInventory memberId={MEMBER_ID} />);

    expect(
      await screen.findByRole("option", {
        name: /앱 근거 연결 계약.*Sample Policy/,
      }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /약관.*문서 연결$/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      `/api/v1/family-members/${MEMBER_ID}/insurance-document-sets`,
    );
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      display_label: "Sample Policy",
      policy_contract_id: "synthetic-policy-001",
    });
    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      "/api/v1/insurance-document-sets/synthetic-set-created-001/items",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[2]?.[1]?.body))).toEqual({
      expected_set_version: 1,
      insurance_document_component_id: "synthetic-component-unpaired-001",
      match_state: "USER_CONFIRMED",
    });
  });

  it("detaches a current set item with its expected item version and reloads", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(INVENTORY))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(jsonResponse(INVENTORY));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<InsuranceDocumentInventory memberId={MEMBER_ID} />);

    await user.click(
      (await screen.findAllByRole("button", { name: /연결 해제/ }))[0]!,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/v1/insurance-document-set-items/synthetic-item-001",
    );
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({
      body: JSON.stringify({ expected_version: 1 }),
      method: "DELETE",
    });
  });
});
