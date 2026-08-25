import styles from "./EventComposer.module.css";

export function NaturalLanguageSituation({
  value,
  onChange,
  error,
  disabled = false,
}: {
  value: string;
  onChange: (value: string) => void;
  error?: string;
  disabled?: boolean;
}) {
  const errorId = "event-situation-error";
  return (
    <label className={styles.field}>
      <span>현재 상황</span>
      <textarea
        aria-describedby={error ? errorId : undefined}
        aria-invalid={error ? true : undefined}
        aria-label="현재 상황"
        autoComplete="off"
        disabled={disabled}
        maxLength={2000}
        placeholder="예: 방문 전 확인하고 싶은 상황을 짧게 적어 주세요."
        rows={5}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      <span className={styles.fieldHint}>
        최대 2,000자 · 저장하기 전에는 이 화면의 메모리에서만 유지됩니다.
      </span>
      {error ? (
        <span className={styles.fieldError} id={errorId} role="alert">
          {error}
        </span>
      ) : null}
    </label>
  );
}
