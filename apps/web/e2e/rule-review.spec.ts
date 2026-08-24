import { expect, test } from "@playwright/test";

import type {
  CoverageRuleVersionsResponse,
  PolicyReviewItem,
} from "../src/api/generated";

const RULE_ID = "00000000-0000-4000-8000-000000000801";
const RULE_VERSION_ID = "00000000-0000-4000-8000-000000000802";
const REVIEW_ID = "00000000-0000-4000-8000-000000000803";
const EVIDENCE_ID = "00000000-0000-4000-8000-000000000804";
const DOCUMENT_VERSION_ID = "00000000-0000-4000-8000-000000000805";

const ruleReview = {
  aggregate_id: RULE_ID,
  candidate_kind: "coverage_rule",
  candidate_version_id: "00000000-0000-4000-8000-000000000806",
  evidence: [
    {
      bbox: null,
      bounded_excerpt: "합성 약관의 보장 개시일 근거입니다.",
      document_label: "Sample Terms",
      document_version_id: DOCUMENT_VERSION_ID,
      evidence_id: EVIDENCE_ID,
      page: 2,
    },
  ],
  expected_version: 1,
  fields: [
    {
      evidence_ids: [EVIDENCE_ID],
      field_id: "rule_operator",
      value: "date_between",
    },
  ],
  issues: [{ code: "LOW_CONFIDENCE", field_id: "rule_operator" }],
  review_item_id: REVIEW_ID,
  status: "NEEDS_REVIEW",
} satisfies PolicyReviewItem;

const versions = {
  expected_version: 1,
  rule_id: RULE_ID,
  versions: [
    {
      evidence: [
        {
          bbox: null,
          content_sha256: "a".repeat(64),
          document_version_id: DOCUMENT_VERSION_ID,
          evidence_id: EVIDENCE_ID,
          page_number: 2,
        },
      ],
      executable: false,
      generator_version: "synthetic-generator-v1",
      input_field_paths: ["MedicalEvent.event_date"],
      required: true,
      result_reason_code: "SYNTHETIC_WAITING_PERIOD",
      review_state: "AI_VERIFIED",
      rule_kind: "temporal",
      schema_version: "coverage-rule-v1",
      verifier_version: "synthetic-verifier-v1",
      version_id: RULE_VERSION_ID,
      version_number: 1,
    },
  ],
} satisfies CoverageRuleVersionsResponse;

test("publishes only a stored verified rule with Evidence at 320px", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.addInitScript(() => {
    const writes = { indexedDB: 0, localStorage: 0, sessionStorage: 0 };
    Object.defineProperty(window, "__familyCareStorageWrites", {
      value: writes,
    });
    const originalSetItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function setItem(key, value) {
      if (this === window.localStorage) writes.localStorage += 1;
      if (this === window.sessionStorage) writes.sessionStorage += 1;
      return originalSetItem.call(this, key, value);
    };
  });

  let publishBody: Record<string, unknown> | undefined;
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/review-items") {
      await route.fulfill({
        body: JSON.stringify(
          url.searchParams.get("domain") === "coverage_rule"
            ? [ruleReview]
            : [],
        ),
        contentType: "application/json",
        headers: { "Cache-Control": "no-store" },
      });
      return;
    }
    if (url.pathname === `/api/v1/coverage-rules/${RULE_ID}/versions`) {
      await route.fulfill({
        body: JSON.stringify(versions),
        contentType: "application/json",
        headers: { "Cache-Control": "no-store" },
      });
      return;
    }
    if (
      request.method() === "POST" &&
      url.pathname === `/api/v1/coverage-rules/${RULE_ID}/publish`
    ) {
      publishBody = request.postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        body: JSON.stringify({ ...versions.versions[0], executable: true }),
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

  await page.goto("/app/clauses/review", { waitUntil: "domcontentloaded" });
  await expect(
    page.getByRole("heading", { name: "보장 규칙 검토", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "규칙 검토" }).click();
  const dialog = page.getByRole("dialog", { name: "보장 규칙 검토" });
  await expect(dialog).toBeFocused();
  await expect(dialog).toContainText("MedicalEvent.event_date");
  await expect(dialog).not.toContainText("FULL_SYNTHETIC_RULE_BODY");
  await dialog.getByRole("button", { name: "근거 보기 Evidence" }).click();
  await expect(page.getByRole("dialog", { name: "근거 페이지" })).toContainText(
    "2페이지",
  );
  await page
    .getByRole("dialog", { name: "근거 페이지" })
    .getByRole("button", { name: "닫기" })
    .click();
  await dialog.getByRole("button", { name: "규칙 게시" }).click();

  expect(publishBody).toEqual({
    expected_version: 1,
    version_id: RULE_VERSION_ID,
  });
  const layout = await page.evaluate(() => ({
    innerWidth: window.innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.innerWidth);
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
