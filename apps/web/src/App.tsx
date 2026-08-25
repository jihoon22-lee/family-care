import "./styles.css";

import { useEffect, useState } from "react";

import { AppRoot } from "./app/AppRoot";
import { AppShell } from "./app/AppShell";
import { AppRoutes } from "./app/AppRoutes";
import { ApiError } from "./api/errors";
import { loadCurrentUser } from "./features/identity/authApi";
import { LoginPage } from "./features/identity/LoginPage";
import { SessionPage } from "./features/identity/SessionPage";
import { authStore, useAuthStore } from "./features/identity/authStore";

function usePathname(): string {
  const [pathname, setPathname] = useState(() => window.location.pathname);
  useEffect(() => {
    const update = () => setPathname(window.location.pathname);
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, []);
  return pathname;
}

function navigateToLogin(): void {
  window.history.replaceState(null, "", "/login");
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function AuthenticatedApp() {
  const pathname = usePathname();
  const auth = useAuthStore();

  useEffect(() => {
    if (authStore.getSnapshot().status === "authenticated") return undefined;
    let active = true;
    authStore.setLoading();
    void loadCurrentUser().catch((reason: unknown) => {
      if (!active) return;
      if (
        reason instanceof ApiError &&
        reason.code === "AUTHENTICATION_REQUIRED"
      ) {
        authStore.clear();
        navigateToLogin();
        return;
      }
      if (reason instanceof ApiError) authStore.setError(reason);
      else authStore.clear();
    });
    return () => {
      active = false;
    };
  }, [pathname]);

  useEffect(() => {
    if (auth.status === "unauthenticated") navigateToLogin();
  }, [auth.status]);

  if (auth.status === "loading" || auth.status === "unknown") {
    return (
      <main className="identity-page" id="main-content" tabIndex={-1}>
        <p className="identity-loading" role="status" aria-live="polite">
          로그인 상태를 확인하는 중입니다.
        </p>
      </main>
    );
  }
  if (auth.status === "error") {
    return (
      <main className="identity-page" id="main-content" tabIndex={-1}>
        <p className="identity-error" role="alert">
          로그인 상태를 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.
        </p>
      </main>
    );
  }
  if (auth.status !== "authenticated") return null;

  if (/^\/app\/(?:settings\/sessions|sessions)\/?$/.test(pathname)) {
    return (
      <AppShell>
        <SessionPage onLoggedOut={navigateToLogin} />
      </AppShell>
    );
  }
  return (
    <AppShell>
      <AppRoutes />
    </AppShell>
  );
}

function Application() {
  const pathname = usePathname();
  if (/^\/(?:login|auth\/login)\/?$/.test(pathname)) return <LoginPage />;
  return <AuthenticatedApp />;
}

export function App() {
  return (
    <AppRoot>
      <Application />
    </AppRoot>
  );
}
