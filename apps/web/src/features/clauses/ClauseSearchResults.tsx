import type { ClauseSearchHit } from "../../api/clauses";

const MAX_EXCERPT_CHARS = 320;

function boundedExcerpt(excerpt: string): string {
  if (excerpt.length <= MAX_EXCERPT_CHARS) return excerpt;
  return `${excerpt.slice(0, MAX_EXCERPT_CHARS - 1)}…`;
}

function pageRange(hit: ClauseSearchHit): string {
  return hit.physical_page_start === hit.physical_page_end
    ? `${hit.physical_page_start}`
    : `${hit.physical_page_start}–${hit.physical_page_end}`;
}

export function ClauseSearchResults({
  hits,
  loading,
  hasSearched,
  onEvidence,
  onHierarchy,
}: {
  hits: ClauseSearchHit[];
  loading: boolean;
  hasSearched: boolean;
  onEvidence: (hit: ClauseSearchHit) => void;
  onHierarchy: (hit: ClauseSearchHit) => void;
}) {
  if (loading) {
    return (
      <p className="loading-state" role="status" aria-live="polite">
        약관 조항을 찾는 중입니다.
      </p>
    );
  }

  if (!hasSearched) {
    return (
      <section className="empty-state clause-empty-state">
        <h2>검색어를 입력해 주세요.</h2>
        <p>
          검색어는 이 화면에만 잠시 보관되며 URL이나 브라우저 저장소에 남지
          않습니다.
        </p>
      </section>
    );
  }

  if (hits.length === 0) {
    return (
      <section className="empty-state clause-empty-state">
        <h2>일치하는 조항이 없습니다.</h2>
        <p>다른 표현이나 약관 판본을 선택해 다시 검색해 보세요.</p>
      </section>
    );
  }

  return (
    <ol className="clause-result-list" aria-label="약관 검색 결과">
      {hits.map((hit) => (
        <li className="clause-result-card" key={hit.clause_id}>
          <div className="clause-result-heading">
            <div>
              <span className="clause-type-label">Clause</span>
              <h3>{hit.label}</h3>
            </div>
            {typeof hit.relevance === "number" ? (
              <span className="clause-relevance" aria-label="검색 관련도">
                {Math.round(hit.relevance * 100)}%
              </span>
            ) : null}
          </div>
          <p className="clause-excerpt">{boundedExcerpt(hit.excerpt)}</p>
          <div className="clause-result-meta">
            <span>Physical page / 물리 페이지 {pageRange(hit)}</span>
            <span>{hit.evidence.length}개 근거</span>
          </div>
          <div className="clause-result-actions">
            <button
              type="button"
              className="secondary-button"
              onClick={() => onEvidence(hit)}
            >
              근거 보기 Evidence
            </button>
            <button
              type="button"
              className="quiet-button"
              onClick={() => onHierarchy(hit)}
            >
              조항 계층 보기
            </button>
          </div>
        </li>
      ))}
    </ol>
  );
}
