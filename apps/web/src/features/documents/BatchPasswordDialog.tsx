import { useEffect, useRef, useState, type FormEvent } from "react";

import { trapDialogFocus } from "../identity/dialogFocus";

export function BatchPasswordDialog({
  busy = false,
  error,
  onCancel,
  onSubmit,
  open,
}: {
  busy?: boolean;
  error?: string;
  onCancel: () => void;
  onSubmit: (password: string) => Promise<void> | void;
  open: boolean;
}) {
  const [password, setPassword] = useState("");
  const dialogRef = useRef<HTMLElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) {
      setPassword("");
      return undefined;
    }
    returnFocusRef.current = document.activeElement as HTMLElement | null;
    inputRef.current?.focus();
    return () => {
      setPassword("");
      queueMicrotask(() => returnFocusRef.current?.focus());
    };
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
    if (!password) return;
    const submitted = password;
    setPassword("");
    try {
      await onSubmit(submitted);
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
        aria-labelledby="batch-password-title"
        aria-modal="true"
        className="identity-dialog import-password-dialog"
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <div className="dialog-heading">
          <span>Encrypted PDF</span>
          <h2 id="batch-password-title">PDF 비밀번호 입력</h2>
        </div>
        <p>
          비밀번호가 필요한 항목만 다시 처리합니다. 입력값은 이 요청 뒤에
          화면에서 지워집니다.
        </p>
        <form
          className="identity-form"
          onSubmit={(event) => void submit(event)}
        >
          <div className="identity-field">
            <label htmlFor="batch-pdf-password">PDF 비밀번호</label>
            <input
              autoComplete="off"
              id="batch-pdf-password"
              maxLength={8192}
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
            <button disabled={busy} onClick={cancel} type="button">
              닫기
            </button>
            <button
              className="identity-primary-button"
              disabled={busy || !password}
              type="submit"
            >
              {busy ? "전달 중…" : "다시 처리"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
