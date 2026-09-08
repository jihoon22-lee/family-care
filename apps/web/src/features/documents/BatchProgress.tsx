import type { BatchResponse } from "../../api/generated";

const DOCUMENT_KIND_LABELS: Record<
  BatchResponse["items"][number]["document_kind"],
  string
> = {
  application: "청약서",
  policy: "증권",
  product_explanation: "상품설명서",
  supporting: "보조자료",
  terms: "약관",
};

const ITEM_LABELS: Record<BatchResponse["items"][number]["state"], string> = {
  cancelled: "취소됨",
  password_required: "비밀번호 필요",
  permanently_failed: "처리 실패",
  queued: "대기 중",
  retryable_failed: "재시도 대기",
  running: "처리 중",
  succeeded: "완료",
};

const OCR_STATE_LABELS: Record<
  BatchResponse["items"][number]["ocr_state"],
  string
> = {
  completed: "OCR 완료",
  failed: "OCR 실패",
  native_only: "OCR 불필요 (원문 사용)",
  pending: "OCR 대기 중",
  running: "OCR 처리 중",
  warning: "OCR 확인 필요",
};

const STRUCTURE_LABELS: Record<
  NonNullable<BatchResponse["items"][number]["structure_state"]>,
  string
> = {
  PENDING: "문서 내용을 준비하고 있습니다.",
  PREPARED: "문서 내용 준비 완료",
  PARTIAL: "문서 일부를 준비하지 못했습니다.",
  FAILED: "가져온 문서의 내용 준비에 실패했습니다.",
  RETRYABLE_FAILED: "문서 내용 준비를 다시 시도할 예정입니다.",
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
              <small>
                문서 종류: {DOCUMENT_KIND_LABELS[item.document_kind]}
              </small>
              <small>시도 {item.attempts}회</small>
              <small>OCR 상태: {OCR_STATE_LABELS[item.ocr_state]}</small>
              <small>OCR 처리 페이지 {item.ocr_pages_processed}</small>
              {item.structure_state ? (
                <small>{STRUCTURE_LABELS[item.structure_state]}</small>
              ) : null}
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
