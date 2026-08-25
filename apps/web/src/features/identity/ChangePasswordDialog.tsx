import { useEffect, useRef, useState, type FormEvent } from "react";

import { trapDialogFocus } from "./dialogFocus";

export interface ChangePasswordDialogProps {
  open: boolean;
  busy?: boolean;
  error?: string;
  onCancel: () => void;
  onSubmit: (newPassword: string) => Promise<void> | void;
}

export function ChangePasswordDialog({
  open,
  busy = false,
  error,
  onCancel,
  onSubmit,
}: ChangePasswordDialogProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [validationError, setValidationError] = useState<string>();

  useEffect(() => {
    if (open) {
      returnFocusRef.current = document.activeElement as HTMLElement | null;
      inputRef.current?.focus();
      return () => queueMicrotask(() => returnFocusRef.current?.focus());
    }
    setNewPassword("");
    setConfirmation("");
    setValidationError(undefined);
    return undefined;
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        event.preventDefault();
        setNewPassword("");
        setConfirmation("");
        setValidationError(undefined);
        onCancel();
        return;
      }
      trapDialogFocus(event, dialogRef.current);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (newPassword.length < 16) {
      setValidationError("새 비밀번호는 16자 이상이어야 합니다.");
      return;
    }
    if (newPassword !== confirmation) {
      setValidationError("새 비밀번호가 서로 일치하지 않습니다.");
      return;
    }
    setValidationError(undefined);
    const submittedPassword = newPassword;
    setNewPassword("");
    setConfirmation("");
    try {
      await onSubmit(submittedPassword);
    } finally {
      setNewPassword("");
      setConfirmation("");
    }
  }

  function cancel(): void {
    setNewPassword("");
    setConfirmation("");
    setValidationError(undefined);
    onCancel();
  }

  if (!open) return null;
  const displayedError = validationError ?? error;
  return (
    <div className="identity-dialog-backdrop">
      <section
        aria-labelledby="change-password-title"
        aria-modal="true"
        className="identity-dialog"
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <div className="dialog-heading">
          <span>Account access</span>
          <h2 id="change-password-title">비밀번호 변경</h2>
        </div>
        <p>변경이 완료되면 모든 기기에서 다시 로그인해야 합니다.</p>
        <form
          className="identity-form"
          noValidate
          onSubmit={(event) => void submit(event)}
        >
          <div className="identity-field">
            <label htmlFor="new-password">새 비밀번호</label>
            <input
              autoComplete="new-password"
              id="new-password"
              minLength={16}
              ref={inputRef}
              required
              type="password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
            />
          </div>
          <div className="identity-field">
            <label htmlFor="new-password-confirmation">새 비밀번호 확인</label>
            <input
              autoComplete="new-password"
              id="new-password-confirmation"
              required
              type="password"
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
            />
          </div>
          {displayedError ? (
            <p className="identity-error" role="alert">
              {displayedError}
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
              {busy ? "변경 중…" : "비밀번호 변경"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
