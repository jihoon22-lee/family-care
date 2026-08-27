import {
  expect,
  test,
  type Page,
  type Request,
  type Route,
} from "@playwright/test";

import type {
  BatchResponse,
  FamilyMemberResponse,
  ImportSourceResponse,
} from "../src/api/generated";
import {
  installStorageWriteSpy,
  mockAuthenticatedSession,
} from "./support/mockApi";

const MEMBER_ID = "00000000-0000-4000-8000-000000000101";
const SOURCE_ID_A = "a".repeat(64);
const SOURCE_ID_B = "b".repeat(64);
const BATCH_ID = "00000000-0000-4000-8000-000000000201";
const PASSWORD = "synthetic-batch-password";
const SOURCE_PATH_MARKER = "synthetic-import-root";

const MEMBER: FamilyMemberResponse = {
  deleted: false,
  display_name: "Family Member A",
  id: MEMBER_ID,
  internal_alias: "family-member-a",
  version: 1,
};

const SOURCES: ImportSourceResponse[] = [
  {
    display_label: "Sample Policy A.pdf",
    encrypted: false,
    size_bytes: 1024,
    source_id: SOURCE_ID_A,
  },
  {
    display_label: "Sample Policy B.pdf",
    encrypted: true,
    size_bytes: 2048,
    source_id: SOURCE_ID_B,
  },
];

type ItemState = BatchResponse["items"][number]["state"];

function batchItem(
  source: ImportSourceResponse,
  state: ItemState,
): BatchResponse["items"][number] {
  return {
    attempts:
      state === "queued"
        ? 0
        : source.source_id === SOURCE_ID_B && state === "succeeded"
          ? 2
          : 1,
    display_label: source.display_label,
    document_kind: "policy",
    error_code: state === "password_required" ? "PASSWORD_REQUIRED" : null,
    ocr_pages_processed: state === "succeeded" ? 1 : 0,
    ocr_state: state === "succeeded" ? "completed" : "pending",
    ocr_warning_codes: [],
    source_id: source.source_id,
    state,
  };
}

function documentBatch(
  state: BatchResponse["state"],
  firstItemState: ItemState,
  secondItemState: ItemState,
): BatchResponse {
  return {
    batch_id: BATCH_ID,
    family_member_id: MEMBER_ID,
    items: [
      batchItem(SOURCES[0]!, firstItemState),
      batchItem(SOURCES[1]!, secondItemState),
    ],
    schema_version: "1",
    state,
  };
}

const PARTIAL_BATCH = documentBatch(
  "partial",
  "succeeded",
  "password_required",
);
const COMPLETED_BATCH = documentBatch("succeeded", "succeeded", "succeeded");

function requestBody(request: Request): Record<string, unknown> {
  const body = request.postData();
  if (!body) return {};
  try {
    const parsed: unknown = JSON.parse(body);
    return typeof parsed === "object" &&
      parsed !== null &&
      !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

async function fulfillJson(
  route: Route,
  payload: unknown,
  status = 200,
): Promise<void> {
  await route.fulfill({
    body: JSON.stringify(payload),
    contentType: "application/json",
    headers: { "Cache-Control": "no-store" },
    status,
  });
}

async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const layout = await page.evaluate(() => ({
    innerWidth: window.innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(layout.innerWidth).toBe(320);
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.innerWidth);
}

test("imports a synthetic encrypted batch without exposing file secrets", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await installStorageWriteSpy(page);

  const requestLog: Array<{ body: string; method: string; url: string }> = [];
  const createBodies: Record<string, unknown>[] = [];
  const passwordBodies: Record<string, unknown>[] = [];
  const unexpectedBatchRequests: string[] = [];

  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/v1/")) {
      requestLog.push({
        body: request.postData() ?? "",
        method: request.method(),
        url: request.url(),
      });
    }
  });

  await page.route("**/api/v1/family-members", async (route) => {
    await fulfillJson(route, [MEMBER]);
  });

  await page.route("**/api/v1/document-import-sources", async (route) => {
    await fulfillJson(route, SOURCES);
  });

  await page.route("**/api/v1/document-batches**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());

    if (
      request.method() === "POST" &&
      url.pathname === "/api/v1/document-batches"
    ) {
      createBodies.push(requestBody(request));
      await fulfillJson(route, PARTIAL_BATCH, 202);
      return;
    }

    if (
      request.method() === "POST" &&
      url.pathname === `/api/v1/document-batches/${BATCH_ID}/password`
    ) {
      passwordBodies.push(requestBody(request));
      await fulfillJson(route, COMPLETED_BATCH, 202);
      return;
    }

    unexpectedBatchRequests.push(`${request.method()} ${url.pathname}`);
    await fulfillJson(
      route,
      { error_code: "NOT_FOUND", message: "Synthetic route not found." },
      404,
    );
  });

  await mockAuthenticatedSession(page);

  await page.goto("/app/documents/import", { waitUntil: "domcontentloaded" });
  await expect(
    page.getByRole("heading", { name: "보험 PDF 가져오기" }),
  ).toBeVisible();
  await expect(
    page.getByRole("checkbox", { name: /Sample Policy A\.pdf/ }),
  ).toBeVisible();
  await expect(page.getByRole("combobox", { name: "가족 구성원" })).toHaveValue(
    MEMBER_ID,
  );

  await expect(page.locator('input[type="file"]')).toHaveCount(0);
  await expect(
    page.getByRole("textbox", { name: /경로|폴더|파일/ }),
  ).toHaveCount(0);
  await expectNoHorizontalOverflow(page);

  await page.getByRole("checkbox", { name: /Sample Policy A\.pdf/ }).check();
  await page.getByRole("checkbox", { name: /Sample Policy B\.pdf/ }).check();
  await page.getByRole("button", { name: "가져오기 시작" }).click();

  await expect(
    page.getByRole("heading", { name: "문서 처리 현황" }),
  ).toBeVisible();
  await expect(
    page.getByText("Sample Policy A.pdf", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("완료", { exact: true })).toBeVisible();
  await expect(page.getByText("비밀번호 필요", { exact: true })).toBeVisible();

  const passwordDialog = page.getByRole("dialog", {
    name: "PDF 비밀번호 입력",
  });
  await expect(passwordDialog).toBeVisible();
  await passwordDialog.getByLabel("PDF 비밀번호").fill(PASSWORD);
  await passwordDialog.getByRole("button", { name: "다시 처리" }).click();

  await expect(passwordDialog).toBeHidden();
  await expect(
    page.getByText("문서 처리가 끝났습니다. 보장 원장에서 확인할 수 있습니다."),
  ).toBeVisible();
  await expect(page.getByText("완료", { exact: true })).toHaveCount(2);
  await expect(page.getByText("비밀번호 필요", { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "보장 원장 열기" }),
  ).toHaveAttribute("href", `/app/members/${MEMBER_ID}/ledger`);
  await expectNoHorizontalOverflow(page);

  expect(createBodies).toEqual([
    {
      family_member_id: MEMBER_ID,
      schema_version: "1",
      source_ids: [SOURCE_ID_A, SOURCE_ID_B],
    },
  ]);
  expect(passwordBodies).toEqual([{ password: PASSWORD }]);
  expect(unexpectedBatchRequests).toEqual([]);

  const pageUrl = new URL(page.url());
  expect(pageUrl.pathname).toBe("/app/documents/import");
  expect(pageUrl.search).toBe("");
  expect(pageUrl.hash).toBe("");

  const sensitiveMarkers = [
    SOURCE_ID_A,
    SOURCE_ID_B,
    SOURCES[0]!.display_label,
    SOURCES[1]!.display_label,
    SOURCE_PATH_MARKER,
    PASSWORD,
  ];
  for (const marker of sensitiveMarkers) {
    expect(page.url()).not.toContain(marker);
  }
  for (const request of requestLog) {
    for (const marker of sensitiveMarkers) {
      expect(request.url).not.toContain(marker);
    }
    if (!request.url.endsWith(`/document-batches/${BATCH_ID}/password`)) {
      expect(request.body).not.toContain(PASSWORD);
    }
  }
  expect(passwordBodies).toHaveLength(1);
  expect(
    requestLog.filter(({ url }) => url.endsWith("/password")),
  ).toHaveLength(1);

  const browserState = await page.evaluate(() => ({
    localStorage: Object.entries(window.localStorage),
    sessionStorage: Object.entries(window.sessionStorage),
    writes: window.__familyCareStorageWrites,
  }));
  expect(browserState.writes).toEqual({
    indexedDB: 0,
    localStorage: 0,
    sessionStorage: 0,
  });
  const storageText = JSON.stringify(browserState);
  for (const marker of sensitiveMarkers) {
    expect(storageText).not.toContain(marker);
  }

  const cachedUrls = await page.evaluate(async () => {
    if (!("caches" in window)) return [];
    const urls: string[] = [];
    for (const cacheName of await caches.keys()) {
      const cache = await caches.open(cacheName);
      for (const request of await cache.keys()) urls.push(request.url);
    }
    return urls;
  });
  expect(
    cachedUrls.filter((url) =>
      /\/api\/|\/document-batches|\/document-import-sources/.test(url),
    ),
  ).toEqual([]);
  for (const marker of sensitiveMarkers) {
    expect(cachedUrls.join("\n")).not.toContain(marker);
  }
  expect(await page.locator("body").textContent()).not.toContain(PASSWORD);
});
