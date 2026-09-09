import { useEffect, useId, useRef, useState } from "react";
import type {
  FactInput,
  GuidanceQuestion,
  MedicalEventResponse,
  MedicalEventUpdateRequest,
  StructuredFactInput,
} from "../../api/generated";
import { ApiError } from "../../api/errors";
import { authStore } from "../identity/authStore";
import { guidanceInputLabel } from "./LocalGuidancePanel";
import styles from "./GuidanceQuestions.module.css";

const structuredFields: StructuredFactInput["field_id"][] = [
  "condition_class",
  "diagnosis_label",
  "treatment_kind",
  "admission",
  "outpatient",
  "pharmacy",
  "diagnosis_code",
  "procedure_code",
  "anatomical_site_code",
  "pathology_code",
  "treatment_setting",
  "treatment_context",
  "separately_billed_treatment",
];
const booleans = new Set([
  "admission",
  "outpatient",
  "pharmacy",
  "separately_billed_treatment",
]);
function field(path: string) {
  return path.startsWith("MedicalEvent.")
    ? path.slice("MedicalEvent.".length)
    : "";
}
function supported(path: string) {
  const name = field(path);
  return [
    "admission_days",
    "event_date",
    "visit_date",
    ...structuredFields,
  ].includes(name);
}
function existingFacts(event: MedicalEventResponse): Record<string, FactInput> {
  const facts: Record<string, FactInput> = {};
  for (const [path, raw] of Object.entries(event.facts)) {
    if (
      !["MedicalEvent.classification", "MedicalEvent.admission_days"].includes(
        path,
      ) ||
      !raw ||
      typeof raw !== "object"
    )
      continue;
    const value = raw as Partial<FactInput>;
    if (
      (value.value === null ||
        typeof value.value === "string" ||
        typeof value.value === "number") &&
      ["user", "ai_structured", "unconfirmed", "conflicting"].includes(
        value.confirmation ?? "",
      )
    ) {
      facts[path] = { value: value.value, confirmation: value.confirmation! };
    }
  }
  return facts;
}

export function GuidanceQuestions({
  event,
  questions,
  disabled = false,
  onSave,
  onAnalyze,
}: {
  event: MedicalEventResponse;
  questions: GuidanceQuestion[];
  disabled?: boolean;
  onSave: (
    input: MedicalEventUpdateRequest,
    signal: AbortSignal,
  ) => Promise<MedicalEventResponse>;
  onAnalyze: (signal: AbortSignal) => Promise<void>;
}) {
  const id = useId();
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [expired, setExpired] = useState(false);
  const request = useRef<AbortController | null>(null);
  const submitted = useRef(false);
  const saved = useRef<{ values: string; version: number } | undefined>(
    undefined,
  );
  const paths = [
    ...new Set(questions.map((question) => question.field_path)),
  ].filter(supported);

  useEffect(() => {
    setValues({});
    setError("");
    setNotice("");
    setBusy(false);
    submitted.current = false;
    saved.current = undefined;
    return () => request.current?.abort();
  }, [event.id]);
  useEffect(
    () =>
      authStore.registerCacheClearer(() => {
        request.current?.abort();
        setValues({});
        setError("");
        setNotice("");
        saved.current = undefined;
        setExpired(true);
      }),
    [],
  );

  async function submit() {
    if (disabled || expired || submitted.current) return;
    const supplied = paths.filter((path) => values[path]?.trim());
    setError("");
    setNotice("");
    if (!supplied.length) {
      setNotice("입력하지 않은 질문은 그대로 두고 기존 결과를 볼 수 있습니다.");
      return;
    }
    const input: MedicalEventUpdateRequest = {
      expected_version: Math.max(
        event.version,
        saved.current?.version ?? event.version,
      ),
    };
    for (const path of supplied) {
      const name = field(path),
        value = values[path].trim();
      if (name === "admission_days") {
        if (!/^\d+$/.test(value) || Number(value) > 36500) {
          setError("입원 일수는 0부터 36,500 사이의 정수로 입력해 주세요.");
          return;
        }
        input.facts = {
          ...existingFacts(event),
          [path]: { value: Number(value), confirmation: "user" },
        };
      } else if (name === "event_date" || name === "visit_date") {
        input[name] = value;
      } else {
        const fieldId = structuredFields.find(
          (candidate) => candidate === name,
        );
        if (fieldId)
          (input.structured_facts ??= []).push({
            field_id: fieldId,
            value: booleans.has(name) ? value === "true" : value,
          });
      }
    }
    const fingerprint = JSON.stringify(
      supplied.map((path) => [path, values[path].trim()]),
    );
    const controller = new AbortController();
    request.current = controller;
    submitted.current = true;
    setBusy(true);
    try {
      if (
        saved.current?.values !== fingerprint ||
        event.version > saved.current.version
      ) {
        const updated = await onSave(input, controller.signal);
        if (controller.signal.aborted) return;
        saved.current = { values: fingerprint, version: updated.version };
      }
      await onAnalyze(controller.signal);
      if (!controller.signal.aborted)
        setNotice("보완한 입력으로 다시 계산했습니다.");
    } catch (cause) {
      if (!controller.signal.aborted)
        setError(
          cause instanceof ApiError && cause.status === 409
            ? "사건이 변경되었습니다. 현재 사건을 다시 불러온 뒤 입력을 보완해 주세요."
            : saved.current?.values === fingerprint &&
                event.version <= saved.current.version
              ? "입력은 저장했습니다. 연결을 확인한 뒤 다시 계산해 주세요."
              : "입력을 저장하지 못했습니다. 기존 결과를 유지하며 다시 시도할 수 있습니다.",
        );
    } finally {
      if (!controller.signal.aborted) {
        submitted.current = false;
        setBusy(false);
      }
    }
  }
  if (!paths.length || expired) return null;
  return (
    <section className={styles.panel} aria-labelledby={`${id}-heading`}>
      <h2 id={`${id}-heading`}>필요한 사건 정보만 보완</h2>
      <p>
        답한 항목만 저장하고 다시 계산합니다. 질문에 답하지 않아도 기존 후보와
        금액은 계속 볼 수 있습니다.
      </p>
      <form
        noValidate
        onSubmit={(submitEvent) => {
          submitEvent.preventDefault();
          void submit();
        }}
      >
        <div className={styles.fields}>
          {paths.map((path) => {
            const name = field(path);
            return (
              <label key={path}>
                <span>{guidanceInputLabel(path)}</span>
                {booleans.has(name) ? (
                  <select
                    value={values[path] ?? ""}
                    disabled={disabled || busy}
                    onChange={(change) =>
                      setValues((current) => ({
                        ...current,
                        [path]: change.target.value,
                      }))
                    }
                  >
                    <option value="">아직 답하지 않음</option>
                    <option value="true">예</option>
                    <option value="false">아니요</option>
                  </select>
                ) : (
                  <input
                    type={
                      name === "admission_days"
                        ? "number"
                        : name.endsWith("date")
                          ? "date"
                          : "text"
                    }
                    min={name === "admission_days" ? 0 : undefined}
                    max={name === "admission_days" ? 36500 : undefined}
                    step={name === "admission_days" ? 1 : undefined}
                    maxLength={160}
                    autoComplete="off"
                    value={values[path] ?? ""}
                    disabled={disabled || busy}
                    onChange={(change) =>
                      setValues((current) => ({
                        ...current,
                        [path]: change.target.value,
                      }))
                    }
                  />
                )}
              </label>
            );
          })}
        </div>
        <button type="submit" disabled={disabled || busy}>
          입력 보완 후 다시 계산
        </button>
        {busy ? (
          <p role="status">
            입력을 저장하고 다시 계산하는 중입니다. 기존 결과는 유지됩니다.
          </p>
        ) : null}
        {error ? <p role="alert">{error}</p> : null}
        {notice ? <p role="status">{notice}</p> : null}
      </form>
    </section>
  );
}
