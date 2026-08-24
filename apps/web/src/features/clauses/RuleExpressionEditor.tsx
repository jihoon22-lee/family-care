import { useEffect, useMemo, useState } from "react";

import { ApiError } from "../../api/errors";
import type {
  CandidateField,
  CandidateScalar,
  PolicyCandidateFieldId,
  PolicyReviewItem,
} from "../../api/generated";
import { correctRuleReviewField } from "../../api/rules";

const EDITABLE_FIELDS = new Set<PolicyCandidateFieldId>([
  "rule_kind",
  "rule_operator",
  "fact_field",
  "unit",
  "decimal_boundary",
  "date_boundary",
  "required",
]);

const OPTIONS: Partial<Record<PolicyCandidateFieldId, readonly string[]>> = {
  fact_field: [
    "MedicalEvent.event_date",
    "MedicalEvent.classification",
    "MedicalEvent.admission_days",
    "PolicyContract.contract_start",
    "PolicyContract.contract_end",
    "Rider.status",
    "Rider.insured_amount",
    "ClaimHistory.counted_occurrence",
  ],
  rule_kind: [
    "eligibility",
    "classification",
    "temporal",
    "exclusion",
    "frequency",
    "fixed_amount",
    "rate_amount",
    "indemnity_eligibility",
    "deductible",
    "limit",
    "required_document",
  ],
  rule_operator: [
    "all",
    "any",
    "not",
    "present",
    "equals",
    "in",
    "range",
    "date_between",
    "days_since",
    "count_before",
    "add",
    "subtract",
    "multiply",
    "min",
    "max",
    "round",
  ],
  unit: ["date", "days", "occurrences", "amount", "currency"],
};

const LABELS: Partial<Record<PolicyCandidateFieldId, string>> = {
  date_boundary: "날짜 경계",
  decimal_boundary: "숫자 경계",
  fact_field: "판정에 필요한 정보",
  required: "필수 조건",
  rule_kind: "규칙 종류",
  rule_operator: "규칙 조건",
  unit: "단위",
};

function editableFields(item: PolicyReviewItem): CandidateField[] {
  return item.fields.filter((field) => EDITABLE_FIELDS.has(field.field_id));
}

function draftValue(value: CandidateScalar): string {
  return value === null ? "" : String(value);
}

function typedValue(
  fieldId: PolicyCandidateFieldId,
  value: string,
): CandidateScalar {
  if (fieldId === "required") return value === "true";
  if (fieldId === "decimal_boundary") return Number(value);
  return value;
}

export function RuleExpressionEditor({
  item,
  onSaved,
}: {
  item: PolicyReviewItem;
  onSaved: (item: PolicyReviewItem) => void;
}) {
  const fields = useMemo(() => editableFields(item), [item]);
  const firstField = fields[0];
  const [fieldId, setFieldId] = useState<PolicyCandidateFieldId>(
    firstField?.field_id ?? "rule_operator",
  );
  const selected =
    fields.find((field) => field.field_id === fieldId) ?? firstField;
  const [value, setValue] = useState(draftValue(selected?.value ?? null));
  const [evidenceId, setEvidenceId] = useState(
    selected?.evidence_ids[0] ?? item.evidence[0]?.evidence_id ?? "",
  );
  const [expectedVersion, setExpectedVersion] = useState(item.expected_version);
  const [error, setError] = useState<string>();
  const [saving, setSaving] = useState(false);

  useEffect(
    () => setExpectedVersion(item.expected_version),
    [item.expected_version],
  );

  function selectField(next: PolicyCandidateFieldId) {
    const field = fields.find((candidate) => candidate.field_id === next);
    if (!field) return;
    setFieldId(next);
    setValue(draftValue(field.value));
    setEvidenceId(field.evidence_ids[0] ?? item.evidence[0]?.evidence_id ?? "");
    setError(undefined);
  }

  async function save() {
    if (!selected || !value || !evidenceId) {
      setError("수정할 값과 근거를 선택해 주세요.");
      return;
    }
    if (
      fieldId === "decimal_boundary" &&
      (!Number.isFinite(Number(value)) || Number(value) < 0)
    ) {
      setError("숫자 경계는 0 이상이어야 합니다.");
      return;
    }
    setSaving(true);
    setError(undefined);
    try {
      const updated = await correctRuleReviewField(item.review_item_id, {
        evidence_id: evidenceId,
        expected_version: expectedVersion,
        field_id: fieldId,
        value: typedValue(fieldId, value),
      });
      setExpectedVersion(updated.expected_version);
      onSaved(updated);
    } catch (reason) {
      setError(
        reason instanceof ApiError && reason.code === "VERSION_CONFLICT"
          ? "다른 변경이 먼저 저장되었습니다. 현재 선택은 유지됩니다."
          : "수정 내용을 저장하지 못했습니다.",
      );
    } finally {
      setSaving(false);
    }
  }

  if (!firstField) {
    return <p className="empty-inline">수정 가능한 구조화 필드가 없습니다.</p>;
  }

  const options = OPTIONS[fieldId];
  return (
    <section className="rule-expression-editor" aria-label="구조화된 규칙 수정">
      <div className="editor-grid">
        <label>
          <span>수정할 항목</span>
          <select
            value={fieldId}
            onChange={(event) =>
              selectField(event.target.value as PolicyCandidateFieldId)
            }
          >
            {fields.map((field) => (
              <option key={field.field_id} value={field.field_id}>
                {LABELS[field.field_id] ?? field.field_id}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>{LABELS[fieldId] ?? fieldId}</span>
          {fieldId === "required" ? (
            <select
              aria-label={LABELS[fieldId]}
              value={value}
              onChange={(event) => setValue(event.target.value)}
            >
              <option value="true">필수</option>
              <option value="false">선택</option>
            </select>
          ) : options ? (
            <select
              aria-label={LABELS[fieldId]}
              value={value}
              onChange={(event) => setValue(event.target.value)}
            >
              {options.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          ) : (
            <input
              aria-label={LABELS[fieldId] ?? fieldId}
              min={fieldId === "decimal_boundary" ? 0 : undefined}
              type={
                fieldId === "decimal_boundary"
                  ? "number"
                  : fieldId === "date_boundary"
                    ? "date"
                    : "text"
              }
              value={value}
              onChange={(event) => setValue(event.target.value)}
            />
          )}
        </label>
        <label>
          <span>근거 Evidence</span>
          <select
            value={evidenceId}
            onChange={(event) => setEvidenceId(event.target.value)}
          >
            {item.evidence.map((evidence) => (
              <option key={evidence.evidence_id} value={evidence.evidence_id}>
                {evidence.document_label} · {evidence.page}페이지
              </option>
            ))}
          </select>
        </label>
      </div>
      {error ? <p role="alert">{error}</p> : null}
      <button
        type="button"
        className="secondary-button"
        disabled={saving}
        onClick={save}
      >
        수정 저장
      </button>
    </section>
  );
}
