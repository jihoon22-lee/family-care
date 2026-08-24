import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  CoverageRuleVersionsResponse,
  PolicyReviewItem,
  RiderClauseLinkResponse,
} from "../../api/generated";
import { renderWithProviders } from "../../test/renderWithProviders";
import { RuleReviewPage } from "./RuleReviewPage";

const LINK_REVIEW_ID = "00000000-0000-4000-8000-000000000701";
const LINK_ID = "00000000-0000-4000-8000-000000000702";
const RIDER_ID = "00000000-0000-4000-8000-000000000703";
const RULE_REVIEW_ID = "00000000-0000-4000-8000-000000000704";
const RULE_ID = "00000000-0000-4000-8000-000000000705";
const RULE_VERSION_ID = "00000000-0000-4000-8000-000000000706";
const EVIDENCE_ID = "00000000-0000-4000-8000-000000000707";
const DOCUMENT_VERSION_ID = "00000000-0000-4000-8000-000000000708";
const PRIVATE_MARKERS = [
  "FULL_SYNTHETIC_RULE_BODY",
  "/synthetic/private/policy.pdf",
  "synthetic-provider-request-id",
];

const candidateEvidence = {
  bbox: null,
  bounded_excerpt: "합성 증권과 약관의 짧은 근거입니다.",
  document_label: "Sample Policy · 2페이지",
  document_version_id: DOCUMENT_VERSION_ID,
  evidence_id: EVIDENCE_ID,
  page: 2,
};

const linkReview = {
  aggregate_id: LINK_ID,
  candidate_kind: "rider_clause",
  candidate_version_id: "00000000-0000-4000-8000-000000000709",
  evidence: [candidateEvidence],
  expected_version: 1,
  fields: [
    { evidence_ids: [EVIDENCE_ID], field_id: "rider_id", value: RIDER_ID },
    {
      evidence_ids: [EVIDENCE_ID],
      field_id: "link_review_state",
      value: "NEEDS_REVIEW",
    },
  ],
  issues: [{ code: "LOW_CONFIDENCE", field_id: "terms_edition_id" }],
  review_item_id: LINK_REVIEW_ID,
  status: "NEEDS_REVIEW",
} satisfies PolicyReviewItem;

const ruleReview = {
  aggregate_id: RULE_ID,
  candidate_kind: "coverage_rule",
  candidate_version_id: "00000000-0000-4000-8000-000000000710",
  evidence: [candidateEvidence],
  expected_version: 1,
  fields: [
    {
      evidence_ids: [EVIDENCE_ID],
      field_id: "rule_operator",
      value: "date_between",
    },
    {
      evidence_ids: [EVIDENCE_ID],
      field_id: "fact_field",
      value: "MedicalEvent.event_date",
    },
  ],
  issues: [{ code: "UNSUPPORTED_DSL", field_id: "rule_operator" }],
  review_item_id: RULE_REVIEW_ID,
  status: "NEEDS_REVIEW",
} satisfies PolicyReviewItem;

const link = {
  applicability_reason_code: "WRONG_EDITION",
  clause_id: "00000000-0000-4000-8000-000000000711",
  clause_label: "제3조 합성 보장 개시일",
  evidence: [
    {
      bbox: null,
      content_sha256: "a".repeat(64),
      document_version_id: DOCUMENT_VERSION_ID,
      evidence_id: EVIDENCE_ID,
      page_number: 2,
    },
  ],
  link_id: LINK_ID,
  review_state: "NEEDS_REVIEW",
  rider_id: RIDER_ID,
  rider_label: "Sample Rider",
  terms_edition_id: "00000000-0000-4000-8000-000000000712",
  version: 1,
} satisfies RiderClauseLinkResponse;

const versions = {
  expected_version: 1,
  rule_id: RULE_ID,
  versions: [
    {
      evidence: link.evidence,
      executable: false,
      generator_version: "synthetic-generator-v1",
      input_field_paths: ["MedicalEvent.event_date"],
      required: true,
      result_reason_code: "SYNTHETIC_WAITING_PERIOD",
      review_state: "NEEDS_REVIEW",
      rule_kind: "temporal",
      schema_version: "coverage-rule-v1",
      verifier_version: "synthetic-verifier-v1",
      version_id: RULE_VERSION_ID,
      version_number: 1,
    },
  ],
} satisfies CoverageRuleVersionsResponse;

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    headers: {
      "cache-control": "no-store",
      "content-type": "application/json",
    },
    status,
  });
}

function installFetch({
  conflict = false,
  linkItem = linkReview,
  ruleItem = ruleReview,
  ruleVersions = versions,
}: {
  conflict?: boolean;
  linkItem?: PolicyReviewItem;
  ruleItem?: PolicyReviewItem;
  ruleVersions?: CoverageRuleVersionsResponse;
} = {}) {
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/review-items") {
        return response(
          url.searchParams.get("domain") === "rider_clause"
            ? [linkItem]
            : [ruleItem],
        );
      }
      if (url.pathname === `/api/v1/riders/${RIDER_ID}/clause-links`) {
        return response([link]);
      }
      if (url.pathname === `/api/v1/coverage-rules/${RULE_ID}/versions`) {
        return response(ruleVersions);
      }
      if (
        url.pathname ===
          `/api/v1/review-items/${RULE_REVIEW_ID}/fields/rule_operator` &&
        init?.method === "PATCH"
      ) {
        if (conflict) {
          return response(
            { error_code: "VERSION_CONFLICT", message: "version conflict" },
            409,
          );
        }
        return response({
          ...ruleReview,
          candidate_version_id: "00000000-0000-4000-8000-000000000713",
          expected_version: 2,
          fields: ruleReview.fields.map((field) =>
            field.field_id === "rule_operator"
              ? { ...field, value: "present" }
              : field,
          ),
        });
      }
      if (
        url.pathname.endsWith("/confirm") ||
        url.pathname.endsWith("/reject")
      ) {
        return response({
          ...link,
          review_state: "USER_CONFIRMED",
          version: 2,
        });
      }
      if (url.pathname.endsWith("/publish")) {
        return response({
          ...versions.versions[0],
          executable: true,
          review_state: "AI_VERIFIED",
        });
      }
      return response({ error_code: "NOT_FOUND", message: "not found" }, 404);
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Rider clause and CoverageRule review", () => {
  it("keeps link and rule exceptions separate and omits raw rule and private transport values", async () => {
    installFetch();
    renderWithProviders(<RuleReviewPage />);

    expect(
      await screen.findByRole("heading", { name: "담보와 약관 연결" }),
    ).toBeVisible();
    expect(screen.getByRole("heading", { name: "보장 규칙" })).toBeVisible();
    expect(screen.getByText("LOW_CONFIDENCE")).toBeVisible();
    expect(screen.getByText("UNSUPPORTED_DSL")).toBeVisible();
    const rendered = document.body.textContent ?? "";
    for (const marker of PRIVATE_MARKERS)
      expect(rendered).not.toContain(marker);
  });

  it("shows exact Evidence and confirms only the selected stored link version", async () => {
    const fetchMock = installFetch({
      linkItem: { ...linkReview, status: "AI_VERIFIED" },
    });
    const user = userEvent.setup();
    renderWithProviders(<RuleReviewPage />);

    await user.click(await screen.findByRole("button", { name: /연결 검토/ }));
    const dialog = await screen.findByRole("dialog", {
      name: /담보와 약관 연결 검토/,
    });
    expect(dialog).toHaveFocus();
    expect(dialog).toHaveTextContent("Sample Rider");
    expect(dialog).toHaveTextContent("제3조 합성 보장 개시일");
    expect(dialog).toHaveTextContent("2페이지");
    await user.click(within(dialog).getByRole("button", { name: "연결 확인" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input, init]) => {
          const url = new URL(String(input), window.location.origin);
          return (
            url.pathname === `/api/v1/rider-clause-links/${LINK_ID}/confirm` &&
            init?.method === "POST" &&
            JSON.parse(String(init.body)).expected_version === 1
          );
        }),
      ).toBe(true),
    );
  });

  it("keeps keyboard focus inside the open link review dialog", async () => {
    installFetch();
    const user = userEvent.setup();
    renderWithProviders(<RuleReviewPage />);

    await user.click(await screen.findByRole("button", { name: /연결 검토/ }));
    const dialog = await screen.findByRole("dialog", {
      name: /담보와 약관 연결 검토/,
    });
    const close = within(dialog).getByRole("button", { name: "닫기" });
    const reject = within(dialog).getByRole("button", { name: "연결 제외" });
    reject.focus();
    await user.keyboard("{Tab}");
    expect(close).toHaveFocus();
    await user.keyboard("{Shift>}{Tab}{/Shift}");
    expect(reject).toHaveFocus();
  });

  it.each([
    ["TERMS_ONLY_RIDER", "rider_id"],
    ["WRONG_EDITION", "terms_edition_id"],
    ["STALE_EVIDENCE", "clause_id"],
  ] as const)(
    "blocks confirmation for %s and keeps rejection available",
    async (code, fieldId) => {
      installFetch({
        linkItem: {
          ...linkReview,
          status: "AI_VERIFIED",
          issues: [{ code, field_id: fieldId }],
        },
      });
      const user = userEvent.setup();
      renderWithProviders(<RuleReviewPage />);

      await user.click(
        await screen.findByRole("button", { name: /연결 검토/ }),
      );
      const dialog = await screen.findByRole("dialog", {
        name: /담보와 약관 연결 검토/,
      });
      expect(
        within(dialog).getByRole("button", { name: "연결 확인" }),
      ).toBeDisabled();
      expect(
        within(dialog).getByRole("button", { name: "연결 제외" }),
      ).toBeEnabled();
    },
  );

  it("uses a typed child-version correction and keeps the draft on conflict", async () => {
    installFetch({ conflict: true });
    const user = userEvent.setup();
    renderWithProviders(<RuleReviewPage />);

    await user.click(await screen.findByRole("button", { name: /규칙 검토/ }));
    const dialog = await screen.findByRole("dialog", {
      name: /보장 규칙 검토/,
    });
    const operator = within(dialog).getByRole("combobox", {
      name: "규칙 조건",
    });
    await user.selectOptions(operator, "present");
    await user.click(within(dialog).getByRole("button", { name: "수정 저장" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "다른 변경이 먼저 저장되었습니다",
    );
    expect(operator).toHaveValue("present");
  });

  it("saves a typed rule field as a child candidate version", async () => {
    const fetchMock = installFetch();
    const user = userEvent.setup();
    renderWithProviders(<RuleReviewPage />);

    await user.click(await screen.findByRole("button", { name: /규칙 검토/ }));
    const dialog = await screen.findByRole("dialog", {
      name: /보장 규칙 검토/,
    });
    await user.selectOptions(
      within(dialog).getByRole("combobox", { name: "규칙 조건" }),
      "present",
    );
    await user.click(within(dialog).getByRole("button", { name: "수정 저장" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input, init]) => {
          const url = new URL(String(input), window.location.origin);
          if (
            url.pathname !==
              `/api/v1/review-items/${RULE_REVIEW_ID}/fields/rule_operator` ||
            init?.method !== "PATCH"
          ) {
            return false;
          }
          const body = JSON.parse(String(init?.body)) as Record<
            string,
            unknown
          >;
          return (
            body.field_id === "rule_operator" &&
            body.value === "present" &&
            body.expected_version === 1 &&
            body.evidence_id === EVIDENCE_ID
          );
        }),
      ).toBe(true),
    );
  });

  it("keeps an unsupported rule informational and does not enable publication", async () => {
    installFetch();
    const user = userEvent.setup();
    renderWithProviders(<RuleReviewPage />);

    await user.click(await screen.findByRole("button", { name: /규칙 검토/ }));
    const dialog = await screen.findByRole("dialog", {
      name: /보장 규칙 검토/,
    });
    expect(dialog).toHaveTextContent("판정에는 아직 사용하지 않습니다");
    expect(
      within(dialog).getByRole("button", { name: "규칙 게시" }),
    ).toBeDisabled();
  });

  it("publishes only an approved, evidence-backed stored rule version", async () => {
    const fetchMock = installFetch({
      ruleItem: {
        ...ruleReview,
        issues: [{ code: "LOW_CONFIDENCE", field_id: "rule_operator" }],
        status: "AI_VERIFIED",
      },
      ruleVersions: {
        ...versions,
        versions: [
          {
            ...versions.versions[0],
            review_state: "AI_VERIFIED",
          },
        ],
      },
    });
    const user = userEvent.setup();
    renderWithProviders(<RuleReviewPage />);

    await user.click(await screen.findByRole("button", { name: /규칙 검토/ }));
    const dialog = await screen.findByRole("dialog", {
      name: /보장 규칙 검토/,
    });
    await user.click(within(dialog).getByRole("button", { name: "규칙 게시" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input, init]) => {
          const url = new URL(String(input), window.location.origin);
          return (
            url.pathname === `/api/v1/coverage-rules/${RULE_ID}/publish` &&
            init?.method === "POST" &&
            JSON.parse(String(init.body)).version_id === RULE_VERSION_ID
          );
        }),
      ).toBe(true),
    );
  });
});
