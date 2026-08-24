import { useEffect, useRef } from "react";

import type { EvidenceRef } from "../api/generated";

export function EvidenceDrawer({
  evidence,
  open,
  unavailable = false,
  onClose,
}: {
  evidence: EvidenceRef[];
  open: boolean;
  unavailable?: boolean;
  onClose: () => void;
}) {
  const drawerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    drawerRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [onClose, open]);
  if (!open) return null;
  return (
    <div
      ref={drawerRef}
      className="evidence-drawer"
      role="dialog"
      aria-modal="true"
      aria-labelledby="evidence-drawer-title"
      tabIndex={-1}
    >
      <header>
        <div>
          <span>Evidence</span>
          <h2 id="evidence-drawer-title">근거 페이지</h2>
        </div>
        <button type="button" className="quiet-button" onClick={onClose}>
          닫기
        </button>
      </header>
      {unavailable ? (
        <p role="alert">
          근거를 확인할 수 없습니다. 원문 상태를 다시 확인해 주세요.
        </p>
      ) : (
        <ol className="evidence-list">
          {evidence.map((item) => (
            <li key={`${item.document_version_id}:${item.evidence_id}`}>
              <div className="evidence-page">
                <strong>{item.document_label}</strong>
                <span>{item.page}페이지</span>
              </div>
              <blockquote>{item.bounded_excerpt}</blockquote>
              {item.bbox ? (
                <small>
                  좌표 {item.bbox.map((value) => Math.round(value)).join(" · ")}
                </small>
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
