import { expect, test, type Page } from "@playwright/test";

import {
  installStorageWriteSpy,
  mockAuthenticatedSession,
  mockLocalGuidanceClaimApi,
  mockSyntheticEventApi,
} from "./support/mockApi";

async function expectMemoryOnlyResults(page: Page) {
  expect(await page.evaluate(() => window.__familyCareStorageWrites)).toEqual({
    indexedDB: 0,
    localStorage: 0,
    sessionStorage: 0,
  });
  const cached = await page.evaluate(async () => {
    if (!("caches" in window)) return [];
    if ("serviceWorker" in navigator) await navigator.serviceWorker.ready;
    const urls: string[] = [];
    for (const name of await caches.keys()) {
      for (const request of await (await caches.open(name)).keys())
        urls.push(request.url);
    }
    return urls;
  });
  expect(
    cached.filter((url) =>
      /\/api\/|\/documents\/|\/evidence\/|\/medical-events\/|\/results\/|\/claims\//.test(
        url,
      ),
    ),
  ).toEqual([]);
}

async function expectNarrowLayout(page: Page) {
  const size = await page.evaluate(() => ({
    width: innerWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  expect(size.scroll).toBeLessThanOrEqual(size.width);
}

test("opens planned subtotal and calculation disclosures with native Enter at 320px", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await installStorageWriteSpy(page);
  const mock = await mockLocalGuidanceClaimApi(page);
  await mockAuthenticatedSession(page);
  await page.goto(`/app/events/${mock.eventId}/result/1`);
  const actual = page.getByRole("region", {
    name: "현재 사건 기준 소계 · KRW",
  });
  const planned = page.getByRole("region", {
    name: "예정 치료 가정 1 소계 · KRW",
  });
  await expect(actual.getByText("300원", { exact: true })).toBeVisible();
  await expect(actual.getByText("부분 소계", { exact: true })).toBeVisible();
  await expect(planned.getByText("600원", { exact: true })).toBeVisible();
  await expect(planned).toContainText("입원 일수: 5일 가정");
  await expect(planned).toContainText("공통 한도나 중복 지급 감액");
  await expect(page.getByText("900원", { exact: true })).toHaveCount(0);
  const totalDetails = planned.locator("details");
  const totalSummary = planned.getByText("포함한 담보와 합산 전제", {
    exact: true,
  });
  await expect(totalDetails).not.toHaveAttribute("open");
  await totalSummary.focus();
  await expect(totalSummary).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(totalDetails).toHaveAttribute("open", "");
  await expect(
    totalDetails.getByText(/Sample Planned Coverage 1/).first(),
  ).toBeVisible();
  const card = page.getByRole("article", { name: "Sample Planned Coverage 1" });
  await expect(card.getByText("100원", { exact: true })).toBeVisible();
  await expect(card.getByText("300원", { exact: true })).toBeVisible();
  const traceSummary = card.getByText("계산 과정과 근거", { exact: true });
  await traceSummary.focus();
  await page.keyboard.press("Enter");
  await expect(card.locator("details")).toHaveAttribute("open", "");
  await expect(
    card.getByText("5일 − 2일 = 3일", { exact: false }),
  ).toBeVisible();
  await expect(
    card.getByText("100원 × 3일 = 300원", { exact: false }),
  ).toBeVisible();
  await expect(card).not.toContainText(
    /\/calculation|source_revision|[a-f]{64}/,
  );
  await expectNarrowLayout(page);
  await expectMemoryOnlyResults(page);
  expect(mock.state.analysisRequests).toBe(0);
  expect(mock.state.structureRequests).toBe(0);
  expect(mock.state.unexpectedAiRequests).toEqual([]);
  expect(mock.state.forbiddenRequests).toEqual([]);
});

test("prepares a conditional formula claim from saved selectors and clears its snapshot on session expiry", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await installStorageWriteSpy(page);
  const mock = await mockLocalGuidanceClaimApi(page, {
    expireClaimUpdates: true,
  });
  await mockAuthenticatedSession(page);
  await page.goto(`/app/events/${mock.eventId}/result/1`);
  const card = page.getByRole("article", { name: "Sample Planned Coverage 1" });
  await expect(
    card.getByText("가입금액 × (입원 일수 − 2)", { exact: true }),
  ).toBeVisible();
  await expectMemoryOnlyResults(page);
  const prepare = card.getByRole("button", {
    name: "Sample Planned Coverage 1 청구 준비",
  });
  await prepare.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/app/claims/${mock.claimId}$`));
  const saved = page.getByRole("region", { name: "청구 준비 때 저장한 안내" });
  await expect(saved).toBeVisible();
  await expect(
    saved.getByText("가입금액 × (입원 일수 − 2)", { exact: true }),
  ).toBeVisible();
  await expect(saved.getByText("300원", { exact: true })).toBeVisible();
  await expect(saved.getByText("100원", { exact: true })).toBeVisible();
  await expect(
    saved.getByRole("region", { name: "등록된 비용" }),
  ).toContainText("50,000원");
  await expect(page.getByText("실제 지급액", { exact: true })).toBeVisible();
  await expect(
    page.getByText("아직 기록되지 않았습니다.", { exact: true }),
  ).toBeVisible();
  const savedTrace = saved.getByText("계산 과정과 근거", { exact: true });
  await savedTrace.focus();
  await page.keyboard.press("Enter");
  await expect(
    saved.getByText("100원 × 3일 = 300원", { exact: false }),
  ).toBeVisible();
  expect(mock.state.claimCreateBodies).toEqual([
    {
      guidance: {
        run_id: mock.runId,
        expected_event_version: 1,
        coverage: mock.coverage,
      },
    },
  ]);
  expect(mock.state.claimReads).toBe(1);
  expect(mock.state.analysisRequests).toBe(0);
  expect(mock.state.structureRequests).toBe(0);
  expect(mock.state.unexpectedAiRequests).toEqual([]);
  expect(mock.state.forbiddenRequests).toEqual([]);
  await expectNarrowLayout(page);
  await expectMemoryOnlyResults(page);
  await page.getByRole("button", { name: "기록 저장", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByLabel("사용자 이름", { exact: true })).toBeVisible();
  await expect(saved).toHaveCount(0);
  await expect(
    page.getByText("Sample Planned Coverage 1", { exact: true }),
  ).toHaveCount(0);
  await expect(page.getByText("50,000원", { exact: true })).toHaveCount(0);
  expect(mock.state.claimUpdates).toBe(1);
  await expectMemoryOnlyResults(page);
});

test("retains stale local amounts while disabling claim creation at 320px", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await installStorageWriteSpy(page);
  const mock = await mockLocalGuidanceClaimApi(page, { stale: true });
  await mockAuthenticatedSession(page);
  await page.goto(`/app/events/${mock.eventId}/result/1`);
  await expect(
    page.getByRole("button", { name: "Sample Planned Coverage 1 청구 준비" }),
  ).toBeDisabled();
  await expect(
    page
      .getByRole("region", { name: "예정 치료 가정 1 소계 · KRW" })
      .getByText("600원", { exact: true }),
  ).toBeVisible();
  expect(mock.state.claimCreateBodies).toEqual([]);
  expect(mock.state.analysisRequests).toBe(0);
  expect(mock.state.structureRequests).toBe(0);
  expect(mock.state.unexpectedAiRequests).toEqual([]);
  await expectNarrowLayout(page);
  await expectMemoryOnlyResults(page);
});

test("shows document-based local guidance at 320px without automatic AI requests", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await installStorageWriteSpy(page);
  const mock = await mockSyntheticEventApi(page, { result: "local" });
  await mockAuthenticatedSession(page);
  await page.goto("/app/events/new?member=synthetic-member-a");
  await page
    .getByRole("textbox", { name: "현재 상황" })
    .fill("Synthetic situation");
  await page.getByRole("button", { name: "현재 후보 보기" }).click();
  await page.getByRole("button", { name: "결과 확인" }).click();
  await expect(page.getByRole("heading", { name: "주요 후보" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Sample Local Coverage" }),
  ).toBeVisible();
  await expect(page.getByText("100원", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "청구 검토 시작", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(/문서에 기록된 계약이 사건일까지 유지/),
  ).toBeVisible();
  expect(mock.structureRequests).toBe(0);
  expect(mock.analysisRequests).toBe(1);
  expect(mock.forbiddenRequests).toEqual([]);
  const size = await page.evaluate(() => ({
    width: innerWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  expect(size.scroll).toBeLessThanOrEqual(size.width);
  expect(await page.evaluate(() => window.__familyCareStorageWrites)).toEqual({
    indexedDB: 0,
    localStorage: 0,
    sessionStorage: 0,
  });
});

test("creates a minimal event and reaches action-first results at 320px", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await installStorageWriteSpy(page);
  const mock = await mockSyntheticEventApi(page);
  await mockAuthenticatedSession(page);

  await page.goto("/app/events/new?member=synthetic-member-a", {
    waitUntil: "domcontentloaded",
  });
  expect(
    await page.evaluate(async () => {
      if (!("serviceWorker" in navigator)) return false;
      return Boolean((await navigator.serviceWorker.ready).active);
    }),
  ).toBe(true);
  await page
    .getByRole("textbox", { name: "현재 상황" })
    .fill("Synthetic situation");
  await page.getByRole("button", { name: "현재 후보 보기" }).click();
  await expect(
    page.getByText("추가 확인 질문은 선택 사항입니다").first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "결과 확인" }).click();

  await expect(page.getByRole("heading", { name: "지금 할 일" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "청구 검토 대상" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "추가 확인 필요" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "조건 불일치" }),
  ).toBeVisible();

  const layout = await page.evaluate(() => ({
    innerWidth: window.innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.innerWidth);
  expect(await page.evaluate(() => document.activeElement?.tagName)).toBe("H1");
  expect(await page.evaluate(() => window.__familyCareStorageWrites)).toEqual({
    indexedDB: 0,
    localStorage: 0,
    sessionStorage: 0,
  });
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
      /\/api\/|\/documents\/|\/evidence\/|\/medical-events\/|\/results\/|\/claims\//.test(
        url,
      ),
    ),
  ).toEqual([]);
  expect(mock.forbiddenRequests).toEqual([]);
});

test("keeps manual analysis available when optional structuring fails", async ({
  page,
}) => {
  await installStorageWriteSpy(page);
  const mock = await mockSyntheticEventApi(page, { structuring: "failure" });
  await mockAuthenticatedSession(page);

  await page.goto("/app/events/new?member=synthetic-member-a");
  await page
    .getByRole("textbox", { name: "현재 상황" })
    .fill("Synthetic situation");
  await page.getByRole("button", { name: "현재 후보 보기" }).click();
  await page.getByRole("button", { name: "선택적으로 자동 구조화" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "직접 입력한 내용으로 계속할 수 있습니다",
  );
  await expect(page.getByRole("button", { name: "결과 확인" })).toBeEnabled();
  await page.getByRole("button", { name: "결과 확인" }).click();
  await expect(page.getByRole("heading", { name: "지금 할 일" })).toBeVisible();
  expect(mock.analysisRequests).toBe(1);
});

test("discloses bounded Evidence and returns focus after Escape", async ({
  page,
}) => {
  await installStorageWriteSpy(page);
  const mock = await mockSyntheticEventApi(page, {
    result: "partial_stale",
  });
  await mockAuthenticatedSession(page);

  await page.goto("/app/events/new?member=synthetic-member-a");
  await page
    .getByRole("textbox", { name: "현재 상황" })
    .fill("Synthetic situation");
  await page.getByRole("button", { name: "현재 후보 보기" }).click();
  await page.getByRole("button", { name: "결과 확인" }).click();
  await expect(page.getByRole("heading", { name: "지금 할 일" })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("다시 확인");
  await expect(
    page.getByRole("heading", { name: "추가 확인 필요" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /청구 검토 시작/ }),
  ).toHaveCount(0);

  const trigger = page.getByRole("button", { name: "근거 보기" }).first();
  await trigger.click();
  const drawer = page.getByRole("dialog", { name: "증권과 약관 근거" });
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText("페이지 3");
  await expect(drawer).toContainText("Synthetic bounded Evidence excerpt");
  await expect(drawer).not.toContainText("/private/");
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
  expect(mock.forbiddenRequests).toEqual([]);
});

test("validates post-treatment receipt lines without sending invalid amounts", async ({
  page,
}) => {
  await installStorageWriteSpy(page);
  const mock = await mockSyntheticEventApi(page);
  await mockAuthenticatedSession(page);

  await page.goto(
    "/app/events/new?member=synthetic-member-a&mode=post_treatment",
  );
  await page.getByRole("button", { name: "영수증 항목 추가" }).click();
  await page.getByRole("spinbutton", { name: "금액" }).fill("-1.00");
  await page.getByRole("button", { name: "항목 저장" }).click();
  await expect(page.getByRole("alert")).toContainText("0 이상");
  expect(mock.receiptLineRequests).toBe(0);

  await page.getByRole("spinbutton", { name: "금액" }).fill("1.00");
  await page.getByRole("textbox", { name: "통화" }).fill("USD");
  await page.getByRole("button", { name: "항목 저장" }).click();
  await expect(page.getByRole("alert")).toContainText("통화가 일치");
  expect(mock.receiptLineRequests).toBe(0);
});
