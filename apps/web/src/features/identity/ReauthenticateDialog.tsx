import { useEffect, useRef, useState, type FormEvent } from "react";

import { trapDialogFocus } from "./dialogFocus";

export interface ReauthenticateDialogProps {
  open: boolean;
  busy?: boolean;
  error?: string;
  onCancel: () => void;
  onSubmit: (password: string) => Promise<void> | void;
}

export function ReauthenticateDialog({
  open,
  busy = false,
  error,
  onCancel,
  onSubmit,
}: ReauthenticateDialogProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const [password, setPassword] = useState("");

  useEffect(() => {
    if (open) {
      returnFocusRef.current = document.activeElement as HTMLElement | null;
      inputRef.current?.focus();
      return () => queueMicrotask(() => returnFocusRef.current?.focus());
    }
    setPassword("");
    return undefined;
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        event.preventDefault();
        setPassword("");
        onCancel();
        return;
      }
      trapDialogFocus(event, dialogRef.current);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onCancel, open]);

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const submittedPassword = password;
    setPassword("");
    try {
      await onSubmit(submittedPassword);
    } finally {
      setPassword("");
    }
  }

  function cancel(): void {
    setPassword("");
    onCancel();
  }

  if (!open) return null;
  return (
    <div className="identity-dialog-backdrop">
      <section
        aria-labelledby="reauthenticate-title"
        aria-modal="true"
        className="identity-dialog"
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <div className="dialog-heading">
          <span>Security boundary</span>
          <h2 id="reauthenticate-title">다시 인증이 필요합니다</h2>
        </div>
        <p>비밀번호를 다시 확인한 뒤 민감한 세션 작업을 계속할 수 있습니다.</p>
        <form
          className="identity-form"
          onSubmit={(event) => void submit(event)}
        >
          <div className="identity-field">
            <label htmlFor="reauthenticate-password">비밀번호</label>
            <input
              autoComplete="current-password"
              id="reauthenticate-password"
              ref={inputRef}
              required
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>
          {error ? (
            <p className="identity-error" role="alert">
              {error}
            </p>
          ) : null}
          <div className="identity-dialog-actions">
            <button onClick={cancel} type="button">
              취소
            </button>
            <button
              className="identity-primary-button"
              disabled={busy}
              type="submit"
            >
              {busy ? "확인 중…" : "확인"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
