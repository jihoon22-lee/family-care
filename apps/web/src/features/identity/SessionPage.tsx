import { useEffect, useState } from "react";

import { ApiError } from "../../api/errors";
import {
  changePassword,
  clearAuthState,
  listSessions,
  logout,
  reauthenticate,
  revokeSession,
  type AuthSession,
} from "./authApi";
import { useAuthStore } from "./authStore";
import { ChangePasswordDialog } from "./ChangePasswordDialog";
import { ReauthenticateDialog } from "./ReauthenticateDialog";

type SensitiveAction =
  { kind: "revoke"; session: AuthSession } | { kind: "change-password" } | null;

export interface SessionPageProps {
  onLoggedOut?: () => void;
}

function navigateToLogin(): void {
  window.history.replaceState(null, "", "/login");
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function stableErrorCopy(error: unknown, fallback: string): string {
  if (error instanceof ApiError && error.code === "NETWORK_ERROR") {
    return "연결할 수 없습니다. 잠시 후 다시 시도해 주세요.";
  }
  return fallback;
}

function sessionLabel(session: AuthSession): string {
  return session.current
    ? `${session.device_label} (현재 기기)`
    : session.device_label;
}

export function SessionPage({ onLoggedOut }: SessionPageProps) {
  const auth = useAuthStore();
  const [sessions, setSessions] = useState<AuthSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [actionError, setActionError] = useState<string>();
  const [sensitiveAction, setSensitiveAction] = useState<SensitiveAction>(null);
  const [reauthOpen, setReauthOpen] = useState(false);
  const [changePasswordOpen, setChangePasswordOpen] = useState(false);

  async function refreshSessions(): Promise<void> {
    setLoading(true);
    setError(undefined);
    try {
      setSessions(await listSessions());
    } catch (reason: unknown) {
      setError(
        stableErrorCopy(
          reason,
          "기기 세션을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.",
        ),
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refreshSessions();
  }, []);

  function openReauthentication(action: SensitiveAction): void {
    setSensitiveAction(action);
    setActionError(undefined);
    setReauthOpen(true);
  }

  async function submitReauthentication(password: string): Promise<void> {
    setBusy(true);
    setActionError(undefined);
    try {
      await reauthenticate(password);
      setReauthOpen(false);
      if (sensitiveAction?.kind === "revoke") {
        await revokeSession(sensitiveAction.session.session_id);
        setSensitiveAction(null);
        await refreshSessions();
      } else if (sensitiveAction?.kind === "change-password") {
        setSensitiveAction(null);
        setChangePasswordOpen(true);
      }
    } catch (reason: unknown) {
      setActionError(
        stableErrorCopy(
          reason,
          "다시 인증하지 못했습니다. 비밀번호를 확인해 주세요.",
        ),
      );
    } finally {
      setBusy(false);
    }
  }

  async function submitPasswordChange(newPassword: string): Promise<void> {
    setBusy(true);
    setActionError(undefined);
    try {
      await changePassword(newPassword);
      clearAuthState();
      setChangePasswordOpen(false);
      if (onLoggedOut) onLoggedOut();
      else navigateToLogin();
    } catch (reason: unknown) {
      setActionError(
        stableErrorCopy(
          reason,
          "비밀번호를 변경하지 못했습니다. 잠시 후 다시 시도해 주세요.",
        ),
      );
    } finally {
      setBusy(false);
    }
  }

  async function signOut(): Promise<void> {
    setBusy(true);
    setActionError(undefined);
    try {
      await logout();
    } catch (reason: unknown) {
      setActionError(
        stableErrorCopy(
          reason,
          "로그아웃 요청을 완료하지 못했습니다. 로컬 화면은 닫습니다.",
        ),
      );
    } finally {
      clearAuthState();
      setBusy(false);
      if (onLoggedOut) onLoggedOut();
      else navigateToLogin();
    }
  }

  return (
    <main
      className="identity-page session-page"
      id="main-content"
      tabIndex={-1}
    >
      <section
        className="identity-card session-card"
        aria-labelledby="session-title"
      >
        <div className="session-heading">
          <div>
            <p className="identity-kicker">Account · Device sessions</p>
            <h1 id="session-title">기기 세션</h1>
          </div>
          <button
            className="identity-secondary-button"
            disabled={busy}
            onClick={() => void signOut()}
            type="button"
          >
            로그아웃
          </button>
        </div>
        <p className="identity-lead">
          {auth.user?.display_name ?? auth.user?.username ?? "현재 관리자"}{" "}
          계정으로 열린 세션을 확인하고 폐기할 수 있습니다.
        </p>
        {auth.user?.needs_reauthentication ? (
          <p className="identity-warning" role="status">
            민감한 작업을 계속하려면 다시 인증해 주세요.
          </p>
        ) : null}
        {error ? (
          <p className="identity-error" role="alert">
            {error}
          </p>
        ) : null}
        {actionError ? (
          <p className="identity-error" role="alert">
            {actionError}
          </p>
        ) : null}
        {loading ? (
          <p className="identity-loading" role="status" aria-live="polite">
            세션을 불러오는 중입니다.
          </p>
        ) : null}
        {!loading && !error ? (
          <ul className="session-list" aria-label="기기 세션 목록">
            {sessions.map((session) => (
              <li className="session-list-item" key={session.session_id}>
                <div>
                  <strong>{sessionLabel(session)}</strong>
                  <span>
                    마지막 확인 {session.last_seen_at} · 만료{" "}
                    {session.expires_at}
                  </span>
                </div>
                <button
                  className="identity-secondary-button"
                  disabled={busy || session.current}
                  onClick={() =>
                    openReauthentication({ kind: "revoke", session })
                  }
                  type="button"
                >
                  {session.current ? "현재 사용 중" : "이 세션 폐기"}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        {!loading && !error && sessions.length === 0 ? (
          <p className="identity-note">표시할 다른 기기 세션이 없습니다.</p>
        ) : null}
        <div className="session-actions">
          <button
            className="identity-secondary-button"
            disabled={busy}
            onClick={() => openReauthentication({ kind: "change-password" })}
            type="button"
          >
            비밀번호 변경
          </button>
          <button
            className="identity-secondary-button"
            disabled={busy}
            onClick={() => void refreshSessions()}
            type="button"
          >
            새로 고침
          </button>
        </div>
      </section>
      <ReauthenticateDialog
        busy={busy}
        error={actionError}
        onCancel={() => {
          setReauthOpen(false);
          setSensitiveAction(null);
          setActionError(undefined);
        }}
        onSubmit={submitReauthentication}
        open={reauthOpen}
      />
      <ChangePasswordDialog
        busy={busy}
        error={actionError}
        onCancel={() => {
          setChangePasswordOpen(false);
          setActionError(undefined);
        }}
        onSubmit={submitPasswordChange}
        open={changePasswordOpen}
      />
    </main>
  );
}
