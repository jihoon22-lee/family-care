import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  ClauseEvidenceResponse,
  ClauseHierarchyNodeResponse,
  ClauseHierarchyResponse,
  ClauseSearchHitResponse,
  ClauseSearchResponse,
  TermsEditionResponse,
} from "../../api/generated";
import { ClauseSearchPage } from "./ClauseSearchPage";
import { renderWithProviders } from "../../test/renderWithProviders";

const TERMS_EDITION_ID = "synthetic-terms-edition-001";
const CLAUSE_ID = "synthetic-clause-001";
const EVIDENCE_ID = "synthetic-clause-evidence-001";
const NORMALIZATION_VERSION = "unicode-nfc-v1";

const TERMS_EDITIONS = [
  {
    applicability_end: "2026-12-31",
    applicability_start: "2026-01-01",
    content_sha256: "b".repeat(64),
    document_version_id: "synthetic-terms-document-version-001",
    id: TERMS_EDITION_ID,
    insurer_display: "Synthetic Mutual",
    insurer_key: "synthetic-mutual",
    normalization_version: NORMALIZATION_VERSION,
    product_display: "Sample Terms",
    product_key: "sample-terms",
    version: 1,
  },
] satisfies TermsEditionResponse[];

const EVIDENCE = {
  bbox: null,
  content_sha256: "a".repeat(64),
  document_version_id: "synthetic-terms-document-version-001",
  evidence_id: EVIDENCE_ID,
  page_number: 14,
} satisfies ClauseEvidenceResponse;

const RESULT = {
  clause_id: CLAUSE_ID,
  evidence: [EVIDENCE],
  excerpt: "합성 약관에서 보장 개시일과 대기기간을 설명하는 발췌입니다.",
  label: "제3조 보장 개시일",
  normalization_version: NORMALIZATION_VERSION,
  physical_page_end: 15,
  physical_page_start: 14,
  relevance: 0.91,
  terms_edition_id: TERMS_EDITION_ID,
} satisfies ClauseSearchHitResponse;

const SEARCH_RESPONSE = {
  normalization_version: NORMALIZATION_VERSION,
  query_matched_count: 1,
  schema_version: "1",
  hits: [RESULT],
} satisfies ClauseSearchResponse;

const HIERARCHY_NODE = {
  clause_id: CLAUSE_ID,
  clause_type: "article",
  evidence: [EVIDENCE],
  excerpt: "합성 약관에서 보장 개시일과 대기기간을 설명하는 발췌입니다.",
  label: RESULT.label,
  normalization_version: NORMALIZATION_VERSION,
  parent_clause_id: null,
  physical_page_end: 15,
  physical_page_start: 14,
} satisfies ClauseHierarchyNodeResponse;

const HIERARCHY_RESPONSE = {
  clauses: [HIERARCHY_NODE],
  terms_edition_id: TERMS_EDITION_ID,
} satisfies ClauseHierarchyResponse;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    headers: {
      "cache-control": "no-store",
      "content-type": "application/json",
    },
    status,
  });
}

function installFetch(
  searchResponse: unknown = SEARCH_RESPONSE,
  searchStatus = 200,
): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), window.location.origin);
    if (url.pathname === "/api/v1/terms-editions") {
      return jsonResponse(TERMS_EDITIONS);
    }
    if (url.pathname === "/api/v1/clauses/search") {
      return jsonResponse(searchResponse, searchStatus);
    }
    return jsonResponse({ error_code: "NOT_FOUND", message: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("private clause search", () => {
  it("submits by keyboard in a POST JSON body without URL or storage leakage", async () => {
    const fetchMock = installFetch();
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const user = userEvent.setup();

    renderWithProviders(<ClauseSearchPage />);

    const query = await screen.findByRole("searchbox", {
      name: /약관|clause/i,
    });
    await user.type(query, "대기기간");
    await user.keyboard("{Enter}");

    expect(
      await screen.findByRole("heading", { name: "제3조 보장 개시일" }),
    ).toBeInTheDocument();
    const searchCall = fetchMock.mock.calls.find(([input]) => {
      const url = new URL(String(input), window.location.origin);
      return url.pathname === "/api/v1/clauses/search";
    });
    expect(searchCall).toBeDefined();
    const [input, init] = searchCall ?? [];
    const url = new URL(String(input), window.location.origin);
    expect(url.search).toBe("");
    expect(init).toMatchObject({
      cache: "no-store",
      credentials: "include",
      method: "POST",
    });
    expect(JSON.parse(String(init?.body))).toEqual({
      q: "대기기간",
      limit: 20,
    });
    expect(storageWrite).not.toHaveBeenCalled();
  });

  it("renders bounded excerpts and exact physical page ranges without full clause text", async () => {
    const fullClauseText = "FULL_SYNTHETIC_NORMALIZED_CLAUSE_BODY";
    installFetch({
      normalization_version: NORMALIZATION_VERSION,
      query_matched_count: 1,
      schema_version: "1",
      hits: [
        {
          ...RESULT,
          excerpt: `${RESULT.excerpt} ${"X".repeat(400)} ${fullClauseText}`,
        },
      ],
    });
    const user = userEvent.setup();

    renderWithProviders(<ClauseSearchPage />);

    const query = await screen.findByRole("searchbox", {
      name: /약관|clause/i,
    });
    await user.type(query, "보장");
    await user.keyboard("{Enter}");

    const result = await screen.findByRole("listitem");
    expect(
      within(result).getByText(/물리 페이지|physical page/i),
    ).toHaveTextContent("14–15");
    expect(
      within(result).getByText(RESULT.excerpt, { exact: false }),
    ).toBeInTheDocument();
    expect(result).not.toHaveTextContent(fullClauseText);
    expect(result.textContent?.length).toBeLessThan(650);
  });

  it("includes selected edition and date filters, then resets them without changing the route", async () => {
    const fetchMock = installFetch();
    const user = userEvent.setup();

    renderWithProviders(<ClauseSearchPage />);

    const edition = await screen.findByRole("combobox", {
      name: /판본|edition/i,
    });
    const date = screen.getByLabelText(/기준일|effective/i);
    await user.selectOptions(edition, TERMS_EDITION_ID);
    await user.type(date, "2026-04-05");
    await user.type(
      screen.getByRole("searchbox", { name: /약관|clause/i }),
      "갱신",
    );
    await user.keyboard("{Enter}");

    await screen.findByRole("heading", { name: "제3조 보장 개시일" });
    const searchCall = fetchMock.mock.calls.find(([input]) => {
      const url = new URL(String(input), window.location.origin);
      return url.pathname === "/api/v1/clauses/search";
    });
    expect(searchCall?.[1]).toMatchObject({ method: "POST" });
    expect(JSON.parse(String(searchCall?.[1]?.body))).toEqual({
      effective_on: "2026-04-05",
      limit: 20,
      q: "갱신",
      terms_edition_id: TERMS_EDITION_ID,
    });

    const pathBeforeReset = window.location.pathname;
    await user.click(
      screen.getByRole("button", { name: /필터 초기화|reset/i }),
    );
    expect(edition).toHaveValue("");
    expect(date).toHaveValue("");
    expect(window.location.pathname).toBe(pathBeforeReset);
  });

  it("rejects an empty query without sending a search request or echoing input", async () => {
    const fetchMock = installFetch();
    const user = userEvent.setup();

    renderWithProviders(<ClauseSearchPage />);

    await screen.findByRole("combobox", { name: /판본|edition/i });
    await user.click(screen.getByRole("button", { name: /검색 Search/i }));

    expect(screen.getByRole("alert")).toHaveTextContent(/검색어.*입력/i);
    expect(
      fetchMock.mock.calls.some(([input]) =>
        new URL(String(input), window.location.origin).pathname.endsWith(
          "/clauses/search",
        ),
      ),
    ).toBe(false);
  });

  it("shows a value-free warning when the server rejects a stale index", async () => {
    installFetch(
      {
        error_code: "SEARCH_INDEX_VERSION_MISMATCH",
        message: "search index version mismatch",
      },
      409,
    );
    const user = userEvent.setup();

    renderWithProviders(<ClauseSearchPage />);

    const query = await screen.findByRole("searchbox", {
      name: /약관|clause/i,
    });
    await user.type(query, "대기");
    await user.keyboard("{Enter}");

    expect(
      await screen.findByRole("status", { name: /검색 인덱스|stale/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
  });

  it("loads hierarchy context from the selected TermsEdition without showing clause bodies", async () => {
    const fetchMock = installFetch();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/terms-editions") {
        return jsonResponse(TERMS_EDITIONS);
      }
      if (url.pathname === "/api/v1/clauses/search") {
        return jsonResponse(SEARCH_RESPONSE);
      }
      if (
        url.pathname === `/api/v1/terms-editions/${TERMS_EDITION_ID}/clauses`
      ) {
        return jsonResponse(HIERARCHY_RESPONSE);
      }
      return jsonResponse({}, 404);
    });
    const user = userEvent.setup();

    renderWithProviders(<ClauseSearchPage />);

    const query = await screen.findByRole("searchbox", {
      name: /약관|clause/i,
    });
    await user.type(query, "대기");
    await user.keyboard("{Enter}");
    await screen.findByRole("heading", { name: RESULT.label });
    await user.click(screen.getByRole("button", { name: /조항 계층/i }));

    expect(
      await screen.findByRole("heading", { name: "조항 계층" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("treeitem")).toHaveTextContent(RESULT.label);
    expect(screen.getByRole("treeitem")).not.toHaveTextContent(
      "normalized text",
    );
  });

  it("aborts an obsolete request and keeps only the newest result", async () => {
    const firstSearch = new Promise<Response>(() => undefined);
    let searchCount = 0;
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input), window.location.origin);
        if (url.pathname === "/api/v1/terms-editions") {
          return jsonResponse(TERMS_EDITIONS);
        }
        if (url.pathname === "/api/v1/clauses/search") {
          searchCount += 1;
          if (searchCount === 1) return firstSearch;
          expect(init?.signal?.aborted).toBe(false);
          return jsonResponse({
            ...SEARCH_RESPONSE,
            hits: [{ ...RESULT, label: "제4조 갱신 안내" }],
          } satisfies ClauseSearchResponse);
        }
        return jsonResponse({}, 404);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<ClauseSearchPage />);

    const query = await screen.findByRole("searchbox", {
      name: /약관|clause/i,
    });
    await user.type(query, "첫 검색");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(searchCount).toBe(1));
    await user.clear(query);
    await user.type(query, "두 번째 검색");
    await user.keyboard("{Enter}");

    expect(
      await screen.findByRole("heading", { name: "제4조 갱신 안내" }),
    ).toBeInTheDocument();
    expect(fetchMock.mock.calls[1]?.[1]?.signal).toBeDefined();
    expect((fetchMock.mock.calls[1]?.[1]?.signal as AbortSignal).aborted).toBe(
      true,
    );
  });
});
