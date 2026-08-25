import { useState } from "react";
import type { FormEvent } from "react";

import type {
  MedicalEventResponse,
  OptionalQuestionResponse,
  StructuredFactResponse,
} from "../../api/generated";
import { NaturalLanguageSituation } from "./NaturalLanguageSituation";
import styles from "./EventComposer.module.css";
import { OptionalQuestionList } from "./OptionalQuestionList";
import { ReceiptLineEditor } from "./ReceiptLineEditor";
import { StructuredFactEditor } from "./StructuredFactEditor";

export type EventMode = MedicalEventResponse["mode"];
export type EventFactField = StructuredFactResponse["field_id"];
export type EventFactState = StructuredFactResponse["state"];
export type EventFactSource = StructuredFactResponse["source"];
export type EventFactConfidence = StructuredFactResponse["confidence"];
export type EventFactValue = StructuredFactResponse["value"];

export interface EventFactView {
  fact_id: string;
  field_id: EventFactField;
  value: EventFactValue;
  source: EventFactSource;
  state: EventFactState;
  confidence: EventFactConfidence;
  evidence_ids: string[];
}

export interface EventQuestionView {
  question_code: EventFactField;
  field_id: EventFactField;
}

export interface ReceiptLineView {
  id?: string;
  version?: number;
  category: "outpatient" | "inpatient" | "pharmacy";
  coverage_category: "covered" | "possible_excluded" | "excluded" | "unknown";
  amount: string;
  currency: string;
  confirmation_level: "user" | "ai_structured" | "unconfirmed";
  note_code?: string | null;
}

export interface EventDraftView {
  family_member_id: string;
  mode: EventMode;
  situation: string;
  event_date: string | null;
  visit_date: string | null;
  facts: EventFactView[];
  receipt_lines: ReceiptLineView[];
}

export interface EventComposerProps {
  memberId: string;
  memberLabel?: string;
  initialEvent?: MedicalEventResponse;
  initialReceiptLines?: ReceiptLineView[];
  mode?: EventMode;
  onSubmit?: (draft: EventDraftView) => void | Promise<void>;
  onStructure?: (draft: EventDraftView) => void | Promise<void>;
  onAnalyze?: (draft: EventDraftView) => void | Promise<void>;
}

function initialFacts(event?: MedicalEventResponse): EventFactView[] {
  return (event?.structured_facts ?? []).map(
    (fact: StructuredFactResponse) => ({
      fact_id: fact.fact_id,
      field_id: fact.field_id,
      value: fact.value,
      source: fact.source,
      state: fact.state,
      confidence: fact.confidence,
      evidence_ids: [...fact.evidence_ids],
    }),
  );
}

function isEventFactField(value: string): value is EventFactField {
  return [
    "event_date",
    "visit_date",
    "condition_class",
    "diagnosis_label",
    "treatment_kind",
    "admission",
    "outpatient",
    "pharmacy",
  ].includes(value);
}

function initialQuestions(event?: MedicalEventResponse): EventQuestionView[] {
  return (event?.optional_questions ?? []).flatMap(
    (question: OptionalQuestionResponse) => {
      if (
        !isEventFactField(question.field_id) ||
        !isEventFactField(question.question_code) ||
        question.field_id !== question.question_code
      ) {
        return [];
      }
      return [
        {
          question_code: question.question_code,
          field_id: question.field_id,
        },
      ];
    },
  );
}

export function EventComposer({
  memberId,
  memberLabel,
  initialEvent,
  initialReceiptLines = [],
  mode: requestedMode,
  onSubmit,
  onStructure,
  onAnalyze,
}: EventComposerProps) {
  const [mode, setMode] = useState<EventMode>(
    initialEvent?.mode ?? requestedMode ?? "pre_visit",
  );
  const [situation, setSituation] = useState(initialEvent?.situation ?? "");
  const [eventDate, setEventDate] = useState(initialEvent?.event_date ?? "");
  const [visitDate, setVisitDate] = useState(initialEvent?.visit_date ?? "");
  const [facts, setFacts] = useState<EventFactView[]>(
    initialFacts(initialEvent),
  );
  const [questions, setQuestions] = useState<EventQuestionView[]>(
    initialQuestions(initialEvent),
  );
  const [receiptLines, setReceiptLines] =
    useState<ReceiptLineView[]>(initialReceiptLines);
  const [submitted, setSubmitted] = useState(Boolean(initialEvent));
  const [working, setWorking] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");

  function draft(): EventDraftView {
    return {
      family_member_id: memberId,
      mode,
      situation: situation.trim(),
      event_date: eventDate || null,
      visit_date: visitDate || null,
      facts: facts.map((fact) => ({
        ...fact,
        evidence_ids: [...fact.evidence_ids],
      })),
      receipt_lines: receiptLines.map((line) => ({ ...line })),
    };
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!situation.trim()) {
      setError("현재 상황을 입력해 주세요.");
      return;
    }
    setWorking(true);
    setError("");
    try {
      const nextDraft = draft();
      await onSubmit?.(nextDraft);
      setSubmitted(true);
      setStatus("현재 입력을 저장했습니다. 추가 확인 질문은 선택 사항입니다.");
    } catch {
      setError("현재 상황을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    } finally {
      setWorking(false);
    }
  }

  async function structure() {
    setWorking(true);
    setError("");
    try {
      await onStructure?.(draft());
      setStatus(
        "자동 구조화 요청을 보냈습니다. 직접 입력한 내용은 유지됩니다.",
      );
    } catch {
      setError(
        "자동 구조화를 완료하지 못했습니다. 직접 입력한 내용으로 계속할 수 있습니다.",
      );
    } finally {
      setWorking(false);
    }
  }

  async function analyze() {
    setWorking(true);
    setError("");
    try {
      await onAnalyze?.(draft());
      setStatus("현재 입력으로 결과 확인을 요청했습니다.");
    } catch {
      setError(
        "결과를 확인하지 못했습니다. 입력을 확인한 뒤 다시 시도해 주세요.",
      );
    } finally {
      setWorking(false);
    }
  }

  return (
    <main className={styles.composer} id="main-content">
      <header className={styles.heading}>
        <p className={styles.kicker}>Medical event</p>
        <h1>새 사건 기록</h1>
        <p className={styles.intro}>
          짧은 상황부터 기록하고, 확인 가능한 정보만 다음 단계에 사용합니다.
        </p>
      </header>

      <section className={styles.context} aria-label="사건 대상">
        <span>현재 대상</span>
        <strong>{memberLabel ?? "선택한 가족 구성원"}</strong>
      </section>

      <form onSubmit={submit} noValidate>
        <fieldset className={styles.fieldset}>
          <legend>사건 기본 정보</legend>
          <label className={styles.field}>
            <span>사건 유형</span>
            <select
              value={mode}
              onChange={(event) => setMode(event.target.value as EventMode)}
            >
              <option value="pre_visit">방문 전</option>
              <option value="post_treatment">치료 후</option>
            </select>
          </label>
          <div className={styles.dateGrid}>
            <label className={styles.field}>
              <span>사건 날짜 (선택)</span>
              <input
                type="date"
                value={eventDate}
                onChange={(event) => setEventDate(event.target.value)}
              />
            </label>
            <label className={styles.field}>
              <span>방문 날짜 (선택)</span>
              <input
                type="date"
                value={visitDate}
                onChange={(event) => setVisitDate(event.target.value)}
              />
            </label>
          </div>
          <NaturalLanguageSituation
            value={situation}
            onChange={setSituation}
            error={error === "현재 상황을 입력해 주세요." ? error : undefined}
          />
        </fieldset>

        {mode === "post_treatment" ? (
          <ReceiptLineEditor
            lines={receiptLines}
            currency="KRW"
            onChange={setReceiptLines}
          />
        ) : null}

        {error && error !== "현재 상황을 입력해 주세요." ? (
          <p className={styles.alert} role="alert">
            {error}
          </p>
        ) : null}
        <button
          className={styles.primaryButton}
          type="submit"
          disabled={working}
        >
          현재 후보 보기
        </button>
      </form>

      {submitted ? (
        <section className={styles.followup} aria-labelledby="candidate-title">
          <div aria-live="polite" className={styles.status}>
            {status || "현재 입력을 기준으로 후보를 확인할 수 있습니다."}
          </div>
          <div className={styles.sectionHeading}>
            <p className={styles.kicker}>Editable facts</p>
            <h2 id="candidate-title">현재 후보</h2>
          </div>
          <p className={styles.optionalNotice}>
            추가 확인 질문은 선택 사항입니다.
          </p>
          <OptionalQuestionList
            questions={questions}
            emptyNotice={false}
            onDismiss={(question) =>
              setQuestions((current) =>
                current.filter(
                  (item) => item.question_code !== question.question_code,
                ),
              )
            }
          />
          <StructuredFactEditor facts={facts} onChange={setFacts} />
          <div className={styles.actions}>
            <button
              className={styles.secondaryButton}
              type="button"
              disabled={working}
              onClick={structure}
            >
              선택적으로 자동 구조화
            </button>
            <button
              className={styles.primaryButton}
              type="button"
              disabled={working}
              onClick={analyze}
            >
              결과 확인
            </button>
          </div>
        </section>
      ) : null}
    </main>
  );
}
