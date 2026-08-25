import { useEffect, useId, useRef } from "react";

import type { EvidenceDetailResponse, EvidenceRef } from "../api/generated";
import {
  captureActiveElement,
  focusElement,
  focusHeading,
  getFocusableElements,
  restoreFocus,
} from "../app/focus";

type EvidenceDrawerItem = EvidenceDetailResponse | EvidenceRef;

const MAX_DOCUMENT_LABEL_LENGTH = 160;
const MAX_CLAUSE_LABEL_LENGTH = 160;
const MAX_EXCERPT_LENGTH = 320;

function boundedText(value: string | null | undefined, maxLength: number) {
  return value?.trim().slice(0, maxLength) || "확인되지 않음";
}

function isEvidenceDetail(
  evidence: EvidenceDrawerItem,
): evidence is EvidenceDetailResponse {
  return "physical_page" in evidence && "review_state" in evidence;
}

function physicalPage(evidence: EvidenceDrawerItem): number {
  return isEvidenceDetail(evidence) ? evidence.physical_page : evidence.page;
}

function clauseLabel(evidence: EvidenceDrawerItem): string | null {
  return isEvidenceDetail(evidence)
    ? boundedText(evidence.clause_label, MAX_CLAUSE_LABEL_LENGTH)
    : null;
}

function formatBbox(bbox: readonly number[] | null): string {
  if (!bbox || bbox.length !== 4) return "좌표 확인 필요";
  return bbox
    .map((value) =>
      Number.isFinite(value) ? String(Math.round(value * 1000) / 1000) : "?",
    )
    .join(" · ");
}

export function EvidenceDrawer({
  evidence,
  open,
  unavailable = false,
  onClose,
}: {
  evidence: EvidenceDrawerItem[];
  open: boolean;
  unavailable?: boolean;
  onClose: () => void;
}) {
  const drawerRef = useRef<HTMLDivElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);
  const closeRef = useRef(onClose);
  const titleId = useId();
  closeRef.current = onClose;

  useEffect(() => {
    if (!open) return;

    restoreRef.current = captureActiveElement();
    focusHeading(headingRef.current);

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeRef.current();
        return;
      }
      if (event.key !== "Tab" || !drawerRef.current) return;
      if (!drawerRef.current.contains(document.activeElement)) return;

      const focusable = getFocusableElements(drawerRef.current);
      if (focusable.length === 0) {
        event.preventDefault();
        focusHeading(headingRef.current);
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (document.activeElement === headingRef.current) {
        event.preventDefault();
        focusElement(event.shiftKey ? last : first);
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        focusElement(last);
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        focusElement(first);
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      restoreFocus(restoreRef.current);
      restoreRef.current = null;
    };
  }, [open]);

  if (!open) return null;

  return (
    <div
      ref={drawerRef}
      className="evidence-drawer"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      tabIndex={-1}
    >
      <header>
        <div>
          <span>Evidence</span>
          <h2 ref={headingRef} id={titleId} tabIndex={-1}>
            증권과 약관 근거
          </h2>
        </div>
        <button
          type="button"
          className="quiet-button"
          onClick={() => closeRef.current()}
        >
          닫기
        </button>
      </header>
      {unavailable ? (
        <p role="alert">
          EVIDENCE_UNAVAILABLE · 근거를 확인할 수 없습니다. 원문 상태를 다시
          확인해 주세요.
        </p>
      ) : (
        <ol className="evidence-list" aria-label="근거 목록">
          {evidence.map((item) => {
            const page = physicalPage(item);
            const clause = clauseLabel(item);
            const detail = isEvidenceDetail(item);
            return (
              <li key={`${item.document_version_id}:${item.evidence_id}`}>
                <div className="evidence-page">
                  <strong>
                    {boundedText(
                      item.document_label,
                      MAX_DOCUMENT_LABEL_LENGTH,
                    )}
                  </strong>
                  <span>
                    페이지 {page}
                    <span className="visually-hidden"> {page}페이지</span>
                  </span>
                </div>
                {detail ? (
                  <dl>
                    <div>
                      <dt>조항</dt>
                      <dd>{clause}</dd>
                    </div>
                    <div>
                      <dt>검수 상태</dt>
                      <dd>{item.review_state}</dd>
                    </div>
                  </dl>
                ) : null}
                <blockquote>
                  {boundedText(item.bounded_excerpt, MAX_EXCERPT_LENGTH)}
                </blockquote>
                <small>좌표 {formatBbox(item.bbox)}</small>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

export type { EvidenceDrawerItem };
