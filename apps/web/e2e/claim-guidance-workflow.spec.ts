import { expect, test, type Page } from "@playwright/test";
import type {
  GuidanceEvidenceDetail,
  GuidanceEvidenceRequest,
  GuidanceReviewJob,
  MedicalEventResponse,
} from "../src/api/generated";
import {
  installStorageWriteSpy,
  mockAuthenticatedSession,
  mockLocalGuidanceClaimApi,
} from "./support/mockApi";

const id = (value: number) =>
  `00000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
type Evidence = GuidanceEvidenceRequest["evidence"];
function ref(number: number): Evidence {
  if (number === 1)
    return {
      kind: "SEMANTIC_CITATION",
      citation_id: id(8101),
      document_version_id: id(8201),
      terms_edition_id: id(8301),
      generation_id: id(8401),
      publication_id: id(8501),
      root_node_id: "synthetic-root",
      source_node_id: "synthetic-node",
      page_start: 2,
      page_end: 2,
      start: 0,
      end: 20,
      source_layer: "native",
      source_sha256: "a".repeat(64),
      manifest_sha256: "b".repeat(64),
      bbox: [0, 0, 1, 1],
    };
  return {
    kind: number === 0 ? "TERMS_SECTION" : "OPERATIONAL_EVIDENCE",
    evidence_id: id(8000 + number),
    page_start: number + 1,
    page_end: number + 1,
  };
}
function key(value: Evidence) {
  return "citation_id" in value ? value.citation_id : value.evidence_id;
}
function detail(evidence: Evidence, label: string): GuidanceEvidenceDetail {
  return {
    schema_version: "1",
    evidence,
    document_label: label,
    document_version_id:
      "document_version_id" in evidence
        ? evidence.document_version_id
        : id(8200),
    source_document_ref: null,
    terms_edition_id:
      "terms_edition_id" in evidence ? evidence.terms_edition_id : null,
    terms_edition_label:
      "terms_edition_id" in evidence ? "2026-09-01 판본" : null,
    page_start: evidence.page_start,
    page_end: evidence.page_end,
    clause_label: "Sample Clause",
    content_kind: evidence.kind === "TERMS_SECTION" ? "SUMMARY" : "ORIGINAL",
    text: `Synthetic excerpt for ${label}`,
    truncated: false,
    bbox: null,
    reason_codes: [],
  };
}
async function setup(page: Page, count = 1) {
  await page.setViewportSize({ width: 320, height: 800 });
  await installStorageWriteSpy(page);
  const mock = await mockLocalGuidanceClaimApi(page);
  await mockAuthenticatedSession(page);
  const result = structuredClone(mock.result);
  const refs = Array.from({ length: count }, (_, index) => ref(index));
  const primary = result.local_guidance!.candidates[0];
  primary.coverage_label = "Sample Primary Coverage";
  primary.conditions = [];
  primary.relevance = [];
  primary.scenarios = [];
  primary.cases = [];
  primary.estimate.evidence = refs;
  if (primary.contract_amount) primary.contract_amount.evidence = [];
  const other = structuredClone(primary);
  other.ref = { ...primary.ref, coverage_id: id(8700) };
  other.coverage_label = "Sample Other Coverage";
  other.estimate.evidence = [ref(100)];
  result.local_guidance!.candidates = [primary, other];
  await page.route(
    `**/api/v1/medical-events/${mock.eventId}/results/1`,
    async (route) =>
      route.fulfill({ json: result, headers: { "Cache-Control": "no-store" } }),
  );
  return { ...mock, result, refs, primary, other };
}
async function transient(page: Page) {
  expect(await page.evaluate(() => window.__familyCareStorageWrites)).toEqual({
    indexedDB: 0,
    localStorage: 0,
    sessionStorage: 0,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  const stored = await page.evaluate(async () => {
    if (!("caches" in window)) return [];
    const urls: string[] = [];
    for (const name of await caches.keys())
      for (const request of await (await caches.open(name)).keys())
        urls.push(request.url);
    return urls.filter((url) => /\/api\//.test(url));
  });
  expect(stored).toEqual([]);
}

test("retains partial common evidence across 18 sources and retries only the failed source at 320px", async ({
  page,
}) => {
  const mock = await setup(page, 18);
  const counts = Array.from({ length: 18 }, () => 0);
  await page.route(
    `**/api/v1/medical-events/${mock.eventId}/guidance-evidence`,
    async (route) => {
      const body = route.request().postDataJSON() as GuidanceEvidenceRequest;
      expect(body.decision_run_id).toBe(mock.runId);
      expect(body.expected_event_version).toBe(1);
      expect(body.coverage).toEqual(mock.primary.ref);
      const index = mock.refs.findIndex(
        (value) => key(value) === key(body.evidence),
      );
      counts[index] += 1;
      if (index === 2 && counts[index] === 1)
        await route.fulfill({
          status: 503,
          json: { error_code: "RESOURCE_LIMIT_EXCEEDED" },
        });
      else
        await route.fulfill({
          json: detail(body.evidence, `Sample Source ${index}`),
          headers: { "Cache-Control": "no-store" },
        });
    },
  );
  await page.goto(`/app/events/${mock.eventId}/result/1`);
  const trigger = page
    .getByRole("article", { name: "Sample Primary Coverage" })
    .getByRole("button", { name: "근거 내용 보기" });
  await trigger.focus();
  await page.keyboard.press("Enter");
  const drawer = page.getByRole("dialog", { name: "증권과 약관 근거" });
  await expect(drawer).toContainText("근거 총 18건 · 15건 표시");
  await expect(drawer).toContainText("저장된 자료 요약");
  await expect(drawer).toContainText("2026-09-01 판본");
  expect(counts[16]).toBe(0);
  await drawer.getByRole("button", { name: /근거 더 보기/ }).click();
  await expect(drawer).toContainText("근거 총 18건 · 17건 표시");
  await drawer
    .getByRole("button", { name: "불러오지 못한 근거 다시 확인" })
    .click();
  await expect(drawer).toContainText("근거 총 18건 · 18건 표시");
  expect(counts[2]).toBe(2);
  expect(counts.filter((count, index) => index !== 2 && count !== 1)).toEqual(
    [],
  );
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
  await transient(page);
});

test("a closed slow evidence request cannot replace the next candidate or survive session expiry", async ({
  page,
}) => {
  const mock = await setup(page);
  let release!: () => void;
  const held = new Promise<void>((done) => {
    release = done;
  });
  let lateFinished = false,
    expired = false;
  await page.route(
    `**/api/v1/medical-events/${mock.eventId}/guidance-evidence`,
    async (route) => {
      const body = route.request().postDataJSON() as GuidanceEvidenceRequest;
      if (body.coverage.coverage_id === mock.primary.ref.coverage_id) {
        await held;
        await route.fulfill({ json: detail(body.evidence, "Sample Source A") });
        lateFinished = true;
      } else if (expired)
        await route.fulfill({
          status: 401,
          json: { error_code: "AUTHENTICATION_REQUIRED" },
        });
      else
        await route.fulfill({ json: detail(body.evidence, "Sample Source B") });
    },
  );
  try {
    await page.goto(`/app/events/${mock.eventId}/result/1`);
    await page
      .getByRole("article", { name: "Sample Primary Coverage" })
      .getByRole("button", { name: "근거 내용 보기" })
      .click();
    await expect(page.getByRole("dialog")).toContainText("불러오는 중");
    await page.getByRole("button", { name: "닫기", exact: true }).click();
    const other = page
      .getByRole("article", { name: "Sample Other Coverage" })
      .getByRole("button", { name: "근거 내용 보기" });
    await other.click();
    await expect(page.getByRole("dialog")).toContainText("Sample Source B");
    release();
    await expect.poll(() => lateFinished).toBe(true);
    await expect(page.getByRole("dialog")).not.toContainText("Sample Source A");
    await page.keyboard.press("Escape");
    expired = true;
    await other.click();
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(
      page.getByText("Sample Source B", { exact: true }),
    ).toHaveCount(0);
    await transient(page);
  } finally {
    release();
  }
});

test("network recovery resumes only GET review polling and keeps local results and keyboard focus", async ({
  page,
}) => {
  const mock = await setup(page);
  let creates = 0,
    reads = 0,
    offline = true;
  const job: GuidanceReviewJob = {
    id: id(9001),
    medical_event_id: mock.eventId,
    event_version: 1,
    decision_run_id: mock.runId,
    matched_decision_run_id: mock.runId,
    created_at: new Date().toISOString(),
    state: "running",
    http_attempts: 1,
  };
  await page.route(/\/api\/v1\/.*guidance-reviews/, async (route) => {
    const request = route.request();
    if (new URL(request.url()).pathname.endsWith("/current")) {
      await route.fulfill({ json: null });
      return;
    }
    if (request.method() === "POST") {
      creates += 1;
      await route.fulfill({ json: job });
      return;
    }
    reads += 1;
    if (offline) await route.abort("internetdisconnected");
    else await route.fulfill({ json: { ...job, state: "completed" } });
  });
  await page.goto(`/app/events/${mock.eventId}/result/1`);
  const review = page.getByRole("region", {
    name: "AI 선택 검수",
    exact: true,
  });
  await review.getByRole("button", { name: "AI 선택 검수 시작" }).click();
  const trigger = page
    .getByRole("article", { name: "Sample Primary Coverage" })
    .getByRole("button", { name: "근거 내용 보기" });
  await trigger.focus();
  await expect(review.getByRole("alert")).toContainText(
    "기존 로컬 안내는 계속 볼 수 있습니다",
  );
  await expect(trigger).toBeFocused();
  const failedReads = reads;
  offline = false;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(review.getByText("검수가 완료되었습니다.")).toBeVisible();
  expect(creates).toBe(1);
  expect(reads).toBe(failedReads + 1);
  await expect(trigger).toBeFocused();
  await transient(page);
});

test("reuses an earlier review for an equivalent run and prepares the reviewed snapshot with its provenance", async ({
  page,
}) => {
  const mock = await setup(page);
  const selectedRun = id(9100);
  mock.result.run_id = selectedRun;
  const reviewed = structuredClone(mock.result.local_guidance!);
  reviewed.candidates = [
    { ...mock.primary, coverage_label: "Sample Reviewed Coverage" },
  ];
  const job: GuidanceReviewJob = {
    id: id(9101),
    medical_event_id: mock.eventId,
    event_version: 1,
    decision_run_id: mock.runId,
    matched_decision_run_id: selectedRun,
    created_at: new Date().toISOString(),
    state: "completed",
    http_attempts: 1,
    result: {
      source_digest: "a".repeat(64),
      reason_codes: [],
      findings: [],
      differences: [],
      guidance: reviewed,
      scope: {
        complete: true,
        total_coverages: 1,
        indexed_coverages: 1,
        total_packets: 1,
        reviewed_packets: 1,
        unreviewed_packets: 0,
        omitted_packets: 0,
      },
    },
  };
  let reviewCreates = 0;
  await page.route(/\/api\/v1\/.*guidance-reviews/, async (route) => {
    if (route.request().method() === "POST") reviewCreates += 1;
    await route.fulfill({ json: job });
  });
  const claim = structuredClone(mock.claim);
  claim.snapshot.local_guidance = {
    ...claim.snapshot.local_guidance!,
    run_id: selectedRun,
    candidate: reviewed.candidates[0],
    review: {
      review_job_id: job.id,
      original_decision_run_id: mock.runId,
      source_digest: "a".repeat(64),
      result_digest: "b".repeat(64),
    },
  };
  const created: unknown[] = [];
  await page.route(
    `**/api/v1/medical-events/${mock.eventId}/claims`,
    async (route) => {
      created.push(route.request().postDataJSON());
      await route.fulfill({ status: 201, json: claim });
    },
  );
  await page.route(`**/api/v1/claims/${mock.claimId}`, async (route) =>
    route.fulfill({ json: claim }),
  );
  await page.goto(`/app/events/${mock.eventId}/result/1`);
  const review = page.getByRole("region", {
    name: "AI 선택 검수",
    exact: true,
  });
  await expect(review.getByText("검수가 완료되었습니다.")).toBeVisible();
  const summary = review.getByText("프로그램 재평가 결과와 변경점", {
    exact: true,
  });
  await summary.focus();
  await page.keyboard.press("Enter");
  await review
    .getByRole("button", { name: "Sample Reviewed Coverage 청구 준비" })
    .click();
  await expect(page).toHaveURL(new RegExp(`/app/claims/${mock.claimId}$`));
  expect(created).toEqual([
    {
      guidance: {
        run_id: selectedRun,
        expected_event_version: 1,
        coverage: reviewed.candidates[0].ref,
        review_job_id: job.id,
      },
    },
  ]);
  expect(reviewCreates).toBe(0);
  await expect(
    page.getByText("선택 검수 결과에서 준비한 안내입니다."),
  ).toBeVisible();
  await expect(
    page.getByText("아직 기록되지 않았습니다.", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "이 안내의 사건 결과" }),
  ).toHaveAttribute("href", `/app/events/${mock.eventId}/result/1`);
  await transient(page);
});

test("a saved Korean draft survives reload and optional answers produce a new result version", async ({
  page,
}) => {
  const mock = await setup(page);
  let event: MedicalEventResponse = {
    id: mock.eventId,
    family_member_id: mock.result.local_guidance!.family_member_id,
    version: 1,
    deleted: false,
    mode: "post_treatment",
    situation: "",
    facts: {},
    event_date: null,
    visit_date: null,
    structured_facts: [],
    optional_questions: [],
  };
  let creates = 0,
    analyses = 0;
  const patches: Record<string, unknown>[] = [];
  const results = new Map<number, typeof mock.result>();
  await page.route("**/api/v1/medical-events", async (route) => {
    creates += 1;
    const body = route.request().postDataJSON();
    event = {
      ...event,
      situation: body.situation,
      event_date: body.event_date,
      visit_date: body.visit_date,
    };
    await route.fulfill({ status: 201, json: event });
  });
  await page.route(
    `**/api/v1/medical-events/${mock.eventId}`,
    async (route) => {
      if (route.request().method() === "PATCH") {
        const body = route.request().postDataJSON();
        patches.push(body);
        expect(body.expected_version).toBe(event.version);
        event = {
          ...event,
          version: event.version + 1,
          situation: body.situation ?? event.situation,
          facts: body.facts ?? event.facts,
          event_date:
            body.event_date === undefined ? event.event_date : body.event_date,
          visit_date:
            body.visit_date === undefined ? event.visit_date : body.visit_date,
        };
      }
      await route.fulfill({ json: event });
    },
  );
  await page.route(
    `**/api/v1/medical-events/${mock.eventId}/analyze`,
    async (route) => {
      analyses += 1;
      const result = structuredClone(mock.result);
      result.run_id = id(9200 + event.version);
      result.event_version = event.version;
      result.local_guidance!.event_version = event.version;
      result.local_guidance!.event_date = event.event_date;
      for (const candidate of result.local_guidance!.candidates)
        candidate.questions = [];
      if (!event.facts["MedicalEvent.admission_days"])
        result.local_guidance!.candidates[0].questions = [
          {
            field_path: "MedicalEvent.admission_days",
            reason_code: "MISSING_DAYS",
          },
        ];
      results.set(event.version, result);
      await route.fulfill({ json: result });
    },
  );
  await page.route(
    `**/api/v1/medical-events/${mock.eventId}/results/*`,
    async (route) => {
      const version = Number(
        new URL(route.request().url()).pathname.split("/").at(-1),
      );
      await route.fulfill({ json: results.get(version) ?? mock.result });
    },
  );
  await page.goto(
    `/app/events/new?member=${event.family_member_id}&mode=post_treatment`,
  );
  await expect(
    page.getByText("Family Member A", { exact: true }),
  ).toBeVisible();
  const situation = page.getByRole("textbox", { name: "현재 상황" });
  await situation.focus();
  await situation.dispatchEvent("compositionstart");
  await page.keyboard.insertText("합성 입원 상황을 확인합니다");
  await situation.dispatchEvent("compositionend", {
    data: "합성 입원 상황을 확인합니다",
  });
  await page.getByLabel("사건 날짜 (선택)").fill("2026-09-01");
  await page.getByRole("button", { name: "현재 후보 보기" }).click();
  await expect(page).toHaveURL(new RegExp(`/app/events/${mock.eventId}$`));
  await page.reload();
  await expect(page.getByRole("textbox", { name: "현재 상황" })).toHaveValue(
    "합성 입원 상황을 확인합니다",
  );
  await page.getByRole("button", { name: "결과 확인" }).click();
  await expect(page).toHaveURL(
    new RegExp(`/app/events/${mock.eventId}/result/2$`),
  );
  await expect(page.getByText("2026-09-01", { exact: true })).toBeVisible();
  await expect(page.getByText("자료 확인 기준", { exact: true })).toBeVisible();
  await page.getByLabel("입원 일수", { exact: true }).fill("5");
  await page.getByRole("button", { name: "입력 보완 후 다시 계산" }).click();
  await expect(page).toHaveURL(
    new RegExp(`/app/events/${mock.eventId}/result/3$`),
  );
  expect(patches.at(-1)).toMatchObject({
    expected_version: 2,
    facts: {
      "MedicalEvent.admission_days": { value: 5, confirmation: "user" },
    },
  });
  expect(creates).toBe(1);
  expect(analyses).toBe(2);
  expect(mock.state.structureRequests).toBe(0);
  await page.goBack();
  await expect(page).toHaveURL(
    new RegExp(`/app/events/${mock.eventId}/result/2$`),
  );
  await expect(
    page.getByRole("article", { name: "Sample Primary Coverage" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Sample Primary Coverage 청구 준비" }),
  ).toBeDisabled();
  await page.getByRole("link", { name: "사건 정보 보완" }).click();
  await expect(page.getByRole("textbox", { name: "현재 상황" })).toHaveValue(
    "합성 입원 상황을 확인합니다",
  );
  await transient(page);
});
