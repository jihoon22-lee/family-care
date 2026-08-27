import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  CandidateEvidenceRef,
  CandidateField,
  PolicyReviewItem,
} from "../../api/generated";
import { EvidenceDrawer } from "../../components/EvidenceDrawer";
import { CandidateReviewDialog } from "./CandidateReviewDialog";
import { LedgerPage } from "./LedgerPage";
import { renderWithProviders } from "../../test/renderWithProviders";

const FORBIDDEN_KEYS = [
  "source_path",
  "absolute_path",
  "archive_key",
  "password",
  "policy_number",
  "raw_pdf",
  "raw_provider_response",
  "household_space_id",
] as const;

const PROVIDER_PROSE =
  "Synthetic provider prose must never become user-facing issue copy.";

const POLICY_EVIDENCE: CandidateEvidenceRef = {
  evidence_id: "synthetic-evidence-policy-001",
  document_version_id: "synthetic-document-version-policy-001",
  document_label: "Sample Policy",
  page: 2,
  bbox: [10, 20, 120, 160],
  bounded_excerpt: "Sample policy rider evidence excerpt.",
};

const TERMS_EVIDENCE: CandidateEvidenceRef = {
  evidence_id: "synthetic-evidence-terms-001",
  document_version_id: "synthetic-document-version-terms-001",
  document_label: "Sample Terms",
  page: 7,
  bbox: null,
  bounded_excerpt: "Sample terms-only evidence excerpt.",
};

const ENROLLED_RIDER_FIELDS: CandidateField[] = [
  {
    field_id: "rider_name",
    value: "Sample Enrolled Rider",
    evidence_ids: [POLICY_EVIDENCE.evidence_id],
  },
  {
    field_id: "rider_key",
    value: "sample-enrolled-rider",
    evidence_ids: [POLICY_EVIDENCE.evidence_id],
  },
  {
    field_id: "benefit_type",
    value: "fixed",
    evidence_ids: [POLICY_EVIDENCE.evidence_id],
  },
  {
    field_id: "sum_assured",
    value: 100000,
    evidence_ids: [POLICY_EVIDENCE.evidence_id],
  },
  {
    field_id: "currency",
    value: "KRW",
    evidence_ids: [POLICY_EVIDENCE.evidence_id],
  },
  {
    field_id: "renewable",
    value: false,
    evidence_ids: [POLICY_EVIDENCE.evidence_id],
  },
  {
    field_id: "rider_status",
    value: "active",
    evidence_ids: [POLICY_EVIDENCE.evidence_id],
  },
];

const TERMS_ONLY_FIELDS: CandidateField[] = [
  {
    field_id: "rider_name",
    value: "Terms-only Rider",
    evidence_ids: [TERMS_EVIDENCE.evidence_id],
  },
  {
    field_id: "rider_key",
    value: "terms-only-rider",
    evidence_ids: [TERMS_EVIDENCE.evidence_id],
  },
  {
    field_id: "benefit_type",
    value: "fixed",
    evidence_ids: [TERMS_EVIDENCE.evidence_id],
  },
];

const REVIEW_ITEM: PolicyReviewItem = {
  review_item_id: "synthetic-review-item-001",
  candidate_version_id: "synthetic-candidate-version-001",
  aggregate_id: "synthetic-policy-001",
  candidate_kind: "rider",
  status: "NEEDS_REVIEW",
  fields: ENROLLED_RIDER_FIELDS,
  evidence: [POLICY_EVIDENCE],
  issues: [{ code: "LOW_CONFIDENCE", field_id: "rider_name" }],
  expected_version: 1,
};

const TERMS_ONLY_REVIEW_ITEM: PolicyReviewItem = {
  review_item_id: "synthetic-review-item-terms-001",
  candidate_version_id: "synthetic-candidate-version-terms-001",
  aggregate_id: "synthetic-policy-001",
  candidate_kind: "rider",
  status: "NEEDS_REVIEW",
  fields: TERMS_ONLY_FIELDS,
  evidence: [TERMS_EVIDENCE],
  issues: [{ code: "TERMS_ONLY_RIDER", field_id: "rider_name" }],
  expected_version: 1,
};

type MockResponse = {
  body?: unknown;
  status?: number;
};

type MockApiOptions = {
  onRequest?: (request: {
    body: unknown;
    method: string;
    url: URL;
  }) => MockResponse | undefined;
  reviewItem?: PolicyReviewItem;
  termsOnlyItem?: PolicyReviewItem;
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function noForbiddenKeys(value: unknown): void {
  const serialized = JSON.stringify(value) ?? "";
  for (const key of FORBIDDEN_KEYS) {
    expect(serialized).not.toContain(`"${key}"`);
  }
}

function createMockApi(options: MockApiOptions = {}) {
  const requests: Array<{
    body: unknown;
    method: string;
    url: URL;
  }> = [];
  let reviewItem = options.reviewItem ?? REVIEW_ITEM;
  let termsOnlyItem = options.termsOnlyItem ?? TERMS_ONLY_REVIEW_ITEM;
  const includeTermsOnly = options.termsOnlyItem !== undefined;
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      const method = init?.method ?? "GET";
      const body =
        init?.body === undefined ? undefined : JSON.parse(String(init.body));
      const request = { body, method, url };
      requests.push(request);
      const customResponse = options.onRequest?.(request);
      if (customResponse) {
        return jsonResponse(
          customResponse.body ?? {},
          customResponse.status ?? 200,
        );
      }

      if (url.pathname === "/api/v1/family-members") {
        return jsonResponse([
          {
            id: "synthetic-member-a",
            display_name: "Family Member A",
            internal_alias: "family-member-a",
            deleted: false,
            version: 1,
          },
        ]);
      }

      if (url.pathname === "/api/v1/policies") {
        return jsonResponse([
          {
            id: "synthetic-policy-001",
            insurer_display: "Sample Insurer",
            insurer_key: "sample-insurer",
            product_display: "Sample Policy",
            product_key: "sample-policy",
            contract_date: "2026-01-01",
            coverage_start_date: "2026-01-01",
            coverage_end_date: "2026-12-31",
            status: "active",
            deleted: false,
            version: 1,
            parties: [],
            source_document_version_id: POLICY_EVIDENCE.document_version_id,
            source_evidence: {
              evidence_id: POLICY_EVIDENCE.evidence_id,
              document_version_id: POLICY_EVIDENCE.document_version_id,
              physical_page: POLICY_EVIDENCE.page,
              bbox: POLICY_EVIDENCE.bbox,
              content_sha256: "synthetic-hash-policy-001",
              review_state: "AI_VERIFIED",
            },
            status_evidence: null,
          },
        ]);
      }

      if (url.pathname.endsWith("/riders")) {
        return jsonResponse([
          {
            id: "synthetic-rider-001",
            policy_contract_id: "synthetic-policy-001",
            display_name: "Sample Enrolled Rider",
            normalized_key: "sample-enrolled-rider",
            benefit_type: "fixed",
            insured_amount: "100000",
            currency: "KRW",
            coverage_start_date: "2026-01-01",
            coverage_end_date: "2026-12-31",
            renewable: false,
            status: "active",
            version: 1,
            source_evidence: {
              evidence_id: POLICY_EVIDENCE.evidence_id,
              document_version_id: POLICY_EVIDENCE.document_version_id,
              physical_page: POLICY_EVIDENCE.page,
              bbox: POLICY_EVIDENCE.bbox,
              content_sha256: "synthetic-hash-policy-001",
              review_state: "AI_VERIFIED",
            },
            status_evidence: null,
          },
        ]);
      }

      if (url.pathname === "/api/v1/review-items") {
        const items: PolicyReviewItem[] = [reviewItem];
        if (includeTermsOnly) {
          items.push(termsOnlyItem);
        }
        const status = url.searchParams.get("status");
        return jsonResponse(
          status ? items.filter((item) => item.status === status) : items,
        );
      }

      const inventoryMatch = url.pathname.match(
        /^\/api\/v1\/family-members\/([^/]+)\/insurance-document-inventory$/,
      );
      if (inventoryMatch) {
        return jsonResponse({
          member_id: inventoryMatch[1],
          registered_policies: [],
          schema_version: "1",
          summary: {
            application_documents: 0,
            certificate_and_terms: 0,
            certificate_backed_policies: 0,
            certificate_only: 0,
            pairing_conflicts: 0,
            product_explanation_documents: 0,
            terms_only_documents: 0,
            unreadable_documents: 0,
          },
          unpaired_components: [],
          unregistered_document_sets: [],
          unreadable_sources: [],
        });
      }

      if (
        url.pathname === `/api/v1/review-items/${reviewItem.review_item_id}`
      ) {
        return jsonResponse(reviewItem);
      }

      if (
        url.pathname === `/api/v1/review-items/${termsOnlyItem.review_item_id}`
      ) {
        return jsonResponse(termsOnlyItem);
      }

      if (url.pathname.includes("/candidate-fields/") && method === "PATCH") {
        reviewItem = {
          ...reviewItem,
          expected_version: reviewItem.expected_version + 1,
          fields: reviewItem.fields.map((field) =>
            field.field_id === body?.field_id
              ? {
                  ...field,
                  value: body.value,
                  evidence_ids: [body.evidence_id],
                }
              : field,
          ),
        };
        return jsonResponse(reviewItem);
      }

      if (url.pathname.endsWith("/confirm") && method === "POST") {
        if (url.pathname.includes(reviewItem.review_item_id)) {
          reviewItem = { ...reviewItem, status: "USER_CONFIRMED" };
          return jsonResponse(reviewItem);
        }
        termsOnlyItem = { ...termsOnlyItem, status: "USER_CONFIRMED" };
        return jsonResponse(termsOnlyItem);
      }

      if (url.pathname.endsWith("/reject") && method === "POST") {
        if (url.pathname.includes(reviewItem.review_item_id)) {
          reviewItem = { ...reviewItem, status: "rejected" };
          return jsonResponse(reviewItem);
        }
        termsOnlyItem = { ...termsOnlyItem, status: "rejected" };
        return jsonResponse(termsOnlyItem);
      }

      return jsonResponse({});
    },
  );

  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, requests };
}

function getRequests(
  requests: Array<{ body: unknown; method: string; url: URL }>,
  method: string,
  path: string,
) {
  return requests.filter(
    (request) => request.method === method && request.url.pathname === path,
  );
}

describe("candidate review", () => {
  beforeEach(() => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => undefined);
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(
      () => undefined,
    );
    noForbiddenKeys(REVIEW_ITEM);
    noForbiddenKeys(TERMS_ONLY_REVIEW_ITEM);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("keeps a terms-only candidate out of enrolled Riders and uses fixed issue copy", async () => {
    createMockApi({ termsOnlyItem: TERMS_ONLY_REVIEW_ITEM });
    const user = userEvent.setup();
    renderWithProviders(<LedgerPage memberId="synthetic-member-a" />);

    expect(
      await screen.findByText("Sample Enrolled Rider"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Terms-only Rider")).not.toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "검토 필요 항목 보기" }),
    );
    const candidateButtons = screen.getAllByRole("button", {
      name: "후보 검토",
    });
    await user.click(candidateButtons[candidateButtons.length - 1]);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("TERMS_ONLY_RIDER")).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "약관에서만 확인된 후보는 가입 담보로 등록하지 않습니다.",
      ),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText(PROVIDER_PROSE)).not.toBeInTheDocument();
  });

  it("moves dialog focus in, restores the opener on Escape, and shows only bounded Evidence", async () => {
    createMockApi();
    const user = userEvent.setup();
    renderWithProviders(<LedgerPage memberId="synthetic-member-a" />);

    const opener = await screen.findByRole("button", { name: "후보 검토" });
    await user.click(opener);

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveFocus();
    expect(
      within(dialog).getByText(POLICY_EVIDENCE.document_label),
    ).toBeInTheDocument();
    expect(within(dialog).getAllByText(/2페이지/)).not.toHaveLength(0);
    expect(
      within(dialog).getByText(POLICY_EVIDENCE.bounded_excerpt),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByText(POLICY_EVIDENCE.evidence_id),
    ).not.toBeInTheDocument();
    expect(within(dialog).queryByText(PROVIDER_PROSE)).not.toBeInTheDocument();

    const closeButton = within(dialog).getByRole("button", { name: "닫기" });
    closeButton.focus();
    await user.keyboard("{Shift>}{Tab}{/Shift}");
    expect(within(dialog).getByRole("button", { name: "거절" })).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(opener).toHaveFocus();
  });

  it("disables confirmation when Evidence is stale or unavailable", async () => {
    createMockApi({
      reviewItem: {
        ...REVIEW_ITEM,
        evidence: [],
        fields: REVIEW_ITEM.fields.map((field) => ({
          ...field,
          evidence_ids: [],
        })),
        issues: [{ code: "MISSING_EVIDENCE", field_id: "rider_name" }],
      },
    });
    const user = userEvent.setup();
    renderWithProviders(<LedgerPage memberId="synthetic-member-a" />);

    await user.click(await screen.findByRole("button", { name: "후보 검토" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /근거가 필요합니다/,
    );
    expect(screen.getByRole("button", { name: "확인" })).toBeDisabled();
  });

  it("submits typed corrections with a generated field id, typed value, and Evidence", async () => {
    const { requests } = createMockApi();
    const user = userEvent.setup();
    renderWithProviders(<LedgerPage memberId="synthetic-member-a" />);

    await user.click(await screen.findByRole("button", { name: "후보 검토" }));
    const dialog = await screen.findByRole("dialog");
    const field = within(dialog).getByRole("combobox", { name: "수정할 필드" });
    await user.selectOptions(field, "sum_assured");
    const amount = within(dialog).getByRole("spinbutton", {
      name: "sum_assured",
    });
    await user.clear(amount);
    await user.type(amount, "125000");
    await user.selectOptions(
      within(dialog).getByRole("combobox", { name: "근거 Evidence" }),
      POLICY_EVIDENCE.evidence_id,
    );
    await user.click(within(dialog).getByRole("button", { name: "수정 저장" }));

    await waitFor(() => {
      const correction = getRequests(
        requests,
        "PATCH",
        "/api/v1/review-items/synthetic-review-item-001/candidate-fields/sum_assured",
      )[0];
      expect(correction?.body).toEqual({
        expected_version: 1,
        field_id: "sum_assured",
        value: 125000,
        evidence_id: POLICY_EVIDENCE.evidence_id,
      });
    });
  });

  it("uses an explicit boolean control for renewable corrections", async () => {
    createMockApi();
    const user = userEvent.setup();
    renderWithProviders(<LedgerPage memberId="synthetic-member-a" />);

    await user.click(await screen.findByRole("button", { name: "후보 검토" }));
    const dialog = await screen.findByRole("dialog");
    await user.selectOptions(
      within(dialog).getByRole("combobox", { name: "수정할 필드" }),
      "renewable",
    );
    expect(
      within(dialog).getByRole("combobox", { name: "renewable" }),
    ).toHaveValue("false");
  });

  it("preserves the unsaved draft, refetches after 409, and retries safely", async () => {
    let correctionAttempts = 0;
    const { requests } = createMockApi({
      onRequest: ({ method, url }) => {
        if (method === "PATCH" && url.pathname.includes("/candidate-fields/")) {
          correctionAttempts += 1;
          if (correctionAttempts === 1) {
            return {
              status: 409,
              body: {
                error_code: "VERSION_CONFLICT",
                message: PROVIDER_PROSE,
              },
            };
          }
        }
        return undefined;
      },
    });
    const user = userEvent.setup();
    renderWithProviders(<LedgerPage memberId="synthetic-member-a" />);

    await user.click(await screen.findByRole("button", { name: "후보 검토" }));
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByRole("textbox", { name: "rider_name" });
    await user.clear(input);
    await user.type(input, "Sample Rider Corrected");
    await user.selectOptions(
      within(dialog).getByRole("combobox", { name: "근거 Evidence" }),
      POLICY_EVIDENCE.evidence_id,
    );
    await user.click(within(dialog).getByRole("button", { name: "수정 저장" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "다른 변경이 먼저 저장되었습니다",
    );
    expect(
      within(screen.getByRole("dialog")).getByRole("textbox", {
        name: "rider_name",
      }),
    ).toHaveValue("Sample Rider Corrected");
    expect(
      getRequests(
        requests,
        "GET",
        "/api/v1/review-items/synthetic-review-item-001",
      ).length,
    ).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "다시 시도" }));
    await waitFor(() => expect(correctionAttempts).toBe(2));
    expect(
      getRequests(
        requests,
        "PATCH",
        "/api/v1/review-items/synthetic-review-item-001/candidate-fields/rider_name",
      )[1]?.body,
    ).toEqual({
      expected_version: 1,
      field_id: "rider_name",
      value: "Sample Rider Corrected",
      evidence_id: POLICY_EVIDENCE.evidence_id,
    });
  });

  it("invalidates ledger data after confirm and reject, without Web Storage writes", async () => {
    const { fetchMock, requests } = createMockApi({
      termsOnlyItem: TERMS_ONLY_REVIEW_ITEM,
    });
    const user = userEvent.setup();
    renderWithProviders(<LedgerPage memberId="synthetic-member-a" />);

    const opener = await screen.findByRole("button", { name: "후보 검토" });
    await user.click(opener);
    await user.click(screen.getByRole("button", { name: "확인" }));
    await waitFor(() => {
      expect(
        getRequests(
          requests,
          "POST",
          "/api/v1/review-items/synthetic-review-item-001/confirm",
        ),
      ).toHaveLength(1);
    });

    await user.click(
      await screen.findByRole("button", { name: "검토 필요 항목 보기" }),
    );
    await user.click(screen.getByRole("button", { name: "후보 검토" }));
    await user.click(screen.getByRole("button", { name: "거절" }));
    await user.click(screen.getByRole("button", { name: "거절 확정" }));
    await waitFor(() => {
      expect(
        getRequests(
          requests,
          "POST",
          "/api/v1/review-items/synthetic-review-item-terms-001/reject",
        ),
      ).toHaveLength(1);
    });

    expect(fetchMock).toHaveBeenCalled();
    expect(Storage.prototype.setItem).not.toHaveBeenCalled();
    expect(sessionStorage.setItem).not.toHaveBeenCalled();
    for (const request of requests) {
      noForbiddenKeys(request.body);
    }
  });

  it("does not confirm a dialog when rendered with no Evidence", async () => {
    const itemWithoutEvidence: PolicyReviewItem = {
      ...REVIEW_ITEM,
      evidence: [],
      fields: REVIEW_ITEM.fields.map((field) => ({
        ...field,
        evidence_ids: [],
      })),
      issues: [{ code: "MISSING_EVIDENCE", field_id: "rider_name" }],
    };
    const onClose = vi.fn();
    const onConfirmed = vi.fn();
    render(
      <CandidateReviewDialog
        item={itemWithoutEvidence}
        onClose={onClose}
        onConfirmed={onConfirmed}
      />,
    );

    expect(screen.getByRole("button", { name: "확인" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent(/근거가 필요합니다/);
    expect(onConfirmed).not.toHaveBeenCalled();
  });

  it("keeps EvidenceDrawer bounded to label, page, coordinates, and excerpt", () => {
    const onClose = vi.fn();
    render(
      <EvidenceDrawer evidence={[POLICY_EVIDENCE]} open onClose={onClose} />,
    );

    expect(screen.getByRole("dialog")).toHaveTextContent("Sample Policy");
    expect(screen.getByRole("dialog")).toHaveTextContent("2");
    expect(screen.getByRole("dialog")).toHaveTextContent(
      POLICY_EVIDENCE.bounded_excerpt,
    );
    expect(screen.getByRole("dialog")).not.toHaveTextContent(
      "synthetic-evidence-policy-001",
    );
    expect(screen.getByRole("dialog")).not.toHaveTextContent(PROVIDER_PROSE);
  });
});
