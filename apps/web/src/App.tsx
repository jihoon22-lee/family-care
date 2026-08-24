import "./styles.css";

import { AppRoot } from "./app/AppRoot";
import { AppShell } from "./app/AppShell";
import { LedgerPage } from "./features/ledger/LedgerPage";

function routeMemberId(): string | undefined {
  const match = window.location.pathname.match(
    /^\/app\/members\/([^/]+)\/ledger\/?$/,
  );
  return match ? decodeURIComponent(match[1]) : undefined;
}

export function App() {
  return (
    <AppRoot>
      <AppShell>
        <LedgerPage memberId={routeMemberId()} />
      </AppShell>
    </AppRoot>
  );
}
