import { useRef, useState } from "react";

import type { ReceiptLineView } from "./EventComposer";
import styles from "./EventComposer.module.css";

const CATEGORY_LABELS: Record<ReceiptLineView["category"], string> = {
  outpatient: "외래",
  inpatient: "입원",
  pharmacy: "약국",
};

const COVERAGE_LABELS: Record<ReceiptLineView["coverage_category"], string> = {
  covered: "보장 가능성 있음",
  possible_excluded: "제외 가능성 있음",
  excluded: "제외 확인",
  unknown: "확인 필요",
};

const CONFIRMATION_LABELS: Record<
  ReceiptLineView["confirmation_level"],
  string
> = {
  user: "사용자 입력",
  ai_structured: "AI 구조화",
  unconfirmed: "미확인",
};

const DECIMAL_PATTERN = /^(?:0|[1-9]\d{0,11})(?:\.\d+)?$/;
const MAX_DECIMAL_PLACES = 2;

type ReceiptDraft = Omit<ReceiptLineView, "id"> & { id?: string };

function emptyDraft(currency: string): ReceiptDraft {
  return {
    category: "outpatient",
    coverage_category: "unknown",
    amount: "",
    currency,
    confirmation_level: "user",
  };
}

function validateAmount(value: string): string | undefined {
  if (!value.trim()) return "금액을 입력해 주세요.";
  if (value.includes("e") || value.includes("E")) {
    return "금액은 지수 표기 없이 소수로 입력해 주세요.";
  }
  if (value.startsWith("-")) return "금액은 0 이상이어야 합니다.";
  if (!DECIMAL_PATTERN.test(value))
    return "금액은 0 이상인 소수로 입력해 주세요.";
  const decimalPlaces = value.includes(".") ? value.split(".")[1].length : 0;
  if (decimalPlaces > MAX_DECIMAL_PLACES) {
    return "금액은 소수 둘째 자리까지 입력해 주세요.";
  }
  return undefined;
}

function lineLabel(line: ReceiptLineView): string {
  return `${CATEGORY_LABELS[line.category]} ${line.amount} ${line.currency}`;
}

export function ReceiptLineEditor({
  lines,
  currency,
  onChange,
}: {
  lines: ReceiptLineView[];
  currency: string;
  onChange: (lines: ReceiptLineView[]) => void;
}) {
  const [draft, setDraft] = useState<ReceiptDraft | null>(null);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [error, setError] = useState("");
  const nextDraftId = useRef(1);

  function startAdd() {
    setEditingIndex(null);
    setDraft(emptyDraft(currency));
    setError("");
  }

  function startEdit(index: number) {
    const line = lines[index];
    if (!line) return;
    setEditingIndex(index);
    setDraft({ ...line });
    setError("");
  }

  function cancelEdit() {
    setDraft(null);
    setEditingIndex(null);
    setError("");
  }

  function saveDraft() {
    if (!draft) return;
    const amountError = validateAmount(draft.amount);
    if (amountError) {
      setError(amountError);
      return;
    }
    const normalizedCurrency = draft.currency.trim().toUpperCase();
    if (normalizedCurrency !== currency.trim().toUpperCase()) {
      setError("통화가 일치해야 합니다.");
      return;
    }
    if (!/^[A-Z]{3}$/.test(normalizedCurrency)) {
      setError("통화는 영문 대문자 세 글자로 입력해 주세요.");
      return;
    }
    const saved: ReceiptLineView = {
      ...draft,
      id: draft.id ?? `synthetic-receipt-line-${nextDraftId.current++}`,
      amount: draft.amount,
      currency: normalizedCurrency,
    };
    const nextLines = [...lines];
    if (editingIndex === null) nextLines.push(saved);
    else nextLines[editingIndex] = saved;
    onChange(nextLines);
    cancelEdit();
  }

  function removeLine(index: number) {
    onChange(lines.filter((_, lineIndex) => lineIndex !== index));
    if (editingIndex === index) cancelEdit();
  }

  return (
    <section
      className={styles.receiptSection}
      aria-labelledby="receipt-lines-title"
    >
      <div className={styles.sectionHeading}>
        <p className={styles.kicker}>Manual receipt lines</p>
        <h3 id="receipt-lines-title">영수증 항목</h3>
      </div>
      <p className={styles.fieldHint}>
        영수증 사진이나 PDF는 올리지 않습니다. 항목과 통화를 직접 입력해 주세요.
      </p>
      {lines.length > 0 ? (
        <ul className={styles.receiptList}>
          {lines.map((line, index) => (
            <li
              className={styles.receiptItem}
              key={line.id ?? `receipt-${index}`}
              aria-label={lineLabel(line)}
            >
              <div>
                <strong>{lineLabel(line)}</strong>
                <span>
                  {COVERAGE_LABELS[line.coverage_category]} ·{" "}
                  {CONFIRMATION_LABELS[line.confirmation_level]}
                </span>
              </div>
              <div className={styles.itemActions}>
                <button
                  className={styles.quietButton}
                  type="button"
                  onClick={() => startEdit(index)}
                >
                  수정
                </button>
                <button
                  className={styles.quietButton}
                  type="button"
                  onClick={() => removeLine(index)}
                >
                  삭제
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className={styles.emptyReceipt}>입력한 영수증 항목이 없습니다.</p>
      )}
      <button
        className={styles.secondaryButton}
        type="button"
        onClick={startAdd}
      >
        영수증 항목 추가
      </button>

      {draft ? (
        <div className={styles.receiptEditor} aria-label="영수증 항목 편집">
          <label className={styles.field}>
            <span>항목 분류</span>
            <select
              aria-label="항목 분류"
              value={draft.category}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  category: event.target.value as ReceiptLineView["category"],
                })
              }
            >
              <option value="outpatient">외래</option>
              <option value="inpatient">입원</option>
              <option value="pharmacy">약국</option>
            </select>
          </label>
          <label className={styles.field}>
            <span>금액</span>
            <input
              aria-label="금액"
              inputMode="decimal"
              role="spinbutton"
              type="text"
              value={draft.amount}
              onChange={(event) =>
                setDraft({ ...draft, amount: event.target.value })
              }
            />
          </label>
          <label className={styles.field}>
            <span>통화</span>
            <input
              aria-label="통화"
              autoCapitalize="characters"
              maxLength={3}
              type="text"
              value={draft.currency}
              onChange={(event) =>
                setDraft({ ...draft, currency: event.target.value })
              }
            />
          </label>
          <label className={styles.field}>
            <span>보장 분류</span>
            <select
              aria-label="보장 분류"
              value={draft.coverage_category}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  coverage_category: event.target
                    .value as ReceiptLineView["coverage_category"],
                })
              }
            >
              <option value="covered">보장 가능성 있음</option>
              <option value="possible_excluded">제외 가능성 있음</option>
              <option value="excluded">제외 확인</option>
              <option value="unknown">확인 필요</option>
            </select>
          </label>
          <label className={styles.field}>
            <span>확인 상태</span>
            <select
              aria-label="확인 상태"
              value={draft.confirmation_level}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  confirmation_level: event.target
                    .value as ReceiptLineView["confirmation_level"],
                })
              }
            >
              <option value="user">사용자 입력</option>
              <option value="unconfirmed">미확인</option>
            </select>
          </label>
          {error ? (
            <p className={styles.alert} role="alert">
              {error}
            </p>
          ) : null}
          <div className={styles.actions}>
            <button
              className={styles.primaryButton}
              type="button"
              onClick={saveDraft}
            >
              항목 저장
            </button>
            <button
              className={styles.quietButton}
              type="button"
              onClick={cancelEdit}
            >
              취소
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

export {
  CATEGORY_LABELS,
  CONFIRMATION_LABELS,
  COVERAGE_LABELS,
  validateAmount,
};
