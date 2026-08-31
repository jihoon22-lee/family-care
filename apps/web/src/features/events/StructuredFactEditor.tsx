import { useState } from "react";

import type { EventFactField, EventFactView } from "./EventComposer";
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
  diagnosis_code: "진단 코드",
  procedure_code: "처치·수술 코드",
  anatomical_site_code: "신체 부위 코드",
  pathology_code: "병리 코드",
  treatment_setting: "치료 환경",
  treatment_context: "치료 맥락",
  separately_billed_treatment: "별도 결제 치료 여부",
};

const BOOLEAN_FIELDS = new Set<EventFactField>([
  "admission",
  "outpatient",
  "pharmacy",
  "separately_billed_treatment",
]);

const SOURCE_LABELS: Record<EventFactView["source"], string> = {
  ai: "AI 제안",
  user: "사용자 입력",
  system: "시스템 값",
};

const STATE_LABELS: Record<EventFactView["state"], string> = {
  confirmed: "확인됨",
  ambiguous: "추가 확인 필요",
  missing: "입력 필요",
  conflict: "충돌 확인 필요",
};

function displayValue(value: EventFactView["value"]): string {
  if (value === null) return "";
  return typeof value === "boolean" ? String(value) : value;
}

function updateFact(fact: EventFactView, rawValue: string): EventFactView {
  const value = BOOLEAN_FIELDS.has(fact.field_id)
    ? rawValue === ""
      ? null
      : rawValue === "true"
    : rawValue;
  return {
    ...fact,
    value,
    source: "user",
    state: value === null || value === "" ? "missing" : "confirmed",
  };
}

export function StructuredFactEditor({
  facts,
  onChange,
}: {
  facts: EventFactView[];
  onChange: (facts: EventFactView[]) => void;
}) {
  const [selectedField, setSelectedField] = useState<EventFactField | "">("");
  const availableFields = (
    Object.keys(FIELD_LABELS) as EventFactField[]
  ).filter((fieldId) => !facts.some((fact) => fact.field_id === fieldId));

  function addFact() {
    if (!selectedField || !availableFields.includes(selectedField)) return;
    onChange([
      ...facts,
      {
        confidence: "high",
        evidence_ids: [],
        fact_id: `manual-${selectedField}`,
        field_id: selectedField,
        source: "user",
        state: "missing",
        value: null,
      },
    ]);
    setSelectedField("");
  }

  return (
    <section
      className={styles.factSection}
      aria-labelledby="structured-facts-title"
    >
      <div className={styles.sectionHeading}>
        <p className={styles.kicker}>Editable candidates</p>
        <h3 id="structured-facts-title">구조화된 후보</h3>
      </div>
      <div className={styles.factAdd}>
        <label className={styles.field}>
          <span>직접 추가할 정보</span>
          <select
            aria-label="직접 추가할 정보"
            value={selectedField}
            onChange={(event) =>
              setSelectedField(event.target.value as EventFactField | "")
            }
          >
            <option value="">항목 선택</option>
            {availableFields.map((fieldId) => (
              <option key={fieldId} value={fieldId}>
                {FIELD_LABELS[fieldId]}
              </option>
            ))}
          </select>
        </label>
        <button
          className={styles.secondaryButton}
          disabled={!selectedField}
          type="button"
          onClick={addFact}
        >
          정보 추가
        </button>
      </div>
      {facts.length === 0 ? (
        <p className={styles.emptyFacts}>
          자동 구조화 결과가 없습니다. 필요한 값은 위에서 직접 추가할 수
          있습니다.
        </p>
      ) : (
        <div className={styles.factList}>
          {facts.map((fact, index) => {
            const label = FIELD_LABELS[fact.field_id];
            const stateText = STATE_LABELS[fact.state];
            const sourceText = SOURCE_LABELS[fact.source];
            const inputId = `event-fact-${fact.fact_id || index}`;
            return (
              <article
                className={styles.factCard}
                key={fact.fact_id || `${fact.field_id}-${index}`}
              >
                <div className={styles.factMeta}>
                  <span>
                    {sourceText} · {stateText}
                  </span>
                  <span>신뢰도 {fact.confidence}</span>
                </div>
                <label className={styles.field} htmlFor={inputId}>
                  <span>{label}</span>
                  {BOOLEAN_FIELDS.has(fact.field_id) ? (
                    <select
                      aria-label={label}
                      id={inputId}
                      value={
                        fact.value === null
                          ? ""
                          : fact.value === true
                            ? "true"
                            : "false"
                      }
                      onChange={(event) =>
                        onChange(
                          facts.map((candidate, candidateIndex) =>
                            candidateIndex === index
                              ? updateFact(candidate, event.target.value)
                              : candidate,
                          ),
                        )
                      }
                    >
                      <option value="">선택 필요</option>
                      <option value="true">예</option>
                      <option value="false">아니오</option>
                    </select>
                  ) : (
                    <input
                      aria-label={label}
                      id={inputId}
                      type="text"
                      value={displayValue(fact.value)}
                      onChange={(event) =>
                        onChange(
                          facts.map((candidate, candidateIndex) =>
                            candidateIndex === index
                              ? updateFact(candidate, event.target.value)
                              : candidate,
                          ),
                        )
                      }
                    />
                  )}
                </label>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

export { FIELD_LABELS, SOURCE_LABELS, STATE_LABELS };
