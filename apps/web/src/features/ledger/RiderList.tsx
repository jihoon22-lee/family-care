import type { RiderResponse } from "../../api/generated";

const STATUS_LABEL = {
  active: "유지 확인",
  cancelled: "해지",
  expired: "만료",
  inactive: "비활성",
  unknown: "상태 확인 필요",
} as const;

function amount(rider: RiderResponse): string {
  if (rider.benefit_type === "indemnity") return "실손형 · 영수증 기준 확인";
  if (!rider.insured_amount) return "가입금액 확인 필요";
  return `${rider.insured_amount} ${rider.currency ?? ""}`.trim();
}

export function RiderList({ riders }: { riders: RiderResponse[] }) {
  if (riders.length === 0) {
    return <p className="empty-inline">확인된 가입 담보가 없습니다.</p>;
  }
  return (
    <ul className="rider-list" aria-label="실제 가입 담보">
      {riders.map((rider) => (
        <li key={rider.id} className="rider-row">
          <div>
            <span
              className={`status-dot status-${rider.status}`}
              aria-hidden="true"
            />
            <h3>{rider.display_name}</h3>
            <p>{amount(rider)}</p>
          </div>
          <dl>
            <div>
              <dt>계약 상태</dt>
              <dd>{STATUS_LABEL[rider.status]}</dd>
            </div>
            <div>
              <dt>근거</dt>
              <dd>증권 · {rider.source_evidence.physical_page}페이지</dd>
            </div>
          </dl>
        </li>
      ))}
    </ul>
  );
}
