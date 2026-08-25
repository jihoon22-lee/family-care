import type { ClaimStatus } from "../../api/claims";

const STATUS_LABELS: Record<ClaimStatus, string> = {
  preparing: "준비 중",
  submitted: "제출 기록",
  supplementation_requested: "보완 요청",
  paid: "지급 완료",
  partially_paid: "부분 지급",
  denied: "부지급 기록",
  closed: "종료",
};

const STATUS_ORDER: ClaimStatus[] = [
  "preparing",
  "submitted",
  "supplementation_requested",
  "partially_paid",
  "paid",
  "denied",
  "closed",
];

export function ClaimStatusStepper({
  allowedTransitions,
  busy = false,
  onTransition,
  status,
}: {
  allowedTransitions: ClaimStatus[];
  busy?: boolean;
  onTransition: (target: ClaimStatus) => void;
  status: ClaimStatus;
}) {
  return (
    <section
      className="claim-status-panel"
      aria-labelledby="claim-status-title"
    >
      <div className="claim-section-heading">
        <p className="claim-kicker">Claim state</p>
        <h2 id="claim-status-title">청구 진행 상태</h2>
      </div>
      <ol className="claim-status-stepper" aria-label="청구 상태">
        {STATUS_ORDER.map((item) => (
          <li className={item === status ? "is-current" : undefined} key={item}>
            <span aria-hidden="true" className="claim-status-node" />
            <span>{STATUS_LABELS[item]}</span>
            {item === status ? (
              <span className="claim-current">현재</span>
            ) : null}
          </li>
        ))}
      </ol>
      {allowedTransitions.length > 0 ? (
        <div className="claim-action-row" aria-label="가능한 다음 상태">
          {allowedTransitions.map((target) => (
            <button
              className={
                target === "paid" || target === "partially_paid"
                  ? "claim-primary-button"
                  : "claim-secondary-button"
              }
              disabled={busy}
              key={target}
              onClick={() => onTransition(target)}
              type="button"
            >
              {STATUS_LABELS[target]}
            </button>
          ))}
        </div>
      ) : (
        <p className="claim-muted">더 진행할 수 없는 종료 상태입니다.</p>
      )}
    </section>
  );
}

export { STATUS_LABELS };
