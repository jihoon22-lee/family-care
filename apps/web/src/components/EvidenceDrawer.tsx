import { useEffect, useId, useRef } from "react";

import type {
  EvidenceDetailResponse,
  EvidenceRef,
  GuidanceEvidenceDetail,
} from "../api/generated";
import {
  captureActiveElement,
  focusElement,
  focusHeading,
  getFocusableElements,
  restoreFocus,
} from "../app/focus";

type EvidenceDrawerItem =
  EvidenceDetailResponse | EvidenceRef | GuidanceEvidenceDetail;

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
  return "content_kind" in evidence
    ? evidence.page_start
    : isEvidenceDetail(evidence)
      ? evidence.physical_page
      : evidence.page;
}

function clauseLabel(evidence: EvidenceDrawerItem): string | null {
  return isEvidenceDetail(evidence) || "content_kind" in evidence
    ? boundedText(evidence.clause_label, MAX_CLAUSE_LABEL_LENGTH)
    : null;
}

export function EvidenceDrawer({
  evidence,
  open,
  unavailable = false,
  loading = false,
  totalCount = evidence.length,
  failedCount = unavailable ? 1 : 0,
  remainingCount = 0,
  onRetry,
  onShowMore,
  onClose,
}: {
  evidence: EvidenceDrawerItem[];
  open: boolean;
  unavailable?: boolean;
  loading?: boolean;
  totalCount?: number;
  failedCount?: number;
  remainingCount?: number;
  onRetry?: () => void;
  onShowMore?: () => void;
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
      <p>
        근거 총 {totalCount}건 · {evidence.length}건 표시
      </p>
      {loading ? (
        <p role="status">
          근거를 불러오는 중입니다. 확인된 내용은 계속 볼 수 있습니다.
        </p>
      ) : null}
      {failedCount > 0 ? (
        <p role="alert">
          근거 {failedCount}건을 불러오지 못했습니다. 확인된 근거는 유지됩니다.
        </p>
      ) : null}
      {failedCount > 0 && onRetry ? (
        <button type="button" disabled={loading} onClick={onRetry}>
          불러오지 못한 근거 다시 확인
        </button>
      ) : null}
      {evidence.length ? (
        <ol className="evidence-list" aria-label="근거 목록">
          {evidence.map((item) => {
            const page = physicalPage(item);
            const clause = clauseLabel(item);
            const detail = isEvidenceDetail(item);
            const common = "content_kind" in item ? item : undefined;
            const key = common
              ? JSON.stringify(common.evidence)
              : "evidence_id" in item
                ? item.evidence_id
                : "";
            return (
              <li key={`${item.document_version_id}:${key}`}>
                <div className="evidence-page">
                  <strong>
                    {boundedText(
                      item.document_label,
                      MAX_DOCUMENT_LABEL_LENGTH,
                    )}
                  </strong>
                  <span>
                    페이지 {page}
                    {common && common.page_end !== page
                      ? `–${common.page_end}`
                      : ""}
                    <span className="visually-hidden"> {page}페이지</span>
                  </span>
                </div>
                {detail || common ? (
                  <dl>
                    <div>
                      <dt>조항</dt>
                      <dd>{clause}</dd>
                    </div>
                    {detail ? (
                      <div>
                        <dt>확인 상태</dt>
                        <dd>
                          {item.review_state === "USER_CONFIRMED"
                            ? "사용자 확인"
                            : item.review_state === "AI_VERIFIED"
                              ? "문서 검수 확인"
                              : "추가 확인 필요"}
                        </dd>
                      </div>
                    ) : null}
                    {common?.terms_edition_id ? (
                      <div>
                        <dt>약관 판본</dt>
                        <dd>
                          {common.terms_edition_label ?? "판본 날짜 미확인"}
                        </dd>
                      </div>
                    ) : null}
                  </dl>
                ) : null}
                {common ? (
                  <>
                    <p>
                      {common.content_kind === "ORIGINAL"
                        ? "원문 발췌"
                        : common.content_kind === "SUMMARY"
                          ? "저장된 자료 요약 · 원문과 다를 수 있습니다"
                          : "해당 근거 내용을 불러오지 못했습니다"}
                    </p>
                    {common.text ? (
                      <blockquote>{boundedText(common.text, 2048)}</blockquote>
                    ) : null}
                    {common.truncated ? (
                      <p>긴 내용 중 일부를 표시했습니다.</p>
                    ) : null}
                  </>
                ) : "bounded_excerpt" in item ? (
                  <blockquote>
                    {boundedText(item.bounded_excerpt, MAX_EXCERPT_LENGTH)}
                  </blockquote>
                ) : null}
              </li>
            );
          })}
        </ol>
      ) : null}
      {remainingCount > 0 && onShowMore ? (
        <button type="button" disabled={loading} onClick={onShowMore}>
          근거 더 보기 · 남은 {remainingCount}건
        </button>
      ) : null}
    </div>
  );
}

export type { EvidenceDrawerItem };
