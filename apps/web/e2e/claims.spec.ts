import { expect, test } from "@playwright/test";

import type {
  BenefitCalculationsResponse,
  ClaimCaseResponse,
  CoverageDecisionResponse,
  MedicalEventResponse,
} from "../src/api/generated";
import { installStorageWriteSpy } from "./support/mockApi";

const EVENT_ID = "00000000-0000-4000-8000-000000000201";
const RIDER_ID = "00000000-0000-4000-8000-000000000701";
const CLAIM_ID = "00000000-0000-4000-8000-000000000801";
const ITEM_ID = "00000000-0000-4000-8000-000000000901";
const POLICY_ID = "00000000-0000-4000-8000-000000001001";

const EVENT: MedicalEventResponse = {
  deleted: false,
  event_date: "2026-08-25",
  facts: {},
  family_member_id: "00000000-0000-4000-8000-000000000101",
  id: EVENT_ID,
  mode: "post_treatment",
  situation: "Synthetic treatment event",
  version: 2,
  visit_date: "2026-08-26",
};

const RESULT: CoverageDecisionResponse = {
  candidates: [
    {
      aggregate_result: "MATCH",
      candidate_id: "00000000-0000-4000-8000-000000000601",
      hold_reason_codes: [],
      questions: [],
      required_match_count: 1,
      required_no_match_count: 0,
      required_unknown_count: 0,
      rider_id: RIDER_ID,
      rider_label: "Sample Rider A",
      rider_type: "fixed",
    },
  ],
  engine_version: "synthetic-decision-engine-v1",
  evaluations: [],
  event_version: 2,
  medical_event_id: EVENT_ID,
  policy_snapshot_at: "2026-08-25T09:00:00Z",
  rule_set_version: "synthetic-rule-set-v1",
  run_id: "00000000-0000-4000-8000-000000000501",
  schema_version: "1",
  stale: false,
};

const CALCULATIONS: BenefitCalculationsResponse = {
  calculations: [],
  schema_version: "1",
};

function claim(
  status: ClaimCaseResponse["status"] = "preparing",
): ClaimCaseResponse {
  return {
    allowed_transitions:
      status === "preparing"
        ? ["submitted"]
        : status === "submitted"
          ? ["paid", "partially_paid", "denied"]
          : [],
    checklist: [
      {
        conditional: false,
        document_kind: "claim_form",
        id: ITEM_ID,
        note_code: null,
        prepared: false,
        required: true,
        requirement_code: "CLAIM_FORM_REQUIRED",
        source_evidence_id: null,
        source_rule_version_id: null,
        version: 1,
      },
    ],
    claimed_amount: null,
    currency: null,
    deleted: false,
    family_member_id: EVENT.family_member_id,
    id: CLAIM_ID,
    insurer_key: "synthetic-insurer",
    medical_event_id: EVENT_ID,
    outcome_reason_code: null,
    paid_amount: null,
    policy_contract_id: POLICY_ID,
    receipt_number: null,
    schema_version: "1",
    snapshot: {
      calculation: { calculation_ids: [], statuses: [], versions: [] },
      candidate: {
        aggregate_results: ["MATCH"],
        candidate_ids: [],
        rider_ids: [RIDER_ID],
      },
      evidence: { content_sha256: [], evidence_ids: [] },
      policy: {
        captured_at: "2026-08-25T09:00:00Z",
        policy_contract_id: POLICY_ID,
        rider_ids: [RIDER_ID],
        status_codes: ["active"],
      },
      rules: { evaluator_versions: [], reason_codes: [], rule_version_ids: [] },
      snapshot_sha256: "a".repeat(64),
      snapshot_version: 1,
    },
    status,
    status_events: [],
    submitted_at: null,
    version: status === "preparing" ? 1 : 2,
  };
}

test("starts a server-created claim and records checklist/status without browser persistence", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await installStorageWriteSpy(page);
  let current = claim();
  const forbiddenBodies: string[] = [];
  let createBody: unknown;

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const body = request.postData() ?? "";
    if (/file|path|ocr|document_text|medical_text|receipt_number/.test(body)) {
      forbiddenBodies.push(body);
    }
    if (
      request.method() === "GET" &&
      url.pathname === `/api/v1/medical-events/${EVENT_ID}`
    ) {
      await route.fulfill({
        json: EVENT,
        headers: { "Cache-Control": "no-store" },
      });
      return;
    }
    if (
      request.method() === "GET" &&
      url.pathname === `/api/v1/medical-events/${EVENT_ID}/results/2`
    ) {
      await route.fulfill({
        json: RESULT,
        headers: { "Cache-Control": "no-store" },
      });
      return;
    }
    if (
      request.method() === "GET" &&
      url.pathname === `/api/v1/medical-events/${EVENT_ID}/calculations`
    ) {
      await route.fulfill({
        json: CALCULATIONS,
        headers: { "Cache-Control": "no-store" },
      });
      return;
    }
    if (
      request.method() === "POST" &&
      url.pathname === `/api/v1/medical-events/${EVENT_ID}/claims`
    ) {
      createBody = JSON.parse(body);
      await route.fulfill({
        status: 201,
        json: current,
        headers: { "Cache-Control": "no-store" },
      });
      return;
    }
    if (
      request.method() === "GET" &&
      url.pathname === `/api/v1/claims/${CLAIM_ID}`
    ) {
      await route.fulfill({
        json: current,
        headers: { "Cache-Control": "no-store" },
      });
      return;
    }
    if (
      request.method() === "PATCH" &&
      url.pathname === `/api/v1/claims/${CLAIM_ID}/checklist/${ITEM_ID}`
    ) {
      current = {
        ...current,
        checklist: [{ ...current.checklist[0], prepared: true, version: 2 }],
      };
      await route.fulfill({
        json: current,
        headers: { "Cache-Control": "no-store" },
      });
      return;
    }
    if (
      request.method() === "POST" &&
      url.pathname === `/api/v1/claims/${CLAIM_ID}/transitions`
    ) {
      current = {
        ...current,
        ...claim("submitted"),
        status_events: [
          {
            from_status: "preparing",
            to_status: "submitted",
            occurred_at: "2026-08-26T09:00:00Z",
            reason_code: null,
          },
        ],
      };
      await route.fulfill({
        json: current,
        headers: { "Cache-Control": "no-store" },
      });
      return;
    }
    await route.fulfill({
      status: 404,
      json: { error_code: "CLAIM_NOT_FOUND", message: "Synthetic missing" },
    });
  });

  await page.goto(`/app/events/${EVENT_ID}/result/2`);
  await page
    .getByRole("button", { name: "Sample Rider A 청구 검토 시작" })
    .click();
  await expect(page).toHaveURL(new RegExp(`/app/claims/${CLAIM_ID}$`));
  await expect(page.getByRole("heading", { name: "준비 항목" })).toBeVisible();

  await page.getByRole("checkbox", { name: "청구서 준비 완료" }).click();
  await expect(
    page.getByRole("checkbox", { name: "청구서 준비 완료" }),
  ).toBeChecked();
  await page.getByRole("button", { name: "제출 기록" }).click();
  await expect(page.getByText("제출 기록").first()).toBeVisible();

  expect(createBody).toEqual({ rider_id: RIDER_ID });
  expect(forbiddenBodies).toEqual([]);
  expect(await page.evaluate(() => window.__familyCareStorageWrites)).toEqual({
    indexedDB: 0,
    localStorage: 0,
    sessionStorage: 0,
  });
  const layout = await page.evaluate(() => ({
    innerWidth: window.innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.innerWidth);
});
