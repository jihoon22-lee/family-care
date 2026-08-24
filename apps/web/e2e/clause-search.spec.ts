import { expect, test } from "@playwright/test";

import type {
  ClauseEvidenceResponse,
  ClauseHierarchyNodeResponse,
  ClauseHierarchyResponse,
  ClauseSearchHitResponse,
  ClauseSearchResponse,
  TermsEditionResponse,
} from "../src/api/generated";

const TERMS_EDITION_ID = "synthetic-terms-edition-001";
const CLAUSE_ID = "synthetic-clause-001";
const EVIDENCE_ID = "synthetic-clause-evidence-001";
const NORMALIZATION_VERSION = "unicode-nfc-v1";

const termsEdition = {
  applicability_end: "2026-12-31",
  applicability_start: "2026-01-01",
  content_sha256: "b".repeat(64),
  document_version_id: "synthetic-terms-document-version-001",
  id: TERMS_EDITION_ID,
  insurer_display: "Synthetic Mutual",
  insurer_key: "synthetic-mutual",
  normalization_version: NORMALIZATION_VERSION,
  product_display: "Sample Terms",
  product_key: "sample-terms",
  version: 1,
} satisfies TermsEditionResponse;

const evidence = {
  bbox: null,
  content_sha256: "a".repeat(64),
  document_version_id: "synthetic-terms-document-version-001",
  evidence_id: EVIDENCE_ID,
  page_number: 14,
} satisfies ClauseEvidenceResponse;

function clauseHit(): ClauseSearchHitResponse {
  return {
    clause_id: CLAUSE_ID,
    evidence: [evidence],
    excerpt: "합성 약관에서 보장 개시일과 대기기간을 설명하는 발췌입니다.",
    label: "제3조 보장 개시일",
    normalization_version: NORMALIZATION_VERSION,
    physical_page_end: 15,
    physical_page_start: 14,
    relevance: 0.91,
    terms_edition_id: TERMS_EDITION_ID,
  };
}

const hierarchyNode = {
  clause_id: CLAUSE_ID,
  clause_type: "article",
  evidence: [evidence],
  excerpt: "합성 약관에서 보장 개시일과 대기기간을 설명하는 발췌입니다.",
  label: "제3조 보장 개시일",
  normalization_version: NORMALIZATION_VERSION,
  parent_clause_id: null,
  physical_page_end: 15,
  physical_page_start: 14,
} satisfies ClauseHierarchyNodeResponse;

const hierarchyResponse = {
  clauses: [hierarchyNode],
  terms_edition_id: TERMS_EDITION_ID,
} satisfies ClauseHierarchyResponse;

const searchResponse = {
  hits: [clauseHit()],
  normalization_version: NORMALIZATION_VERSION,
  query_matched_count: 1,
  schema_version: "1",
} satisfies ClauseSearchResponse;

test("searches with POST body, opens Evidence, and stays usable at 320px", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.addInitScript(() => {
    const writes = { indexedDB: 0, localStorage: 0, sessionStorage: 0 };
    Object.defineProperty(window, "__familyCareStorageWrites", {
      configurable: true,
      value: writes,
    });
    const originalSetItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function setItem(key, value) {
      if (this === window.localStorage) writes.localStorage += 1;
      if (this === window.sessionStorage) writes.sessionStorage += 1;
      return originalSetItem.call(this, key, value);
    };
    if (typeof indexedDB !== "undefined") {
      const originalOpen = IDBFactory.prototype.open;
      IDBFactory.prototype.open = function open(name, version) {
        writes.indexedDB += 1;
        return originalOpen.call(this, name, version);
      };
    }
  });

  let searchBody: Record<string, unknown> | undefined;
  let searchUrl = "";
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (
      request.method() === "GET" &&
      url.pathname === "/api/v1/terms-editions"
    ) {
      await route.fulfill({
        body: JSON.stringify([termsEdition]),
        contentType: "application/json",
        headers: { "Cache-Control": "no-store" },
      });
      return;
    }
    if (
      request.method() === "POST" &&
      url.pathname === "/api/v1/clauses/search"
    ) {
      searchUrl = request.url();
      searchBody = request.postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        body: JSON.stringify(searchResponse),
        contentType: "application/json",
        headers: { "Cache-Control": "no-store" },
      });
      return;
    }
    if (
      request.method() === "GET" &&
      url.pathname === `/api/v1/terms-editions/${TERMS_EDITION_ID}/clauses`
    ) {
      await route.fulfill({
        body: JSON.stringify(hierarchyResponse),
        contentType: "application/json",
        headers: { "Cache-Control": "no-store" },
      });
      return;
    }
    await route.fulfill({
      body: JSON.stringify({
        error_code: "NOT_FOUND",
        message: "synthetic route",
      }),
      contentType: "application/json",
      status: 404,
    });
  });

  await page.goto("/app/clauses/search", { waitUntil: "domcontentloaded" });
  const query = page.getByRole("searchbox", { name: /약관|clause/i });
  const searchNav = page
    .getByRole("navigation", { name: "주요 화면" })
    .getByRole("link", { name: "약관 검색" });
  const searchForm = page.locator('form[role="search"]');
  await expect(searchNav).toBeVisible();
  await expect(searchNav).toBeEnabled();
  await expect(searchForm).toBeVisible();
  await expect(query).toBeVisible();
  await expect(query).toBeEnabled();
  await expect(
    page.getByRole("button", { name: /검색 Search/i }),
  ).toBeEnabled();
  await query.fill("대기기간");
  await query.press("Enter");

  await expect(
    page.getByRole("heading", { name: "제3조 보장 개시일" }),
  ).toBeVisible();
  expect(new URL(searchUrl).search).toBe("");
  expect(searchBody).toEqual({ limit: 20, q: "대기기간" });
  const layout = await page.evaluate(() => ({
    innerWidth: window.innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.innerWidth);
  await expect(
    page.getByText(/Physical page \/ 물리 페이지 14–15/),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /검색 Search/i }),
  ).toBeVisible();

  await page.getByRole("button", { name: /근거 보기 Evidence/i }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toContainText("14페이지");
  await expect(drawer).toContainText("제3조 보장 개시일");

  const storageWrites = await page.evaluate(
    () =>
      (
        window as Window & {
          __familyCareStorageWrites?: Record<string, number>;
        }
      ).__familyCareStorageWrites,
  );
  expect(storageWrites).toEqual({
    indexedDB: 0,
    localStorage: 0,
    sessionStorage: 0,
  });
});
