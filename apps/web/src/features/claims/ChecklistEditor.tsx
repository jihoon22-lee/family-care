import type { ClaimChecklistItemResponse } from "../../api/generated";

function documentLabel(kind: string): string {
  const labels: Record<string, string> = {
    claim_form: "청구서",
    medical_receipt: "진료비 영수증",
    treatment_confirmation: "치료 확인 자료",
    identity: "본인 확인 자료",
  };
  return labels[kind] ?? "필요 자료";
}

export function ChecklistEditor({
  busyItemId,
  items,
  onUpdate,
}: {
  busyItemId?: string;
  items: ClaimChecklistItemResponse[];
  onUpdate: (item: ClaimChecklistItemResponse) => void;
}) {
  return (
    <section className="claim-card" aria-labelledby="claim-checklist-title">
      <div className="claim-section-heading">
        <p className="claim-kicker">Preparation</p>
        <h2 id="claim-checklist-title">준비 항목</h2>
      </div>
      {items.length === 0 ? (
        <p className="claim-muted">
          저장된 준비 항목이 없습니다. 필요한 자료는 보험사 안내를 확인하세요.
        </p>
      ) : (
        <ul className="claim-checklist">
          {items.map((item) => {
            const label = documentLabel(item.document_kind);
            return (
              <li key={item.id}>
                <label>
                  <input
                    aria-label={`${label} 준비 완료`}
                    checked={item.prepared}
                    disabled={busyItemId === item.id}
                    onChange={() => onUpdate(item)}
                    type="checkbox"
                  />
                  <span>
                    <strong>{label}</strong>
                    <small>
                      {item.required ? "필수" : "선택"}
                      {item.conditional ? " · 조건부" : ""} ·{" "}
                      {item.requirement_code}
                    </small>
                  </span>
                </label>
                {item.note_code ? (
                  <span className="claim-code">{item.note_code}</span>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      <p className="claim-boundary-note">
        이 목록은 준비 상태만 기록합니다. 파일, 사진, OCR 내용을 FamilyCare에
        업로드하지 않습니다.
      </p>
    </section>
  );
}
