import { useEffect, useMemo, useState } from "react";

import type {
  CandidateField,
  CandidateScalar,
  PolicyCandidateFieldId,
  PolicyReviewItem,
} from "../../api/generated";
import { ApiError } from "../../api/errors";
import { correctCandidateField, getReviewItem } from "../../api/ledger";

function initialField(item: PolicyReviewItem): CandidateField {
  const field =
    item.fields.find((candidate) => candidate.field_id === "rider_name") ??
    item.fields[0];
  if (!field)
    throw new Error("A policy review item must contain at least one field");
  return field;
}

function draftValue(value: CandidateScalar): string {
  if (value === null) return "";
  return String(value);
}

function typedValue(
  fieldId: PolicyCandidateFieldId,
  value: string,
): CandidateScalar {
  if (fieldId === "sum_assured") return Number(value);
  if (fieldId === "renewable") return value === "true";
  return value;
}

function validationMessage(
  fieldId: PolicyCandidateFieldId,
  value: string,
): string | undefined {
  if (!value.trim()) return "수정할 값을 입력해 주세요.";
  if (
    fieldId === "sum_assured" &&
    (!Number.isFinite(Number(value)) || Number(value) < 0)
  ) {
    return "가입금액은 0 이상이어야 합니다.";
  }
  if (fieldId === "currency" && !/^[A-Z]{3}$/.test(value)) {
    return "통화는 영문 대문자 세 글자로 입력해 주세요.";
  }
  if (
    [
      "contract_start",
      "contract_end",
      "coverage_start",
      "coverage_end",
    ].includes(fieldId) &&
    !/^\d{4}-\d{2}-\d{2}$/.test(value)
  ) {
    return "날짜는 YYYY-MM-DD 형식으로 입력해 주세요.";
  }
  return undefined;
}

export function CandidateFieldEditor({
  item,
  onSaved,
}: {
  item: PolicyReviewItem;
  onSaved: (item: PolicyReviewItem) => void;
}) {
  const startingField = initialField(item);
  const [fieldId, setFieldId] = useState(startingField.field_id);
  const [value, setValue] = useState(draftValue(startingField.value));
  const [evidenceId, setEvidenceId] = useState(
    startingField.evidence_ids[0] ?? "",
  );
  const [expectedVersion, setExpectedVersion] = useState(item.expected_version);
  const [error, setError] = useState<string>();
  const [saving, setSaving] = useState(false);

  const selectedField = useMemo(
    () =>
      item.fields.find((field) => field.field_id === fieldId) ?? startingField,
    [fieldId, item.fields],
  );

  useEffect(() => {
    setExpectedVersion(item.expected_version);
  }, [item.expected_version]);

  function selectField(nextFieldId: PolicyCandidateFieldId) {
    const field = item.fields.find(
      (candidate) => candidate.field_id === nextFieldId,
    );
    if (!field) return;
    setFieldId(nextFieldId);
    setValue(draftValue(field.value));
    setEvidenceId(field.evidence_ids[0] ?? item.evidence[0]?.evidence_id ?? "");
    setError(undefined);
  }

  async function save() {
    const invalid = validationMessage(fieldId, value);
    if (invalid || !evidenceId) {
      setError(invalid ?? "근거 Evidence를 선택해 주세요.");
      return;
    }
    const policyId = item.aggregate_id;
    if (!policyId) {
      setError("연결된 계약을 확인한 뒤 수정할 수 있습니다.");
      return;
    }
    setSaving(true);
    setError(undefined);
    try {
      const updated = await correctCandidateField(policyId, {
        evidence_id: evidenceId,
        expected_version: expectedVersion,
        field_id: fieldId,
        value: typedValue(fieldId, value),
      });
      setExpectedVersion(updated.expected_version);
      onSaved(updated);
    } catch (reason) {
      if (reason instanceof ApiError && reason.code === "VERSION_CONFLICT") {
        setError("다른 변경이 먼저 저장되었습니다");
        try {
          const latest = await getReviewItem(item.review_item_id);
          setExpectedVersion(latest.expected_version);
        } catch {
          // The stable conflict remains actionable without exposing fetch details.
        }
      } else {
        setError("수정 내용을 저장하지 못했습니다.");
      }
    } finally {
      setSaving(false);
    }
  }

  const inputType = fieldId === "sum_assured" ? "number" : "text";
  return (
    <section className="candidate-editor" aria-label="후보 필드 수정">
      <div className="editor-grid">
        <label>
          <span>수정할 필드</span>
          <select
            value={fieldId}
            onChange={(event) =>
              selectField(event.target.value as PolicyCandidateFieldId)
            }
          >
            {item.fields.map((field) => (
              <option key={field.field_id} value={field.field_id}>
                {field.field_id}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>{fieldId}</span>
          <input
            aria-label={fieldId}
            min={fieldId === "sum_assured" ? 0 : undefined}
            type={inputType}
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
        </label>
        <label>
          <span>근거 Evidence</span>
          <select
            value={evidenceId}
            onChange={(event) => setEvidenceId(event.target.value)}
          >
            <option value="">선택해 주세요</option>
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
        {error === "다른 변경이 먼저 저장되었습니다"
          ? "다시 시도"
          : "수정 저장"}
      </button>
      <span className="visually-hidden">
        현재 값 {draftValue(selectedField.value)}
      </span>
    </section>
  );
}
