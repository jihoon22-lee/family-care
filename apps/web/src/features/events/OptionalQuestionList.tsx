import { useState } from "react";

import type { EventFactField, EventQuestionView } from "./EventComposer";
import styles from "./EventComposer.module.css";

const FIELD_LABELS: Record<EventFactField, string> = {
  event_date: "사건 날짜",
  visit_date: "방문 날짜",
  condition_class: "상황 분류",
  diagnosis_label: "진단 표기",
  treatment_kind: "치료 종류",
  admission: "입원 여부",
  outpatient: "외래 여부",
  pharmacy: "약국 이용 여부",
};

function questionKey(question: EventQuestionView): string {
  return `${question.question_code}:${question.field_id}`;
}

export function OptionalQuestionList({
  questions,
  onDismiss,
  emptyNotice = true,
}: {
  questions: EventQuestionView[];
  onDismiss?: (question: EventQuestionView) => void;
  emptyNotice?: boolean;
}) {
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());
  const visibleQuestions = questions.filter(
    (question) => !dismissed.has(questionKey(question)),
  );

  function dismiss(question: EventQuestionView) {
    const key = questionKey(question);
    setDismissed((current) => new Set(current).add(key));
    onDismiss?.(question);
  }

  if (visibleQuestions.length === 0) {
    return emptyNotice && questions.length === 0 ? (
      <p className={styles.optionalNotice}>추가 확인 질문은 선택 사항입니다.</p>
    ) : null;
  }

  return (
    <section
      className={styles.questionSection}
      aria-labelledby="optional-questions-title"
    >
      <div className={styles.questionIntro}>
        <h3 id="optional-questions-title">선택 질문</h3>
        <p>추가 확인 질문은 선택 사항입니다.</p>
      </div>
      <ul className={styles.questionList}>
        {visibleQuestions.map((question) => {
          const label = FIELD_LABELS[question.field_id];
          return (
            <li className={styles.questionItem} key={questionKey(question)}>
              <div>
                <strong>{label}를 확인해 주세요.</strong>
                <span>현재 결과 그룹에 영향을 줄 수 있습니다.</span>
              </div>
              <button
                className={styles.quietButton}
                type="button"
                onClick={() => dismiss(question)}
              >
                질문 닫기
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export { FIELD_LABELS };
