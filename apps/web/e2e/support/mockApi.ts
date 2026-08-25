import type { Page, Route } from "@playwright/test";

import type {
  BenefitCalculationsResponse,
  CoverageDecisionResponse,
  EvidenceDetailResponse,
  MedicalEventResponse,
  ReceiptLineCreateRequest,
  ReceiptLineResponse,
  ReceiptLineUpdateRequest,
  StructureAcceptedResponse,
  StructuringJobResponse,
} from "../../src/api/generated";

const MEMBER_ID = "00000000-0000-4000-8000-000000000101";
const EVENT_ID = "00000000-0000-4000-8000-000000000201";
const JOB_ID = "00000000-0000-4000-8000-000000000301";
const RESULT_ID = "00000000-0000-4000-8000-000000000401";
const EVIDENCE_ID = "00000000-0000-4000-8000-000000000501";
const DOCUMENT_VERSION_ID = "00000000-0000-4000-8000-000000000601";
const RIDER_MATCH_ID = "00000000-0000-4000-8000-000000000701";
const RIDER_UNKNOWN_ID = "00000000-0000-4000-8000-000000000702";
const RECEIPT_LINE_ID = "00000000-0000-4000-8000-000000001101";

type JsonObject = Record<string, unknown>;

export interface SyntheticEventApiOptions {
  result?: "complete" | "partial_stale";
  structuring?: "success" | "failure";
}

export interface SyntheticEventApiState {
  analysisRequests: number;
  forbiddenRequests: string[];
  receiptLineRequests: number;
  structureRequests: number;
  updateRequests: number;
}

declare global {
  interface Window {
    __familyCareStorageWrites?: {
      indexedDB: number;
      localStorage: number;
      sessionStorage: number;
    };
  }
}

const FORBIDDEN_REQUEST_FIELDS = [
  "archive_key",
  "document_text",
  "file_upload",
  "local_path",
  "ocr_text",
  "password",
  "source_path",
];

const SYNTHETIC_EVIDENCE = {
  bbox: [0.1, 0.2, 0.8, 0.3] as [number, number, number, number],
  bounded_excerpt: "Synthetic bounded Evidence excerpt",
  clause_label: "Synthetic clause 3",
  document_label: "Sample Policy Terms",
  document_version_id: DOCUMENT_VERSION_ID,
  evidence_id: EVIDENCE_ID,
  physical_page: 3,
  review_state: "AI_VERIFIED" as const,
  schema_version: "1" as const,
} satisfies EvidenceDetailResponse;

const SYNTHETIC_CALCULATIONS = {
  calculations: [],
  schema_version: "1",
} satisfies BenefitCalculationsResponse;

function currentEvent(
  situation: string,
  mode: MedicalEventResponse["mode"] = "pre_visit",
  version = 1,
  structuredFacts: MedicalEventResponse["structured_facts"] = [],
): MedicalEventResponse {
  return {
    deleted: false,
    event_date: null,
    facts: {},
    family_member_id: MEMBER_ID,
    id: EVENT_ID,
    mode,
    optional_questions: [
      { field_id: "treatment_kind", question_code: "treatment_kind" },
    ],
    situation,
    structured_facts: structuredFacts,
    version,
    visit_date: null,
  };
}

function resultResponse(
  eventVersion: number,
  resultMode: SyntheticEventApiOptions["result"],
): CoverageDecisionResponse {
  const partial = resultMode === "partial_stale";
  return {
    candidates: [
      {
        aggregate_result: "MATCH",
        candidate_id: RIDER_MATCH_ID,
        hold_reason_codes: [],
        questions: [],
        required_match_count: 1,
        required_no_match_count: 0,
        required_unknown_count: 0,
        rider_id: RIDER_MATCH_ID,
        rider_label: "Sample Rider A",
        rider_type: "fixed",
      },
      {
        aggregate_result: "UNKNOWN",
        candidate_id: RIDER_UNKNOWN_ID,
        hold_reason_codes: ["RULE_SYNTHETIC_PARTIAL_FAILURE"],
        questions: [
          {
            field_path: "MedicalEvent.treatment_kind",
            reason_code: "SYNTHETIC_MISSING_FACT",
          },
        ],
        required_match_count: 0,
        required_no_match_count: 0,
        required_unknown_count: 1,
        rider_id: RIDER_UNKNOWN_ID,
        rider_label: "Sample Rider B",
        rider_type: "indemnity",
      },
    ],
    engine_version: "synthetic-decision-engine-v1",
    evaluations: [
      {
        conflicting_fields: [],
        engine_version: "synthetic-decision-engine-v1",
        evaluation_id: "00000000-0000-4000-8000-000000000801",
        evidence: [
          {
            bbox: SYNTHETIC_EVIDENCE.bbox,
            content_sha256: "a".repeat(64),
            document_version_id: DOCUMENT_VERSION_ID,
            evidence_id: EVIDENCE_ID,
            extraction_id: "00000000-0000-4000-8000-000000000901",
            physical_page: 3,
            review_state: "AI_VERIFIED",
          },
        ],
        fact_paths: ["MedicalEvent.event_date"],
        missing_fields: [],
        reason_code: "SYNTHETIC_MATCH",
        required: true,
        result: "MATCH",
        rider_id: RIDER_MATCH_ID,
        rule_version_id: "00000000-0000-4000-8000-000000001001",
      },
      {
        conflicting_fields: [],
        engine_version: "synthetic-decision-engine-v1",
        evaluation_id: "00000000-0000-4000-8000-000000000802",
        evidence: [],
        fact_paths: ["MedicalEvent.treatment_kind"],
        missing_fields: ["MedicalEvent.treatment_kind"],
        reason_code: partial
          ? "RULE_SYNTHETIC_PARTIAL_FAILURE"
          : "SYNTHETIC_MISSING_FACT",
        required: true,
        result: "UNKNOWN",
        rider_id: RIDER_UNKNOWN_ID,
        rule_version_id: "00000000-0000-4000-8000-000000001002",
      },
    ],
    event_version: eventVersion,
    medical_event_id: EVENT_ID,
    policy_snapshot_at: "2026-08-25T09:00:00Z",
    rule_set_version: "synthetic-rule-set-v1",
    run_id: RESULT_ID,
    schema_version: "1",
    stale: partial,
  };
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

function requestBody(route: Route): JsonObject {
  const body = route.request().postData();
  if (!body) return {};
  try {
    const parsed: unknown = JSON.parse(body);
    return typeof parsed === "object" &&
      parsed !== null &&
      !Array.isArray(parsed)
      ? (parsed as JsonObject)
      : {};
  } catch {
    return {};
  }
}

function forbiddenFieldsInBody(route: Route): string[] {
  const body = route.request().postData() ?? "";
  return FORBIDDEN_REQUEST_FIELDS.filter((field) => body.includes(field));
}

export async function installStorageWriteSpy(page: Page): Promise<void> {
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

    const originalRemoveItem = Storage.prototype.removeItem;
    Storage.prototype.removeItem = function removeItem(key) {
      if (this === window.localStorage) writes.localStorage += 1;
      if (this === window.sessionStorage) writes.sessionStorage += 1;
      return originalRemoveItem.call(this, key);
    };

    const originalClear = Storage.prototype.clear;
    Storage.prototype.clear = function clear() {
      if (this === window.localStorage) writes.localStorage += 1;
      if (this === window.sessionStorage) writes.sessionStorage += 1;
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

export async function mockSyntheticEventApi(
  page: Page,
  options: SyntheticEventApiOptions = {},
): Promise<SyntheticEventApiState> {
  const state: SyntheticEventApiState = {
    analysisRequests: 0,
    forbiddenRequests: [],
    receiptLineRequests: 0,
    structureRequests: 0,
    updateRequests: 0,
  };
  const scenario = {
    result: "complete" as const,
    structuring: "success" as const,
    ...options,
  };
  let savedEvent = currentEvent("Synthetic situation");
  let receiptLines: ReceiptLineResponse[] = [];

  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    const expectedOrigin = new URL(
      process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:4173",
    ).origin;
    if (
      (url.protocol === "http:" || url.protocol === "https:") &&
      url.origin !== expectedOrigin
    ) {
      state.forbiddenRequests.push(route.request().url());
      await route.abort("blockedbyclient");
      return;
    }
    await route.continue();
  });

  await page.route("**/api/v1/**", async (route) => {
    state.forbiddenRequests.push(...forbiddenFieldsInBody(route));
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (method === "POST" && path === "/api/v1/medical-events") {
      const body = requestBody(route);
      savedEvent = currentEvent(
        typeof body.situation === "string"
          ? body.situation
          : "Synthetic situation",
        body.mode === "post_treatment" ? "post_treatment" : "pre_visit",
      );
      await fulfillJson(route, savedEvent, 201);
      return;
    }

    if (method === "GET" && path === `/api/v1/medical-events/${EVENT_ID}`) {
      await fulfillJson(route, savedEvent);
      return;
    }

    if (method === "PATCH" && path === `/api/v1/medical-events/${EVENT_ID}`) {
      state.updateRequests += 1;
      const body = requestBody(route);
      const nextFacts = Array.isArray(body.structured_facts)
        ? body.structured_facts
        : savedEvent.structured_facts;
      savedEvent = currentEvent(
        typeof body.situation === "string"
          ? body.situation
          : savedEvent.situation,
        body.mode === "post_treatment" ? "post_treatment" : savedEvent.mode,
        savedEvent.version + 1,
        nextFacts as MedicalEventResponse["structured_facts"],
      );
      await fulfillJson(route, savedEvent);
      return;
    }

    if (
      method === "POST" &&
      path === `/api/v1/medical-events/${EVENT_ID}/structure`
    ) {
      state.structureRequests += 1;
      if (scenario.structuring === "failure") {
        await fulfillJson(
          route,
          {
            error_code: "STRUCTURING_UNAVAILABLE",
            message: "Synthetic structuring is unavailable.",
          },
          503,
        );
        return;
      }
      const accepted: StructureAcceptedResponse = {
        job_id: JOB_ID,
        schema_version: "1",
        state: "queued",
        status_url: `/api/v1/medical-event-structuring-jobs/${JOB_ID}`,
      };
      await fulfillJson(route, accepted, 202);
      return;
    }

    if (
      method === "GET" &&
      path === `/api/v1/medical-event-structuring-jobs/${JOB_ID}`
    ) {
      const job: StructuringJobResponse = {
        attempts: 1,
        error_code: null,
        facts: [],
        issues: [],
        job_id: JOB_ID,
        questions: [],
        schema_version: "1",
        state: "succeeded",
      };
      await fulfillJson(route, job);
      return;
    }

    if (
      method === "GET" &&
      path === `/api/v1/medical-events/${EVENT_ID}/calculations`
    ) {
      await fulfillJson(route, SYNTHETIC_CALCULATIONS);
      return;
    }

    if (
      method === "POST" &&
      path === `/api/v1/medical-events/${EVENT_ID}/analyze`
    ) {
      state.analysisRequests += 1;
      await fulfillJson(
        route,
        resultResponse(savedEvent.version, scenario.result),
      );
      return;
    }

    const receiptPath = `/api/v1/medical-events/${EVENT_ID}/receipt-lines`;
    if (method === "GET" && path === receiptPath) {
      await fulfillJson(route, {
        receipt_lines: receiptLines,
        schema_version: "1",
      });
      return;
    }

    if (method === "POST" && path === receiptPath) {
      state.receiptLineRequests += 1;
      const body = requestBody(route) as unknown as ReceiptLineCreateRequest;
      const created: ReceiptLineResponse = {
        amount: body.amount,
        category: body.category,
        confirmation_level: body.confirmation_level,
        coverage_category: body.coverage_category,
        currency: body.currency,
        id: RECEIPT_LINE_ID,
        note_code: body.note_code ?? null,
        version: 1,
      };
      receiptLines = [...receiptLines, created];
      await fulfillJson(route, created, 201);
      return;
    }

    const receiptLineMatch = path.match(new RegExp(`^${receiptPath}/([^/]+)$`));
    if (receiptLineMatch && method === "PATCH") {
      state.receiptLineRequests += 1;
      const body = requestBody(route) as unknown as ReceiptLineUpdateRequest;
      receiptLines = receiptLines.map((line) =>
        line.id === receiptLineMatch[1]
          ? {
              ...line,
              amount: body.amount ?? line.amount,
              category: body.category ?? line.category,
              confirmation_level:
                body.confirmation_level ?? line.confirmation_level,
              coverage_category:
                body.coverage_category ?? line.coverage_category,
              currency: body.currency ?? line.currency,
              note_code: body.note_code ?? line.note_code,
              version: line.version + 1,
            }
          : line,
      );
      const updated = receiptLines.find(
        (line) => line.id === receiptLineMatch[1],
      );
      if (updated) {
        await fulfillJson(route, updated);
      } else {
        await fulfillJson(
          route,
          {
            error_code: "NOT_FOUND",
            message: "Synthetic receipt line not found.",
          },
          404,
        );
      }
      return;
    }

    if (receiptLineMatch && method === "DELETE") {
      state.receiptLineRequests += 1;
      receiptLines = receiptLines.filter(
        (line) => line.id !== receiptLineMatch[1],
      );
      await route.fulfill({ status: 204 });
      return;
    }

    if (
      method === "GET" &&
      new RegExp(`^/api/v1/medical-events/${EVENT_ID}/results/[0-9]+$`).test(
        path,
      )
    ) {
      await fulfillJson(
        route,
        resultResponse(savedEvent.version, scenario.result),
      );
      return;
    }

    if (method === "GET" && path === `/api/v1/evidence/${EVIDENCE_ID}`) {
      await fulfillJson(route, SYNTHETIC_EVIDENCE);
      return;
    }

    await fulfillJson(
      route,
      { error_code: "NOT_FOUND", message: "Synthetic route not found." },
      404,
    );
  });

  return state;
}

export const SYNTHETIC_EVENT_IDS = {
  evidence: EVIDENCE_ID,
  event: EVENT_ID,
  member: MEMBER_ID,
};
