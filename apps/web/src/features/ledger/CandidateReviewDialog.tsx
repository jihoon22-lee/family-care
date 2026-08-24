import { useCallback, useEffect, useRef, useState } from "react";

import type { PolicyReviewItem } from "../../api/generated";
import { confirmReviewItem, rejectReviewItem } from "../../api/ledger";
import { CandidateFieldEditor } from "./CandidateFieldEditor";

const ISSUE_COPY = {
  CONFLICTING_EVIDENCE: "서로 다른 근거가 있어 하나를 선택할 수 없습니다.",
  INVALID_DATE: "날짜의 앞뒤 관계를 다시 확인해야 합니다.",
  INVALID_UNIT: "금액 또는 단위를 다시 확인해야 합니다.",
  LOW_CONFIDENCE: "근거와 후보 값의 일치 여부를 확인해 주세요.",
  MISSING_EVIDENCE: "후보 값을 뒷받침하는 근거가 필요합니다.",
  TERMS_ONLY_RIDER: "약관에서만 확인된 후보는 가입 담보로 등록하지 않습니다.",
  UNSUPPORTED_STRUCTURE: "자동으로 구조화하기 어려운 항목입니다.",
} as const;

function evidenceIsComplete(item: PolicyReviewItem): boolean {
  const known = new Set(item.evidence.map((evidence) => evidence.evidence_id));
  return (
    known.size > 0 &&
    item.fields.every(
      (field) =>
        field.evidence_ids.length > 0 &&
        field.evidence_ids.every((id) => known.has(id)),
    )
  );
}

export function CandidateReviewDialog({
  item: initialItem,
  onClose,
  onConfirmed,
}: {
  item: PolicyReviewItem;
  onClose: () => void;
  onConfirmed: () => void;
}) {
  const [item, setItem] = useState(initialItem);
  const [rejecting, setRejecting] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string>();
  const dialogRef = useRef<HTMLDivElement>(null);
  const evidenceComplete = evidenceIsComplete(item);

  const close = useCallback(() => onClose(), [onClose]);
  useEffect(() => {
    dialogRef.current?.focus();
    const handleDialogKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        close();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
        ),
      );
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) return;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleDialogKey);
    return () => document.removeEventListener("keydown", handleDialogKey);
  }, [close]);

  async function confirm() {
    if (!evidenceComplete) return;
    setWorking(true);
    setError(undefined);
    try {
      await confirmReviewItem(item.review_item_id, {
        expected_version: item.expected_version,
      });
      onConfirmed();
      close();
    } catch {
      setError("후보를 확인 상태로 저장하지 못했습니다.");
    } finally {
      setWorking(false);
    }
  }

  async function reject() {
    setWorking(true);
    setError(undefined);
    try {
      await rejectReviewItem(item.review_item_id, {
        expected_version: item.expected_version,
        reason_code: item.issues.some(
          (issue) => issue.code === "TERMS_ONLY_RIDER",
        )
          ? "TERMS_ONLY_RIDER"
          : "INVALID_EVIDENCE",
      });
      onConfirmed();
      close();
    } catch {
      setError("후보를 거절 상태로 저장하지 못했습니다.");
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation">
      <div
        ref={dialogRef}
        className="candidate-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="candidate-dialog-title"
        tabIndex={-1}
      >
        <header className="dialog-heading">
          <div>
            <span>Candidate review</span>
            <h2 id="candidate-dialog-title">추가 확인 필요</h2>
          </div>
          <button type="button" className="quiet-button" onClick={close}>
            닫기
          </button>
        </header>

        <ul className="issue-list" aria-label="검토 사유">
          {item.issues.map((issue) => (
            <li key={`${issue.code}:${issue.field_id ?? "all"}`}>
              <code>{issue.code}</code>
              <span>{ISSUE_COPY[issue.code]}</span>
            </li>
          ))}
        </ul>

        {!evidenceComplete ? (
          <p role="alert">확인하려면 각 후보 값의 근거가 필요합니다.</p>
        ) : null}
        <section className="dialog-evidence" aria-label="후보 근거">
          {item.evidence.map((evidence) => (
            <article key={evidence.evidence_id}>
              <header>
                <strong>{evidence.document_label}</strong>
                <span>{evidence.page}페이지</span>
              </header>
              <blockquote>{evidence.bounded_excerpt}</blockquote>
            </article>
          ))}
        </section>

        <CandidateFieldEditor item={item} onSaved={setItem} />
        {error ? <p role="alert">{error}</p> : null}

        <footer className="dialog-actions">
          {rejecting ? (
            <>
              <span>이 후보를 원장에 반영하지 않습니다.</span>
              <button
                type="button"
                className="danger-button"
                disabled={working}
                onClick={reject}
              >
                거절 확정
              </button>
              <button
                type="button"
                className="quiet-button"
                onClick={() => setRejecting(false)}
              >
                취소
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className="primary-button"
                disabled={working || !evidenceComplete}
                onClick={confirm}
              >
                확인
              </button>
              <button
                type="button"
                className="quiet-button"
                onClick={() => setRejecting(true)}
              >
                거절
              </button>
            </>
          )}
        </footer>
      </div>
    </div>
  );
}

export { ISSUE_COPY };
