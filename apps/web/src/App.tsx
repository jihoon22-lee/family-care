import "./styles.css";

import { AppRoot } from "./app/AppRoot";
import { AppShell } from "./app/AppShell";
import { AppRoutes } from "./app/AppRoutes";

export function App() {
  return (
    <AppRoot>
      <AppShell>
        <AppRoutes />
      </AppShell>
    </AppRoot>
  );
}
