import { expect, test, type Page, type Route } from "@playwright/test";

import { mockAuthenticatedSession } from "./support/mockApi";

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:4173";
const MEMBER_ID = "00000000-0000-4000-8000-000000000001";
const POLICY_ID = "00000000-0000-4000-8000-000000000010";
const RIDER_ID = "00000000-0000-4000-8000-000000000011";
const PARTY_ID = "00000000-0000-4000-8000-000000000012";
const POLICY_DOCUMENT_ID = "00000000-0000-4000-8000-000000000020";
const TERMS_DOCUMENT_ID = "00000000-0000-4000-8000-000000000021";
const POLICY_EVIDENCE_ID = "00000000-0000-4000-8000-000000000030";
const RIDER_EVIDENCE_ID = "00000000-0000-4000-8000-000000000031";
const TERMS_EVIDENCE_ID = "00000000-0000-4000-8000-000000000032";
const REVIEW_ITEM_ID = "00000000-0000-4000-8000-000000000040";
const CANDIDATE_VERSION_ID = "00000000-0000-4000-8000-000000000041";

const BOUNDED_TERMS_EXCERPT =
  "Synthetic terms mention; policy Evidence is required before enrollment.";

type JsonObject = Record<string, unknown>;

interface SyntheticState {
  issueCode: "TERMS_ONLY_RIDER" | "NOT_ENROLLED" | "LOW_CONFIDENCE";
  confirmBodies: JsonObject[];
  confirmed: boolean;
  externalRequests: string[];
}

function evidenceResponse(
  evidenceId: string,
  documentVersionId: string,
  physicalPage: number,
  hashCharacter: string,
): JsonObject {
  return {
    bbox: [0.1, 0.2, 0.8, 0.3],
    content_sha256: hashCharacter.repeat(64),
    document_version_id: documentVersionId,
    evidence_id: evidenceId,
    physical_page: physicalPage,
    review_state: "AI_VERIFIED",
  };
}

function termsEvidence(): JsonObject {
  return {
    bbox: [0.1, 0.2, 0.8, 0.3],
    bounded_excerpt: BOUNDED_TERMS_EXCERPT,
    document_label: "Sample Terms Edition",
    document_version_id: TERMS_DOCUMENT_ID,
    evidence_id: TERMS_EVIDENCE_ID,
    page: 14,
  };
}

function policyResponse(): JsonObject {
  return {
    contract_date: "2026-01-01",
    coverage_end_date: "2026-12-31",
    coverage_start_date: "2026-01-01",
    deleted: false,
    id: POLICY_ID,
    insurer_display: "Synthetic Mutual",
    insurer_key: "synthetic-mutual",
    parties: [
      {
        effective_from: "2026-01-01",
        effective_to: null,
        evidence: evidenceResponse(
          POLICY_EVIDENCE_ID,
          POLICY_DOCUMENT_ID,
          1,
          "a",
        ),
        family_member_id: MEMBER_ID,
        id: PARTY_ID,
        role: "primary_insured",
        version: 1,
      },
    ],
    product_display: "Sample Policy",
    product_key: "sample-policy",
    source_document_version_id: POLICY_DOCUMENT_ID,
    source_evidence: evidenceResponse(
      POLICY_EVIDENCE_ID,
      POLICY_DOCUMENT_ID,
      1,
      "a",
    ),
    status: "active",
    status_evidence: null,
    version: 1,
  };
}

function riderResponse(): JsonObject {
  return {
    benefit_type: "fixed",
    coverage_end_date: "2026-12-31",
    coverage_start_date: "2026-01-01",
    currency: "USD",
    display_name: "Synthetic Accident Rider",
    id: RIDER_ID,
    insured_amount: "1000.00",
    normalized_key: "synthetic-accident-rider",
    policy_contract_id: POLICY_ID,
    renewable: false,
    source_evidence: evidenceResponse(
      RIDER_EVIDENCE_ID,
      POLICY_DOCUMENT_ID,
      2,
      "b",
    ),
    status: "active",
    status_evidence: null,
    version: 1,
  };
}

function reviewItem(state: SyntheticState): JsonObject {
  const source =
    state.issueCode === "TERMS_ONLY_RIDER"
      ? termsEvidence()
      : {
          ...termsEvidence(),
          document_label: "Sample Policy",
          document_version_id: POLICY_DOCUMENT_ID,
          evidence_id: POLICY_EVIDENCE_ID,
          page: 2,
          bounded_excerpt: "Synthetic enrolled rider source for review.",
        };
  return {
    aggregate_id: POLICY_ID,
    candidate_kind: "rider",
    candidate_version_id: CANDIDATE_VERSION_ID,
    evidence: [source],
    expected_version: state.confirmed ? 2 : 1,
    fields: [
      {
        evidence_ids: [source.evidence_id],
        field_id: "rider_name",
        value:
          state.issueCode === "TERMS_ONLY_RIDER"
            ? "Terms-only Rider"
            : "Review Rider",
      },
      {
        evidence_ids: [source.evidence_id],
        field_id: "rider_key",
        value:
          state.issueCode === "TERMS_ONLY_RIDER"
            ? "terms-only-rider"
            : "review-rider",
      },
      {
        evidence_ids: [source.evidence_id],
        field_id: "benefit_type",
        value: "fixed",
      },
      {
        evidence_ids: [source.evidence_id],
        field_id: "rider_status",
        value: "active",
      },
    ],
    issues: state.confirmed
      ? []
      : [{ code: state.issueCode, field_id: "rider_name" }],
    review_item_id: REVIEW_ITEM_ID,
    status: state.confirmed ? "USER_CONFIRMED" : "NEEDS_REVIEW",
  };
}

function parseRequestBody(route: Route): JsonObject {
  const body = route.request().postData();
  if (!body) {
    return {};
  }

  const parsed: unknown = JSON.parse(body);
  return typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)
    ? (parsed as JsonObject)
    : {};
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

async function installStorageWriteSpy(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const writes = {
      indexedDB: 0,
      localStorage: 0,
      sessionStorage: 0,
    };

    Object.defineProperty(window, "__familyCareStorageWrites", {
      configurable: true,
      value: writes,
    });

    const originalSetItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function setItem(key, value) {
      if (this === window.localStorage) {
        writes.localStorage += 1;
      } else if (this === window.sessionStorage) {
        writes.sessionStorage += 1;
      }
      return originalSetItem.call(this, key, value);
    };

    const originalRemoveItem = Storage.prototype.removeItem;
    Storage.prototype.removeItem = function removeItem(key) {
      if (this === window.localStorage) {
        writes.localStorage += 1;
      } else if (this === window.sessionStorage) {
        writes.sessionStorage += 1;
      }
      return originalRemoveItem.call(this, key);
    };

    const originalClear = Storage.prototype.clear;
    Storage.prototype.clear = function clear() {
      if (this === window.localStorage) {
        writes.localStorage += 1;
      } else if (this === window.sessionStorage) {
        writes.sessionStorage += 1;
      }
      return originalClear.call(this);
    };

    if (typeof indexedDB !== "undefined") {
      const originalOpen = IDBFactory.prototype.open;
      IDBFactory.prototype.open = function open(name, version) {
        writes.indexedDB += 1;
        return originalOpen.call(this, name, version);
      };

      const originalDeleteDatabase = IDBFactory.prototype.deleteDatabase;
      IDBFactory.prototype.deleteDatabase = function deleteDatabase(name) {
        writes.indexedDB += 1;
        return originalDeleteDatabase.call(this, name);
      };
    }
  });
}

async function installSyntheticApi(
  page: Page,
  issueCode: SyntheticState["issueCode"],
): Promise<SyntheticState> {
  const state: SyntheticState = {
    issueCode,
    confirmBodies: [],
    confirmed: false,
    externalRequests: [],
  };
  const expectedOrigin = new URL(BASE_URL).origin;

  page.on("request", (request) => {
    const requestUrl = new URL(request.url());
    if (
      (requestUrl.protocol === "http:" || requestUrl.protocol === "https:") &&
      requestUrl.origin !== expectedOrigin
    ) {
      state.externalRequests.push(request.url());
    }
  });

  await page.route("**/*", async (route) => {
    const requestUrl = new URL(route.request().url());
    if (
      (requestUrl.protocol === "http:" || requestUrl.protocol === "https:") &&
      requestUrl.origin !== expectedOrigin
    ) {
      await route.abort("blockedbyclient");
      return;
    }
    await route.continue();
  });

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const requestUrl = new URL(request.url());
    const method = request.method();
    const path = requestUrl.pathname;

    if (method === "GET" && path === "/api/v1/family-members") {
      await fulfillJson(route, [
        {
          deleted: false,
          display_name: "Family Member A",
          id: MEMBER_ID,
          internal_alias: "family-member-a",
          version: 1,
        },
      ]);
      return;
    }

    if (method === "GET" && path === `/api/v1/family-members/${MEMBER_ID}`) {
      await fulfillJson(route, {
        deleted: false,
        display_name: "Family Member A",
        id: MEMBER_ID,
        internal_alias: "family-member-a",
        version: 1,
      });
      return;
    }

    if (method === "GET" && path === "/api/v1/policies") {
      await fulfillJson(route, [policyResponse()]);
      return;
    }

    if (method === "GET" && path === `/api/v1/policies/${POLICY_ID}`) {
      await fulfillJson(route, policyResponse());
      return;
    }

    if (method === "GET" && path === `/api/v1/policies/${POLICY_ID}/riders`) {
      await fulfillJson(route, [riderResponse()]);
      return;
    }

    if (method === "GET" && path === "/api/v1/review-items") {
      const status = requestUrl.searchParams.get("status") ?? "NEEDS_REVIEW";
      if (status === "NEEDS_REVIEW") {
        await fulfillJson(route, state.confirmed ? [] : [reviewItem(state)]);
      } else if (status === "USER_CONFIRMED") {
        await fulfillJson(route, state.confirmed ? [reviewItem(state)] : []);
      } else {
        await fulfillJson(route, [reviewItem(state)]);
      }
      return;
    }

    if (method === "GET" && path === `/api/v1/review-items/${REVIEW_ITEM_ID}`) {
      await fulfillJson(route, reviewItem(state));
      return;
    }

    if (
      method === "POST" &&
      path === `/api/v1/review-items/${REVIEW_ITEM_ID}/confirm`
    ) {
      state.confirmBodies.push(parseRequestBody(route));
      state.confirmed = true;
      await fulfillJson(route, reviewItem(state));
      return;
    }

    await fulfillJson(
      route,
      {
        error_code: "SYNTHETIC_ROUTE_NOT_FOUND",
        message: "Synthetic E2E route not configured",
      },
      404,
    );
  });

  return state;
}

async function openLedger(page: Page): Promise<void> {
  await page.goto(`/app/members/${MEMBER_ID}/ledger`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByText("Sample Policy", { exact: true })).toBeVisible();
}

async function setupLedger(
  page: Page,
  issueCode: SyntheticState["issueCode"] = "TERMS_ONLY_RIDER",
): Promise<SyntheticState> {
  await installStorageWriteSpy(page);
  const state = await installSyntheticApi(page, issueCode);
  await mockAuthenticatedSession(page);
  await openLedger(page);
  return state;
}

async function expectNoExternalRequests(state: SyntheticState): Promise<void> {
  expect(state.externalRequests, state.externalRequests.join("\n")).toEqual([]);
}

test.describe("synthetic policy ledger review", () => {
  test("keeps terms-only candidates out of enrolled Riders at 320px", async ({
    page,
  }) => {
    const state = await setupLedger(page);

    await page.setViewportSize({ width: 320, height: 720 });
    await expect(
      page.getByText("Synthetic Accident Rider", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Terms-only Rider", { exact: true }),
    ).toHaveCount(0);

    const dimensions = await page.evaluate(() => ({
      bodyScrollWidth: document.body.scrollWidth,
      documentScrollWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
    }));
    expect(dimensions.documentScrollWidth).toBeLessThanOrEqual(
      dimensions.viewportWidth,
    );
    expect(dimensions.bodyScrollWidth).toBeLessThanOrEqual(
      dimensions.viewportWidth,
    );
    await expectNoExternalRequests(state);
  });

  test("opens Evidence with bounded text, closes by Escape, and restores focus", async ({
    page,
  }) => {
    const state = await setupLedger(page);
    await page.getByRole("button", { name: "검토 필요 항목 보기" }).click();
    const opener = page.getByRole("button", { name: "후보 검토" });

    await opener.focus();
    await page.keyboard.press("Enter");

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAttribute("aria-modal", "true");
    await expect(
      dialog.getByText("TERMS_ONLY_RIDER", { exact: true }),
    ).toBeVisible();

    const excerpt = dialog.getByText(BOUNDED_TERMS_EXCERPT, { exact: true });
    await expect(excerpt).toBeVisible();
    expect((await excerpt.textContent())?.length ?? 0).toBeLessThanOrEqual(240);

    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
    await expectNoExternalRequests(state);
  });

  for (const issue of ["TERMS_ONLY_RIDER", "NOT_ENROLLED"] as const) {
    test(`blocks confirmation of an excluded enrollment: ${issue}`, async ({
      page,
    }) => {
      const state = await setupLedger(page, issue);
      await page.getByRole("button", { name: "검토 필요 항목 보기" }).click();
      await page.getByRole("button", { name: "후보 검토" }).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog.getByRole("button", { name: "확인" })).toBeDisabled();
      await page.keyboard.press("Escape");
      await expect(dialog).toBeHidden();
      expect(state.confirmBodies).toHaveLength(0);
      await expect(
        page.getByText("Synthetic Accident Rider", { exact: true }),
      ).toHaveCount(1);
      await expectNoExternalRequests(state);
    });
  }

  test("does not write server state to Web Storage or IndexedDB", async ({
    page,
  }) => {
    const state = await setupLedger(page, "LOW_CONFIDENCE");
    await page.getByRole("button", { name: "검토 필요 항목 보기" }).click();
    await page.getByRole("button", { name: "후보 검토" }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "확인" })
      .click();

    await expect.poll(() => state.confirmBodies).toHaveLength(1);
    const writes = await page.evaluate(() => {
      const value = (
        window as typeof window & {
          __familyCareStorageWrites?: {
            indexedDB: number;
            localStorage: number;
            sessionStorage: number;
          };
        }
      ).__familyCareStorageWrites;
      return value ?? { indexedDB: -1, localStorage: -1, sessionStorage: -1 };
    });

    expect(writes).toEqual({
      indexedDB: 0,
      localStorage: 0,
      sessionStorage: 0,
    });
    await expectNoExternalRequests(state);
  });
});

test("reopens automatic contract identity at 320px without persistent storage", async ({
  page,
}) => {
  await installStorageWriteSpy(page);
  const state = await installSyntheticApi(page, "LOW_CONFIDENCE");
  const knowledgeId = "00000000-0000-4000-8000-000000002002";
  const requests: JsonObject[] = [];
  let reopened = false;
  await page.route("**/insurance-reconciliation", async (route) => {
    await fulfillJson(route, {
      schema_version: "1",
      member_id: MEMBER_ID,
      knowledge_run_id: "00000000-0000-4000-8000-000000002003",
      generated_at: "2026-09-07T01:00:00Z",
      summary: {
        total_contracts: 1,
        evidence_ready_contracts: 0,
        documents_pending_contracts: reopened ? 0 : 1,
        link_review_required_contracts: reopened ? 1 : 0,
        conflict_contracts: 0,
        orphan_operational_contracts: 0,
        unresolved_unreadable_sources: 0,
      },
      contracts: [
        {
          knowledge_contract_id: knowledgeId,
          insurer_display: "Sample Insurer",
          product_display: "Sample Knowledge Plan",
          certificate_decision: "MATCH",
          current_status: "unknown",
          reconciliation_state: reopened
            ? "LINK_REVIEW_REQUIRED"
            : "DOCUMENTS_PENDING",
          operational_link: {
            id: null,
            policy_contract_id: reopened ? null : POLICY_ID,
            decision: reopened ? "UNKNOWN" : "MATCH",
            conflict: false,
            authority: reopened
              ? "USER_CONFIRMED_OPERATIONAL_IDENTITY"
              : "PROGRAM_VERIFIED_SOURCE_IDENTITY",
            reason_code: reopened
              ? "USER_REOPENED_OPERATIONAL_REVIEW"
              : "UNIQUE_ENROLLED_NAME_ON_BOUND_PAGE",
            confirmed_at: null,
          },
          document_readiness: reopened
            ? null
            : {
                policy_contract_id: POLICY_ID,
                completeness: "CERTIFICATE_ONLY",
                has_product_explanation: false,
                has_application: false,
              },
        },
      ],
      orphan_operational_contracts: [],
      unresolved_sources: [],
    });
  });
  await page.route("**/operational-link", async (route) => {
    requests.push(parseRequestBody(route));
    reopened = true;
    await fulfillJson(route, {
      schema_version: "1",
      id: CANDIDATE_VERSION_ID,
      knowledge_contract_id: knowledgeId,
      policy_contract_id: null,
      decision: "UNKNOWN",
      conflict: false,
      authority: "USER_CONFIRMED_OPERATIONAL_IDENTITY",
      reason_code: "USER_REOPENED_OPERATIONAL_REVIEW",
      confirmed_at: "2026-09-07T01:00:00Z",
    });
  });
  await mockAuthenticatedSession(page);
  await page.setViewportSize({ width: 320, height: 720 });
  await openLedger(page);
  await expect(page.getByText("원본 근거로 자동 연결")).toBeVisible();
  const button = page.getByRole("button", { name: "앱 계약 연결 다시 검토" });
  await button.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("원본 근거로 자동 연결")).toHaveCount(0);
  expect(requests).toEqual([
    {
      decision: "UNKNOWN",
      conflict: false,
      policy_contract_id: null,
      expected_current_link_id: null,
      reason_code: "USER_REOPENED_OPERATIONAL_REVIEW",
    },
  ]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  expect(
    await page.evaluate(
      () =>
        (
          window as typeof window & {
            __familyCareStorageWrites?: {
              localStorage: number;
              sessionStorage: number;
              indexedDB: number;
            };
          }
        ).__familyCareStorageWrites,
    ),
  ).toEqual({ localStorage: 0, sessionStorage: 0, indexedDB: 0 });
  await expectNoExternalRequests(state);
});
