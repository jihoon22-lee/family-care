import type { PropsWithChildren } from "react";

export function AppShell({ children }: PropsWithChildren) {
  return (
    <div className="app-shell ledger-shell">
      <a className="skip-link" href="#main-content">
        본문으로 건너뛰기
      </a>
      <header className="masthead ledger-masthead">
        <a className="wordmark" href="/" aria-label="FamilyCare 홈">
          <span className="wordmark-mark" aria-hidden="true">
            FC
          </span>
          <span>FamilyCare</span>
        </a>
        <div className="masthead-context">
          <span className="context-kicker">Evidence-bound ledger</span>
          <span>보장 원장</span>
        </div>
        <nav className="primary-nav" aria-label="주요 화면">
          <a href="/app/ledger">보장 원장</a>
          <a href="/app/clauses/search">약관 검색</a>
        </nav>
      </header>
      {children}
      <footer className="page-footer ledger-footer">
        <span>보험금 지급을 보장하지 않습니다.</span>
        <span>MATCH · UNKNOWN · NO_MATCH는 근거에 따라 구분합니다.</span>
      </footer>
    </div>
  );
}
