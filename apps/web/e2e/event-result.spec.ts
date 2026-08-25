import { expect, test } from "@playwright/test";

import {
  installStorageWriteSpy,
  mockAuthenticatedSession,
  mockSyntheticEventApi,
} from "./support/mockApi";

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
