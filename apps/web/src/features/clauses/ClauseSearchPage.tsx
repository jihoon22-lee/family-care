import { FormEvent, useEffect, useRef, useState } from "react";

import type { ApiError } from "../../api/errors";
import {
  CLAUSE_SEARCH_NORMALIZATION_VERSION,
  getClauseHierarchy,
  listTermsEditions,
  searchClauses,
  type ClauseHierarchyNode,
  type ClauseSearchHit,
  type TermsEditionResponse,
} from "../../api/clauses";
import type { EvidenceRef } from "../../api/generated";
import { EvidenceDrawer } from "../../components/EvidenceDrawer";
import { ClauseHierarchy } from "./ClauseHierarchy";
import {
  ClauseSearchFilters,
  type ClauseSearchFilterValues,
} from "./ClauseSearchFilters";
import { ClauseSearchResults } from "./ClauseSearchResults";

const INITIAL_FILTERS: ClauseSearchFilterValues = {
  effectiveOn: "",
  insurerKey: "",
  productKey: "",
  termsEditionId: "",
};

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function searchErrorCopy(error: unknown): string {
  if (
    error instanceof Error &&
    (error as ApiError).code === "AUTHENTICATION_REQUIRED"
  ) {
    return "로그인이 필요합니다. 인증을 확인한 뒤 다시 열어 주세요.";
  }
  return "약관 검색을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.";
}

function errorCode(error: unknown): unknown {
  return error instanceof Error && "code" in error
    ? (error as Error & { code?: unknown }).code
    : undefined;
}

function isStaleIndexError(error: unknown): boolean {
  return errorCode(error) === "SEARCH_INDEX_VERSION_MISMATCH";
}

function drawerEvidence(hit: ClauseSearchHit): EvidenceRef[] {
  return hit.evidence.map((item) => ({
    bbox: item.bbox,
    bounded_excerpt: hit.excerpt,
    document_label: hit.label,
    document_version_id: item.document_version_id,
    evidence_id: item.evidence_id,
    page: item.page_number,
  }));
}

export function ClauseSearchPage() {
  const [editions, setEditions] = useState<TermsEditionResponse[]>([]);
  const [editionsError, setEditionsError] = useState(false);
  const [query, setQuery] = useState("");
  const [filters, setFilters] =
    useState<ClauseSearchFilterValues>(INITIAL_FILTERS);
  const [hits, setHits] = useState<ClauseSearchHit[]>([]);
  const [matchCount, setMatchCount] = useState(0);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState<string>();
  const [hasSearched, setHasSearched] = useState(false);
  const [staleIndex, setStaleIndex] = useState(false);
  const [evidenceHit, setEvidenceHit] = useState<ClauseSearchHit>();
  const [hierarchyHit, setHierarchyHit] = useState<ClauseSearchHit>();
  const [hierarchyNodes, setHierarchyNodes] = useState<ClauseHierarchyNode[]>(
    [],
  );
  const [hierarchyLoading, setHierarchyLoading] = useState(false);
  const [hierarchyError, setHierarchyError] = useState(false);
  const searchController = useRef<AbortController | null>(null);
  const hierarchyController = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void listTermsEditions(controller.signal)
      .then((response) => {
        if (!controller.signal.aborted) setEditions(response);
      })
      .catch((error: unknown) => {
        if (!isAbortError(error) && !controller.signal.aborted) {
          setEditionsError(true);
        }
      });
    return () => controller.abort();
  }, []);

  useEffect(
    () => () => {
      searchController.current?.abort();
      hierarchyController.current?.abort();
    },
    [],
  );

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedQuery = query.trim();
    if (!normalizedQuery) {
      setSearchError("검색어를 입력해 주세요.");
      setHasSearched(false);
      setHits([]);
      setStaleIndex(false);
      return;
    }

    searchController.current?.abort();
    const controller = new AbortController();
    searchController.current = controller;
    setSearchLoading(true);
    setSearchError(undefined);
    setStaleIndex(false);
    setHasSearched(true);
    setEvidenceHit(undefined);
    setHierarchyHit(undefined);
    const request = {
      limit: 20,
      q: normalizedQuery,
      ...(filters.effectiveOn ? { effective_on: filters.effectiveOn } : {}),
      ...(filters.insurerKey ? { insurer_key: filters.insurerKey } : {}),
      ...(filters.productKey ? { product_key: filters.productKey } : {}),
      ...(filters.termsEditionId
        ? { terms_edition_id: filters.termsEditionId }
        : {}),
    };

    void searchClauses(request, controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return;
        const boundedHits = response.hits.map((hit) => ({
          ...hit,
          excerpt: hit.excerpt.slice(0, 320),
        }));
        setHits(boundedHits);
        setMatchCount(response.query_matched_count);
        setStaleIndex(
          response.normalization_version !==
            CLAUSE_SEARCH_NORMALIZATION_VERSION ||
            boundedHits.some(
              (hit) =>
                hit.normalization_version !==
                CLAUSE_SEARCH_NORMALIZATION_VERSION,
            ),
        );
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || isAbortError(error)) return;
        setHits([]);
        setMatchCount(0);
        if (isStaleIndexError(error)) {
          setStaleIndex(true);
          setSearchError(undefined);
          return;
        }
        if (errorCode(error) === "AUTHENTICATION_REQUIRED") {
          setQuery("");
        }
        setSearchError(searchErrorCopy(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setSearchLoading(false);
      });
  }

  function openHierarchy(hit: ClauseSearchHit) {
    hierarchyController.current?.abort();
    const controller = new AbortController();
    hierarchyController.current = controller;
    setHierarchyHit(hit);
    setHierarchyNodes([]);
    setHierarchyError(false);
    setHierarchyLoading(true);
    void getClauseHierarchy(hit.terms_edition_id, controller.signal)
      .then((response) => {
        if (!controller.signal.aborted) setHierarchyNodes(response.clauses);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted && !isAbortError(error)) {
          setHierarchyError(true);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setHierarchyLoading(false);
      });
  }

  function resetFilters() {
    setFilters(INITIAL_FILTERS);
  }

  const hierarchyEdition = hierarchyHit
    ? editions.find((edition) => edition.id === hierarchyHit.terms_edition_id)
    : undefined;

  return (
    <main id="main-content" className="clause-search-page" tabIndex={-1}>
      <section
        className="clause-search-intro"
        aria-labelledby="clause-search-title"
      >
        <div>
          <p className="eyebrow">원문 근거를 찾는 조사 도구</p>
          <h1 id="clause-search-title">약관 조항 검색</h1>
          <p>
            검색 결과는 조항을 확인하기 위한 참고 자료입니다. 가입 여부나 보험금
            지급을 판정하지 않으며, 모든 결과는 근거 페이지에서 다시 확인합니다.
          </p>
        </div>
        <aside className="clause-privacy-note" aria-label="검색 개인정보 안내">
          <span>Private / no-store</span>
          <strong>검색어는 이 화면의 메모리에만 머뭅니다.</strong>
          <small>
            URL, 브라우저 저장소와 서비스 워커 캐시를 사용하지 않습니다.
          </small>
        </aside>
      </section>

      <form
        className="clause-search-form"
        role="search"
        onSubmit={submitSearch}
        aria-busy={searchLoading}
      >
        <label className="clause-query-label" htmlFor="clause-query">
          <span>약관에서 찾을 표현</span>
          <input
            id="clause-query"
            name="clause-query"
            type="search"
            autoComplete="off"
            aria-describedby="clause-query-help"
            aria-label="약관 조항 검색 Clause search"
            placeholder="예: 보장 개시일, 대기기간"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <button type="submit" className="primary-button">
          검색 Search
        </button>
        <p id="clause-query-help" className="clause-query-help">
          검색어는 POST 요청 본문으로만 전송됩니다.
        </p>
      </form>

      <ClauseSearchFilters
        editions={editions}
        values={filters}
        onChange={setFilters}
        onReset={resetFilters}
      />
      {editionsError ? (
        <p className="clause-filter-note" role="status">
          약관 판본 목록을 불러오지 못했습니다. 검색어로 다시 시도해 주세요.
        </p>
      ) : null}
      {searchError ? <p role="alert">{searchError}</p> : null}
      {staleIndex ? (
        <p
          className="clause-stale-warning"
          role="status"
          aria-label="검색 인덱스 stale warning"
        >
          검색 인덱스 형식이 현재 앱과 다릅니다. 결과를 참고용으로만 보고 원문
          근거를 다시 확인해 주세요.
        </p>
      ) : null}
      {hasSearched && !searchLoading && !searchError ? (
        <p className="clause-result-count" role="status" aria-live="polite">
          검색 결과 {matchCount}건
        </p>
      ) : null}

      <ClauseSearchResults
        hits={hits}
        loading={searchLoading}
        hasSearched={hasSearched && !searchError}
        onEvidence={setEvidenceHit}
        onHierarchy={openHierarchy}
      />

      {hierarchyHit ? (
        <ClauseHierarchy
          editionLabel={hierarchyEdition?.product_display}
          error={hierarchyError}
          loading={hierarchyLoading}
          nodes={hierarchyNodes}
          onClose={() => {
            hierarchyController.current?.abort();
            setHierarchyHit(undefined);
          }}
        />
      ) : null}

      <EvidenceDrawer
        evidence={evidenceHit ? drawerEvidence(evidenceHit) : []}
        open={evidenceHit !== undefined}
        unavailable={evidenceHit?.evidence.length === 0}
        onClose={() => setEvidenceHit(undefined)}
      />
    </main>
  );
}
