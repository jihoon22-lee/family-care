import type { PolicyResponse, RiderResponse } from "../../api/generated";

import { RiderList } from "./RiderList";

const POLICY_STATUS = {
  active: "계약 유지",
  cancelled: "해지",
  expired: "만료",
  inactive: "비활성",
  unknown: "현재 상태 확인 필요",
} as const;

export function PolicySummaryCard({
  policy,
  riders,
}: {
  policy: PolicyResponse;
  riders: RiderResponse[];
}) {
  return (
    <article className="policy-card" aria-labelledby={`policy-${policy.id}`}>
      <header className="policy-heading">
        <div>
          <p>{policy.insurer_display}</p>
          <h2 id={`policy-${policy.id}`}>{policy.product_display}</h2>
        </div>
        <span className={`policy-status status-${policy.status}`}>
          {POLICY_STATUS[policy.status]}
        </span>
      </header>
      <dl className="policy-facts">
        <div>
          <dt>보장 시작</dt>
          <dd>{policy.coverage_start_date ?? "확인 필요"}</dd>
        </div>
        <div>
          <dt>보장 종료</dt>
          <dd>{policy.coverage_end_date ?? "확인 필요"}</dd>
        </div>
        <div>
          <dt>증권 근거</dt>
          <dd>{policy.source_evidence.physical_page}페이지</dd>
        </div>
      </dl>
      <section
        className="rider-section"
        aria-label={`${policy.product_display} 가입 담보`}
      >
        <div className="section-heading">
          <span>Enrolled riders</span>
          <strong>실제 가입 담보 {riders.length}건</strong>
        </div>
        <RiderList riders={riders} />
      </section>
    </article>
  );
}
