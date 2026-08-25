import { useEffect, useState } from "react";

import type { ClaimStatus } from "../../api/claims";

export type ClaimOutcomeMetadata = {
  amount: string;
  currency: string;
  payment_date: string;
  reason_code?: string;
};

const AMOUNT_PATTERN = /^(?:0|[1-9][0-9]{0,15})(?:\.[0-9]{1,2})?$/;
const CURRENCY_PATTERN = /^[A-Z]{3}$/;
const REASON_PATTERN = /^[A-Z][A-Z0-9_]{0,63}$/;

export function ClaimOutcomeForm({
  busy = false,
  onCancel,
  onSubmit,
  resetKey = 0,
}: {
  busy?: boolean;
  onCancel?: () => void;
  onSubmit: (
    target: Extract<ClaimStatus, "paid" | "partially_paid">,
    metadata: ClaimOutcomeMetadata,
  ) => void;
  resetKey?: number;
}) {
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("KRW");
  const [paymentDate, setPaymentDate] = useState("");
  const [reasonCode, setReasonCode] = useState("");
  const [error, setError] = useState<string>();

  // Parent increments this only after authentication expiry so sensitive
  // in-memory payment fields cannot remain on screen.
  useEffect(() => {
    setAmount("");
    setCurrency(resetKey === 0 ? "KRW" : "");
    setPaymentDate("");
    setReasonCode("");
    setError(undefined);
  }, [resetKey]);

  function submit(target: Extract<ClaimStatus, "paid" | "partially_paid">) {
    if (!AMOUNT_PATTERN.test(amount)) {
      setError("지급액은 0 이상 숫자로 입력해 주세요.");
      return;
    }
    if (!CURRENCY_PATTERN.test(currency)) {
      setError("통화는 영문 대문자 3자리로 입력해 주세요.");
      return;
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(paymentDate)) {
      setError("지급일을 입력해 주세요.");
      return;
    }
    if (reasonCode && !REASON_PATTERN.test(reasonCode)) {
      setError("사유는 등록된 코드 형식으로 입력해 주세요.");
      return;
    }
    setError(undefined);
    onSubmit(target, {
      amount,
      currency,
      payment_date: paymentDate,
      ...(reasonCode ? { reason_code: reasonCode } : {}),
    });
  }

  return (
    <section
      className="claim-outcome-form"
      aria-labelledby="claim-outcome-title"
    >
      <div className="claim-section-heading">
        <p className="claim-kicker">Payment record</p>
        <h2 id="claim-outcome-title">지급 결과 기록</h2>
      </div>
      <p className="claim-muted">
        FamilyCare는 보험사에 제출하지 않습니다. 보험사에서 받은 결과만 직접
        기록하세요.
      </p>
      {error ? (
        <p className="claim-error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="claim-form-grid">
        <label>
          지급액
          <input
            aria-label="지급액"
            disabled={busy}
            inputMode="decimal"
            min="0"
            onChange={(event) => setAmount(event.target.value)}
            step="0.01"
            type="number"
            value={amount}
          />
        </label>
        <label>
          통화
          <input
            aria-label="통화"
            autoCapitalize="characters"
            disabled={busy}
            maxLength={3}
            onChange={(event) => setCurrency(event.target.value.toUpperCase())}
            type="text"
            value={currency}
          />
        </label>
        <label>
          지급일
          <input
            aria-label="지급일"
            disabled={busy}
            onChange={(event) => setPaymentDate(event.target.value)}
            type="date"
            value={paymentDate}
          />
        </label>
        <label>
          사유 코드 (선택)
          <input
            aria-label="사유 코드"
            disabled={busy}
            maxLength={64}
            onChange={(event) =>
              setReasonCode(event.target.value.toUpperCase())
            }
            type="text"
            value={reasonCode}
          />
        </label>
      </div>
      <div className="claim-action-row">
        <button
          className="claim-primary-button"
          disabled={busy}
          onClick={() => submit("partially_paid")}
          type="button"
        >
          부분 지급 기록
        </button>
        <button
          className="claim-secondary-button"
          disabled={busy}
          onClick={() => submit("paid")}
          type="button"
        >
          전액 지급 기록
        </button>
        {onCancel ? (
          <button
            className="claim-quiet-button"
            disabled={busy}
            onClick={onCancel}
            type="button"
          >
            취소
          </button>
        ) : null}
      </div>
    </section>
  );
}
