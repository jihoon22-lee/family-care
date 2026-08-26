import type { BatchResponse } from "../../api/generated";

const ITEM_LABELS: Record<BatchResponse["items"][number]["state"], string> = {
  cancelled: "취소됨",
  password_required: "비밀번호 필요",
  permanently_failed: "처리 실패",
  queued: "대기 중",
  retryable_failed: "재시도 대기",
  running: "처리 중",
  succeeded: "완료",
};

export function BatchProgress({
  batch,
  busy = false,
  onCancel,
}: {
  batch: BatchResponse;
  busy?: boolean;
  onCancel: () => void;
}) {
  const cancellable = ["created", "running", "partial"].includes(batch.state);
  return (
    <section
      aria-labelledby="import-progress-title"
      aria-live="polite"
      className="import-progress"
    >
      <div className="import-section-heading">
        <div>
          <p className="import-eyebrow">Batch progress</p>
          <h2 id="import-progress-title">문서 처리 현황</h2>
        </div>
        {cancellable ? (
          <button
            className="import-quiet-button"
            disabled={busy}
            onClick={onCancel}
            type="button"
          >
            가져오기 취소
          </button>
        ) : null}
      </div>
      <ul className="import-progress-list">
        {batch.items.map((item) => (
          <li key={item.source_id}>
            <span>
              <strong>{item.display_label}</strong>
              <small>시도 {item.attempts}회</small>
            </span>
            <span className={`import-state import-state-${item.state}`}>
              {ITEM_LABELS[item.state]}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
