import { useCallback, useEffect, useRef, useState } from "react";

import type {
  EvidenceRef,
  PolicyReviewItem,
  RiderClauseLinkResponse,
} from "../../api/generated";
import {
  confirmRiderClauseLink,
  listRiderClauseLinks,
  rejectRiderClauseLink,
} from "../../api/rules";
import { EvidenceDrawer } from "../../components/EvidenceDrawer";

function fieldValue(
  item: PolicyReviewItem,
  fieldId: string,
): string | undefined {
  const value = item.fields.find((field) => field.field_id === fieldId)?.value;
  return typeof value === "string" ? value : undefined;
}

function drawerEvidence(item: PolicyReviewItem): EvidenceRef[] {
  return item.evidence.map((evidence) => ({
    bbox: evidence.bbox,
    bounded_excerpt: evidence.bounded_excerpt.slice(0, 320),
    document_label: evidence.document_label,
    document_version_id: evidence.document_version_id,
    evidence_id: evidence.evidence_id,
    page: evidence.page,
  }));
}

export function RiderClauseReviewDialog({
  item,
  onClose,
  onMutated,
}: {
  item: PolicyReviewItem;
  onClose: () => void;
  onMutated: () => void;
}) {
  const [link, setLink] = useState<RiderClauseLinkResponse>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [working, setWorking] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const close = useCallback(onClose, [onClose]);
  const riderId = fieldValue(item, "rider_id");

  useEffect(() => {
    dialogRef.current?.focus();
    const controller = new AbortController();
    if (!riderId || !item.aggregate_id) {
      setLoading(false);
      setError("연결 후보의 식별 정보를 확인할 수 없습니다.");
      return () => controller.abort();
    }
    void listRiderClauseLinks(riderId, controller.signal)
      .then((links) => {
        const selected = links.find(
          (candidate) => candidate.link_id === item.aggregate_id,
        );
        setLink(selected);
        if (!selected) setError("검토할 연결을 찾지 못했습니다.");
      })
      .catch(() => setError("담보와 약관 연결을 불러오지 못했습니다."))
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [item.aggregate_id, riderId]);

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (!evidenceOpen) close();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current || evidenceOpen) return;
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
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [close, evidenceOpen]);

  async function confirm() {
    if (!link || item.evidence.length === 0) return;
    setWorking(true);
    setError(undefined);
    try {
      await confirmRiderClauseLink(link.link_id, {
        expected_version: link.version,
      });
      onMutated();
      close();
    } catch {
      setError("새 근거를 불러온 뒤 연결을 다시 확인해 주세요.");
    } finally {
      setWorking(false);
    }
  }

  async function reject() {
    if (!link) return;
    setWorking(true);
    setError(undefined);
    try {
      await rejectRiderClauseLink(link.link_id, {
        expected_version: link.version,
        reason_code: item.issues.some((issue) => issue.code === "WRONG_EDITION")
          ? "WRONG_EDITION"
          : "NOT_APPLICABLE",
      });
      onMutated();
      close();
    } catch {
      setError("연결 제외 상태를 저장하지 못했습니다.");
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation">
      <div
        ref={dialogRef}
        className="candidate-dialog rule-review-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="rider-clause-dialog-title"
        tabIndex={-1}
      >
        <header className="dialog-heading">
          <div>
            <span>Evidence connection</span>
            <h2 id="rider-clause-dialog-title">담보와 약관 연결 검토</h2>
          </div>
          <button type="button" className="quiet-button" onClick={close}>
            닫기
          </button>
        </header>
        {loading ? <p role="status">연결 근거를 확인하는 중입니다.</p> : null}
        {link ? (
          <>
            <dl className="rule-link-pair">
              <div>
                <dt>가입 담보</dt>
                <dd>{link.rider_label ?? "확인된 담보"}</dd>
              </div>
              <div>
                <dt>적용 조항</dt>
                <dd>{link.clause_label ?? "확인된 조항"}</dd>
              </div>
              <div>
                <dt>상태</dt>
                <dd>{link.review_state}</dd>
              </div>
            </dl>
            <section
              className="rule-evidence-strip"
              aria-label="연결 근거 페이지"
            >
              {link.evidence.map((evidence) => (
                <span key={evidence.evidence_id}>
                  물리 {evidence.page_number}페이지 · 근거 확인됨
                </span>
              ))}
              <button
                type="button"
                className="secondary-button"
                onClick={() => setEvidenceOpen(true)}
              >
                근거 보기 Evidence
              </button>
            </section>
          </>
        ) : null}
        {error ? <p role="alert">{error}</p> : null}
        <footer className="dialog-actions">
          <button
            type="button"
            className="primary-button"
            disabled={working || !link || item.evidence.length === 0}
            onClick={confirm}
          >
            연결 확인
          </button>
          <button
            type="button"
            className="danger-button"
            disabled={working || !link}
            onClick={reject}
          >
            연결 제외
          </button>
        </footer>
      </div>
      <EvidenceDrawer
        evidence={drawerEvidence(item)}
        open={evidenceOpen}
        unavailable={item.evidence.length === 0}
        onClose={() => setEvidenceOpen(false)}
      />
    </div>
  );
}
