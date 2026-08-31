import type {
  CoverageDecisionResponse,
  MedicalEventResponse,
  ReceiptLineResponse,
  StructureAcceptedResponse,
  StructuringJobResponse,
} from "./generated";
import {
  analyzeMedicalEvent,
  createReceiptLine,
  createMedicalEvent,
  deleteReceiptLine,
  getMedicalEvent,
  listReceiptLines,
  getStructuringJob,
  structureMedicalEvent,
  updateReceiptLine,
  updateMedicalEvent,
} from "./events";

const EVENT_ID = "00000000-0000-4000-8000-000000000201";
const MEMBER_ID = "00000000-0000-4000-8000-000000000202";
const JOB_ID = "00000000-0000-4000-8000-000000000301";
const LINE_ID = "00000000-0000-4000-8000-000000000501";

const SYNTHETIC_EVENT: MedicalEventResponse = {
  deleted: false,
  event_date: "2026-08-25",
  facts: {},
  family_member_id: MEMBER_ID,
  id: EVENT_ID,
  mode: "pre_visit",
  situation: "Synthetic pre-visit situation",
  version: 1,
  visit_date: null,
};

const SYNTHETIC_DECISION: CoverageDecisionResponse = {
  analysis_completeness: "COMPLETE",
  assistance: {
    mode: "NONE",
    model_label: null,
    outcome_code: "NO_ASSISTANCE",
    recommendations: [],
    state: "SEARCH_READY",
  },
  candidates: [],
  catalog_coverage: {
    advisory_coverage_count: 0,
    benefit_coverage_count: 0,
    blocked_coverage_count: 0,
    contract_count: 0,
    not_applicable_coverage_count: 0,
    published_coverage_count: 0,
  },
  conditional_fixed_subtotals: [],
  engine_version: "synthetic-decision-engine-v1",
  evaluations: [],
  event_version: 1,
  indemnity_summary: {
    calculated_candidate_count: 0,
    candidate_count: 0,
    status: "NONE",
    unresolved_candidate_count: 0,
  },
  knowledge_snapshot_version: {
    catalog_import_run_id: null,
    event_fact_schema_version: "medical-event-facts.v2",
    rule_import_run_id: null,
  },
  medical_event_id: EVENT_ID,
  policy_snapshot_at: "2026-08-25T09:00:00Z",
  rule_set_version: "synthetic-rule-set-v1",
  run_id: "00000000-0000-4000-8000-000000000401",
  stale: false,
  schema_version: "2",
  source_failure_codes: [],
};

const SYNTHETIC_LINE: ReceiptLineResponse = {
  amount: "12000.00",
  category: "outpatient",
  confirmation_level: "user",
  coverage_category: "covered",
  currency: "KRW",
  deleted: false,
  id: LINE_ID,
  note_code: null,
  version: 1,
};

function jsonResponse<T>(value: T, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("medical event API clients", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("creates only an event payload through the shared no-store boundary", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(SYNTHETIC_EVENT, 201));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      createMedicalEvent({
        family_member_id: MEMBER_ID,
        mode: "pre_visit",
        situation: "Synthetic pre-visit situation",
      }),
    ).resolves.toEqual(SYNTHETIC_EVENT);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/medical-events",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        cache: "no-store",
      }),
    );
    const body = fetchMock.mock.calls[0]?.[1]?.body as string;
    expect(JSON.parse(body)).toEqual({
      family_member_id: MEMBER_ID,
      mode: "pre_visit",
      situation: "Synthetic pre-visit situation",
    });
    expect(body).not.toContain("source_path");
    expect(body).not.toContain("amount");
  });

  it("reads and updates a scoped event with an explicit expected version", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(SYNTHETIC_EVENT))
      .mockResolvedValueOnce(jsonResponse({ ...SYNTHETIC_EVENT, version: 2 }));
    vi.stubGlobal("fetch", fetchMock);

    await getMedicalEvent(EVENT_ID);
    await updateMedicalEvent(EVENT_ID, {
      expected_version: 1,
      situation: "Synthetic updated situation",
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `/api/v1/medical-events/${EVENT_ID}`,
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      `/api/v1/medical-events/${EVENT_ID}`,
    );
    expect(fetchMock.mock.calls[1]?.[1]).toEqual(
      expect.objectContaining({
        method: "PATCH",
        credentials: "include",
        cache: "no-store",
        body: JSON.stringify({
          expected_version: 1,
          situation: "Synthetic updated situation",
        }),
      }),
    );
  });

  it("enqueues structuring with the event version and polls only the returned status URL", async () => {
    const accepted: StructureAcceptedResponse = {
      job_id: JOB_ID,
      schema_version: "1",
      state: "queued",
      status_url: `/api/v1/medical-event-structuring-jobs/${JOB_ID}`,
    };
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
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(accepted, 202))
      .mockResolvedValueOnce(jsonResponse(job));
    vi.stubGlobal("fetch", fetchMock);

    await structureMedicalEvent(EVENT_ID, 1);
    await getStructuringJob(accepted.status_url);

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `/api/v1/medical-events/${EVENT_ID}/structure`,
    );
    expect(fetchMock.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ expected_version: 1 }),
        cache: "no-store",
        credentials: "include",
      }),
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(accepted.status_url);
    expect(fetchMock.mock.calls[1]?.[1]).toEqual(
      expect.objectContaining({
        method: "GET",
        cache: "no-store",
        credentials: "include",
      }),
    );
  });

  it("returns deterministic analysis synchronously without polling a job", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(SYNTHETIC_DECISION));
    vi.stubGlobal("fetch", fetchMock);

    await expect(analyzeMedicalEvent(EVENT_ID)).resolves.toEqual(
      SYNTHETIC_DECISION,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `/api/v1/medical-events/${EVENT_ID}/analyze`,
    );
    expect(fetchMock.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        method: "POST",
        cache: "no-store",
        credentials: "include",
      }),
    );
  });

  it("creates, lists, updates, and deletes decimal receipt lines", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(SYNTHETIC_LINE, 201))
      .mockResolvedValueOnce(
        jsonResponse({ schema_version: "1", receipt_lines: [SYNTHETIC_LINE] }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ ...SYNTHETIC_LINE, amount: "13000.00", version: 2 }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await createReceiptLine(EVENT_ID, {
      amount: "12000.00",
      category: "outpatient",
      confirmation_level: "user",
      coverage_category: "covered",
      currency: "KRW",
    });
    await listReceiptLines(EVENT_ID);
    await updateReceiptLine(EVENT_ID, LINE_ID, {
      amount: "13000.00",
      expected_version: 1,
    });
    await deleteReceiptLine(EVENT_ID, LINE_ID, 2);

    expect(fetchMock.mock.calls.map((call) => call[1]?.method)).toEqual([
      "POST",
      "GET",
      "PATCH",
      "DELETE",
    ]);
    expect(fetchMock.mock.calls[3]?.[1]?.body).toBe(
      JSON.stringify({ expected_version: 2 }),
    );
    for (const call of fetchMock.mock.calls) {
      expect(call[1]).toEqual(
        expect.objectContaining({ cache: "no-store", credentials: "include" }),
      );
    }
  });
});
