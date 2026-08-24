import { useCallback, useEffect, useRef, useState } from "react";

import type {
  CoverageRuleVersionsResponse,
  EvidenceRef,
  PolicyReviewItem,
} from "../../api/generated";
import { listCoverageRuleVersions, publishCoverageRule } from "../../api/rules";
import { EvidenceDrawer } from "../../components/EvidenceDrawer";
import { RuleExpressionEditor } from "./RuleExpressionEditor";

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

export function CoverageRuleReviewDialog({
  item: initialItem,
  onClose,
  onMutated,
}: {
  item: PolicyReviewItem;
  onClose: () => void;
  onMutated: () => void;
}) {
  const [item, setItem] = useState(initialItem);
  const [snapshot, setSnapshot] = useState<CoverageRuleVersionsResponse>();
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string>();
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const close = useCallback(onClose, [onClose]);
  const ruleId = item.aggregate_id;

  useEffect(() => {
    dialogRef.current?.focus();
    const controller = new AbortController();
    if (!ruleId) {
      setLoading(false);
      setError("규칙 후보의 식별 정보를 확인할 수 없습니다.");
      return () => controller.abort();
    }
    void listCoverageRuleVersions(ruleId, controller.signal)
      .then(setSnapshot)
      .catch(() => setError("보장 규칙 버전을 불러오지 못했습니다."))
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [ruleId]);

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

  const latest = snapshot?.versions.at(-1);
  const unsupported = item.issues.some(
    (issue) => issue.code === "UNSUPPORTED_DSL",
  );
  const canPublish = Boolean(
    latest &&
    !latest.executable &&
    (latest.review_state === "AI_VERIFIED" ||
      latest.review_state === "USER_CONFIRMED") &&
    !unsupported &&
    latest.evidence.length > 0,
  );

  async function publish() {
    if (!ruleId || !snapshot || !latest || !canPublish) return;
    setWorking(true);
    setError(undefined);
    try {
      await publishCoverageRule(ruleId, {
        expected_version: snapshot.expected_version,
        version_id: latest.version_id,
      });
      onMutated();
      close();
    } catch {
      setError("새 규칙 버전을 불러온 뒤 다시 게시해 주세요.");
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
        aria-labelledby="coverage-rule-dialog-title"
        tabIndex={-1}
      >
        <header className="dialog-heading">
          <div>
            <span>Deterministic rule</span>
            <h2 id="coverage-rule-dialog-title">보장 규칙 검토</h2>
          </div>
          <button type="button" className="quiet-button" onClick={close}>
            닫기
          </button>
        </header>
        {loading ? (
          <p role="status">저장된 규칙 버전을 확인하는 중입니다.</p>
        ) : null}
        {latest ? (
          <section className="rule-version-card" aria-label="저장된 규칙 버전">
            <header>
              <strong>{latest.rule_kind}</strong>
              <span>버전 {latest.version_number}</span>
            </header>
            <dl>
              <div>
                <dt>판정 상태</dt>
                <dd>{latest.review_state}</dd>
              </div>
              <div>
                <dt>필요 정보</dt>
                <dd>{latest.input_field_paths.join(" · ")}</dd>
              </div>
              <div>
                <dt>근거 코드</dt>
                <dd>{latest.result_reason_code}</dd>
              </div>
            </dl>
            <button
              type="button"
              className="secondary-button"
              onClick={() => setEvidenceOpen(true)}
            >
              근거 보기 Evidence
            </button>
          </section>
        ) : null}
        {unsupported ? (
          <p className="rule-informational" role="status">
            지원하지 않는 규칙 구조입니다. 판정에는 아직 사용하지 않습니다.
          </p>
        ) : null}
        <RuleExpressionEditor item={item} onSaved={setItem} />
        {error ? <p role="alert">{error}</p> : null}
        <footer className="dialog-actions">
          <button
            type="button"
            className="primary-button"
            disabled={working || !canPublish}
            onClick={publish}
          >
            규칙 게시
          </button>
          <span>검증된 저장 버전만 판정에 사용됩니다.</span>
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
