import { expect, test } from "@playwright/test";
import type {
  GuidanceCandidate,
  GuidanceReviewJob,
} from "../src/api/generated";
import {
  installStorageWriteSpy,
  mockAuthenticatedSession,
  mockLocalGuidanceClaimApi,
} from "./support/mockApi";

function reviewJob(
  eventId: string,
  runId: string,
  state: GuidanceReviewJob["state"],
): GuidanceReviewJob {
  const candidate: GuidanceCandidate = {
    ref: {
      kind: "PRIVATE_KNOWLEDGE_COVERAGE",
      contract_id: "synthetic-policy-review",
      coverage_id: "synthetic-coverage-review",
    },
    contract_label: "Sample Reviewed Policy",
    coverage_label: "Sample Reviewed Coverage",
    benefit_kind: "FIXED",
    condition_result: "MATCH",
    group: "PRIMARY",
    freshness: "DOCUMENT_CONTINUITY",
    reason_codes: [],
    estimate: {
      kind: "POINT",
      amount: "120000",
      currency: "KRW",
      reason_code: "DOCUMENT_BASED_ESTIMATE",
    },
  };
  return {
    id: "synthetic-review-001",
    medical_event_id: eventId,
    decision_run_id: runId,
    event_version: 1,
    created_at: new Date().toISOString(),
    state,
    http_attempts: 1,
    result:
      state === "partial"
        ? {
            source_digest: "synthetic-source-digest",
            reason_codes: [],
            scope: {
              complete: false,
              total_coverages: 2,
              indexed_coverages: 1,
              total_packets: 3,
              reviewed_packets: 1,
              unreviewed_packets: 1,
              omitted_packets: 1,
            },
            findings: [
              {
                coverage: candidate.ref,
                kind: "CONFLICT",
                status: "OPINION",
                reason_codes: [],
              },
            ],
            differences: [
              {
                coverage: candidate.ref,
                change: "ADDED",
                before: null,
                after: candidate,
              },
            ],
            guidance: {
              event_date: "2026-09-01",
              medical_event_id: eventId,
              event_version: 1,
              family_member_id: "synthetic-member-001",
              candidates: [candidate],
              outcome: "CANDIDATES",
              support: {
                total_coverages: 2,
                evaluated_coverages: 1,
                unsupported_coverages: 1,
              },
              versions: {},
            },
          }
        : null,
  };
}

test("optional review exposes partial opinions and program changes with keyboard at 320px while original claims keep their run", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await installStorageWriteSpy(page);
  const mock = await mockLocalGuidanceClaimApi(page);
  await mockAuthenticatedSession(page);
  const requests: { method: string; body: unknown }[] = [];
  await page.route(/\/api\/v1\/.*guidance-reviews/, async (route) => {
    const request = route.request();
    requests.push({ method: request.method(), body: request.postDataJSON() });
    await route.fulfill({
      json: reviewJob(
        mock.eventId,
        mock.runId,
        request.method() === "POST" ? "queued" : "partial",
      ),
      headers: { "Cache-Control": "no-store" },
    });
  });
  await page.goto(`/app/events/${mock.eventId}/result/1`);
  const review = page.getByRole("region", {
    name: "AI 선택 검수",
    exact: true,
  });
  const start = review.getByRole("button", { name: "AI 선택 검수 시작" });
  await expect(start).toBeVisible();
  expect(requests).toEqual([]);
  await start.focus();
  await page.keyboard.press("Enter");
  await expect(
    review.getByText("일부 자료의 검수가 완료되었습니다."),
  ).toBeVisible();
  expect(requests[0]).toEqual({
    method: "POST",
    body: { decision_run_id: mock.runId, expected_event_version: 1 },
  });
  expect(requests.filter((request) => request.method === "POST")).toHaveLength(
    1,
  );
  await expect(review).toContainText("미검수 원문 묶음 1개");
  const opinion = review.getByText("AI 검수 의견과 근거", { exact: true });
  await opinion.focus();
  await page.keyboard.press("Enter");
  await expect(
    review.getByText("AI 의견 · 프로그램 결과에 미반영"),
  ).toBeVisible();
  const details = review.getByText("프로그램 재평가 결과와 변경점", {
    exact: true,
  });
  await details.focus();
  await page.keyboard.press("Enter");
  await expect(
    review.getByText("추가된 후보 · Sample Reviewed Coverage"),
  ).toBeVisible();
  await expect(review.getByRole("button", { name: /청구 준비/ })).toHaveCount(
    0,
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(await page.evaluate(() => window.__familyCareStorageWrites)).toEqual({
    indexedDB: 0,
    localStorage: 0,
    sessionStorage: 0,
  });
  await page
    .getByRole("button", { name: "Sample Planned Coverage 1 청구 준비" })
    .click();
  await expect(page).toHaveURL(new RegExp(`/app/claims/${mock.claimId}$`));
  expect(mock.state.claimCreateBodies).toEqual([
    {
      guidance: {
        run_id: mock.runId,
        expected_event_version: 1,
        coverage: mock.coverage,
      },
    },
  ]);
});

test("review failure preserves local candidates and a retried running review can be cancelled", async ({
  page,
}) => {
  const mock = await mockLocalGuidanceClaimApi(page);
  await mockAuthenticatedSession(page);
  let creates = 0;
  let reads = 0;
  let cancelled = false;
  await page.route(/\/api\/v1\/.*guidance-reviews/, async (route) => {
    const request = route.request();
    if (request.url().endsWith("/cancel")) {
      cancelled = true;
      await route.fulfill({
        json: reviewJob(mock.eventId, mock.runId, "cancelled"),
      });
    } else if (request.method() === "POST" && ++creates === 1) {
      await route.fulfill({
        status: 503,
        json: {
          error_code: "RESOURCE_LIMIT_EXCEEDED",
          message: "Synthetic private backend error",
        },
      });
    } else {
      if (request.method() === "GET") reads += 1;
      await route.fulfill({
        json: reviewJob(mock.eventId, mock.runId, "running"),
      });
    }
  });
  await page.goto(`/app/events/${mock.eventId}/result/1`);
  const review = page.getByRole("region", {
    name: "AI 선택 검수",
    exact: true,
  });
  await review.getByRole("button", { name: "AI 선택 검수 시작" }).click();
  await expect(review.getByRole("alert")).toContainText(
    "검수를 지금 시작할 수 없습니다",
  );
  await expect(
    page.getByRole("article", { name: "Sample Planned Coverage 1" }),
  ).toBeVisible();
  await expect(review).not.toContainText("Synthetic private backend error");
  await review.getByRole("button", { name: "AI 선택 검수 시작" }).click();
  await expect(review).toContainText("이미 전송된 요청의 비용");
  await review.getByRole("button", { name: "검수 취소" }).click();
  await expect(review.getByText("검수를 취소했습니다.")).toBeVisible();
  expect(cancelled).toBe(true);
  const readsAtCancellation = reads;
  await page.clock.install();
  await page.clock.fastForward(5000);
  expect(reads).toBe(readsAtCancellation);
});
