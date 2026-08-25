import type {
  BenefitCalculationsResponse,
  CoverageDecisionResponse,
  EvidenceDetailResponse,
} from "./generated";
import { getBenefitCalculations, getEvidence, getEventResult } from "./results";

const EVENT_ID = "00000000-0000-4000-8000-000000000201";
const EVIDENCE_ID = "00000000-0000-4000-8000-000000000601";

const SYNTHETIC_RESULT: CoverageDecisionResponse = {
  candidates: [],
  engine_version: "synthetic-decision-engine-v1",
  evaluations: [],
  event_version: 3,
  medical_event_id: EVENT_ID,
  policy_snapshot_at: "2026-08-25T09:00:00Z",
  rule_set_version: "synthetic-rule-set-v1",
  run_id: "00000000-0000-4000-8000-000000000401",
  stale: true,
  schema_version: "1",
};

const SYNTHETIC_EVIDENCE: EvidenceDetailResponse = {
  bbox: null,
  bounded_excerpt: "Synthetic evidence excerpt",
  clause_label: "Synthetic clause",
  document_label: "Sample Policy",
  document_version_id: "00000000-0000-4000-8000-000000000602",
  evidence_id: EVIDENCE_ID,
  physical_page: 2,
  review_state: "USER_CONFIRMED",
  schema_version: "1",
};

const SYNTHETIC_CALCULATIONS: BenefitCalculationsResponse = {
  calculations: [],
  schema_version: "1",
};

function jsonResponse<T>(value: T): Response {
  return new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
  });
}

describe("event result API clients", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetches an explicit immutable event-result version with no-store", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(SYNTHETIC_RESULT));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getEventResult(EVENT_ID, 3)).resolves.toEqual(
      SYNTHETIC_RESULT,
    );
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/medical-events/${EVENT_ID}/results/3`,
      expect.objectContaining({
        method: "GET",
        cache: "no-store",
        credentials: "include",
      }),
    );
  });

  it("fetches bounded Evidence by generated identifier without persisting it", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(SYNTHETIC_EVIDENCE));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getEvidence(EVIDENCE_ID)).resolves.toEqual(SYNTHETIC_EVIDENCE);
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/evidence/${EVIDENCE_ID}`,
      expect.objectContaining({ cache: "no-store", credentials: "include" }),
    );
  });

  it("fetches server-calculated benefit details without browser arithmetic", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(SYNTHETIC_CALCULATIONS));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getBenefitCalculations(EVENT_ID)).resolves.toEqual(
      SYNTHETIC_CALCULATIONS,
    );
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/medical-events/${EVENT_ID}/calculations`,
      expect.objectContaining({ cache: "no-store", credentials: "include" }),
    );
  });
});
