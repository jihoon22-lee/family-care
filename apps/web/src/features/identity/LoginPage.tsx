import { useEffect, useRef, useState } from "react";

import { ApiError } from "../../api/errors";
import { authHeaders, clearAuthState, loadCsrfToken, login } from "./authApi";
import type { AuthUser } from "./authStore";

export interface LoginPageProps {
  onAuthenticated?: (user: AuthUser) => void;
}

function navigateToApp(): void {
  window.history.replaceState(null, "", "/app/ledger");
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function loginErrorCopy(error: unknown): string {
  if (error instanceof ApiError && error.code === "NETWORK_ERROR") {
    return "로그인 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.";
  }
  return "로그인에 실패했습니다. 사용자 이름 또는 비밀번호를 확인해 주세요.";
}

export function LoginPage({ onAuthenticated }: LoginPageProps) {
  const usernameRef = useRef<HTMLInputElement>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    usernameRef.current?.focus();
    return () => {
      // Passwords never leave this component's memory and are cleared when
      // the screen is abandoned, including route changes and session expiry.
      setPassword("");
    };
  }, []);

  async function submit(
    event: React.FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(undefined);
    const submittedPassword = password;
    setPassword("");
    try {
      const user = await login(username.trim(), submittedPassword);
      if (Object.keys(authHeaders()).length === 0) await loadCsrfToken();
      if (onAuthenticated) onAuthenticated(user);
      else navigateToApp();
    } catch (reason: unknown) {
      clearAuthState();
      setError(loginErrorCopy(reason));
    } finally {
      // Explicitly clear the local copy on success, failure, and cancellation
      // paths. JavaScript strings cannot be zeroed in place, so references are
      // dropped as soon as the request settles.
      setPassword("");
      setBusy(false);
    }
  }

  return (
    <main className="identity-page" id="main-content" tabIndex={-1}>
      <section className="identity-card" aria-labelledby="login-title">
        <p className="identity-kicker">FamilyCare · Local access</p>
        <h1 id="login-title">로그인</h1>
        <p className="identity-lead">
          가족 보장 원장은 허용된 로컬 관리자 계정으로만 열 수 있습니다.
        </p>
        <form
          className="identity-form"
          onSubmit={(event) => void submit(event)}
        >
          <div className="identity-field">
            <label htmlFor="login-username">사용자 이름</label>
            <input
              autoComplete="username"
              id="login-username"
              name="username"
              ref={usernameRef}
              required
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </div>
          <div className="identity-field">
            <label htmlFor="login-password">비밀번호</label>
            <input
              autoComplete="current-password"
              id="login-password"
              name="password"
              required
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>
          {error ? (
            <p className="identity-error" role="alert" aria-label={error}>
              {error}
            </p>
          ) : null}
          <button
            className="identity-primary-button"
            disabled={busy}
            type="submit"
          >
            {busy ? "확인 중…" : "로그인"}
          </button>
        </form>
        <p className="identity-note">
          가입, 이메일 재설정, 초대 기능은 제공하지 않습니다.
        </p>
      </section>
    </main>
  );
}
