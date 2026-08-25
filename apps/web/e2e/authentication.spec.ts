import { expect, test } from "@playwright/test";

import {
  installStorageWriteSpy,
  mockAuthenticatedSession,
} from "./support/mockApi";

const SYNTHETIC_PASSWORD = "synthetic-auth-secret-a";

test("logs in at 320px through the cookie session boundary", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await installStorageWriteSpy(page);
  const auth = await mockAuthenticatedSession(page, {
    authenticated: false,
  });

  await page.goto("/login", { waitUntil: "domcontentloaded" });
  await page.getByLabel("사용자 이름").fill("admin-a");
  await page.getByLabel("비밀번호").fill(SYNTHETIC_PASSWORD);
  await page.getByRole("button", { name: "로그인" }).click();

  await expect(page).toHaveURL(/\/app\/ledger$/);
  await expect(
    page.getByRole("navigation", { name: "주요 화면" }),
  ).toBeVisible();
  expect(await page.context().cookies()).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        httpOnly: true,
        name: "familycare_session",
        sameSite: "Strict",
      }),
    ]),
  );
  expect(auth.loginRequests).toBe(1);
  expect(await page.getByLabel("비밀번호").count()).toBe(0);
  expect(await page.evaluate(() => window.__familyCareStorageWrites)).toEqual({
    indexedDB: 0,
    localStorage: 0,
    sessionStorage: 0,
  });
});

test("lists device sessions and reauthenticates before revoking another session", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await installStorageWriteSpy(page);
  const auth = await mockAuthenticatedSession(page, {
    needsReauthentication: true,
  });

  await page.goto("/app/settings/sessions", { waitUntil: "domcontentloaded" });
  await expect(
    page.getByRole("heading", { name: "기기 세션", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Synthetic Other Device", { exact: true }),
  ).toBeVisible();

  await page.getByRole("button", { name: "이 세션 폐기" }).click();
  const dialog = page.getByRole("dialog", { name: "다시 인증이 필요합니다" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("비밀번호").fill(SYNTHETIC_PASSWORD);
  await dialog.getByRole("button", { name: "확인" }).click();

  await expect(
    page.getByText("Synthetic Other Device", { exact: true }),
  ).toHaveCount(0);
  expect(auth.requestOrder).toEqual([
    "me",
    "csrf",
    "sessions",
    "reauthenticate",
    "revoke",
    "sessions",
  ]);
  expect(await page.evaluate(() => window.__familyCareStorageWrites)).toEqual({
    indexedDB: 0,
    localStorage: 0,
    sessionStorage: 0,
  });
});
