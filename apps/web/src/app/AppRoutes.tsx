import { ClauseSearchPage } from "../features/clauses/ClauseSearchPage";
import { LedgerPage } from "../features/ledger/LedgerPage";

function routeMemberId(): string | undefined {
  const match = window.location.pathname.match(
    /^\/app\/members\/([^/]+)\/ledger\/?$/,
  );
  return match ? decodeURIComponent(match[1]) : undefined;
}

export function AppRoutes() {
  if (/^\/app\/clauses\/search\/?$/.test(window.location.pathname)) {
    return <ClauseSearchPage />;
  }
  return <LedgerPage memberId={routeMemberId()} />;
}
