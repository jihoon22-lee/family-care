import { useEffect, useState } from "react";

import type { ApiError } from "../../api/errors";
import { CandidateReviewQueue } from "./CandidateReviewQueue";
import { FamilyMemberPicker } from "./FamilyMemberPicker";
import { InsuranceDocumentInventory } from "./InsuranceDocumentInventory";
import { PolicySummaryCard } from "./PolicySummaryCard";
import { useLedger } from "./useLedger";

function errorCopy(error: ApiError): string {
  if (error.code === "AUTHENTICATION_REQUIRED") {
    return "로그인이 필요합니다. 인증을 확인한 뒤 다시 열어 주세요.";
  }
  return "보장 원장을 불러오지 못했습니다. 잠시 후 다시 확인해 주세요.";
}

export function LedgerPage({ memberId }: { memberId?: string }) {
  const [selectedMemberId, setSelectedMemberId] = useState(memberId);
  const { data, loading, error, reload } = useLedger(selectedMemberId);

  useEffect(() => setSelectedMemberId(memberId), [memberId]);
  useEffect(() => {
    if (!selectedMemberId && data?.selectedMember) {
      setSelectedMemberId(data.selectedMember.id);
    }
  }, [data?.selectedMember, selectedMemberId]);

  function changeMember(nextMemberId: string) {
    setSelectedMemberId(nextMemberId);
    const nextPath = `/app/members/${encodeURIComponent(nextMemberId)}/ledger`;
    if (window.location.pathname !== nextPath)
      window.history.replaceState(null, "", nextPath);
  }

  return (
    <main id="main-content" className="ledger-page" tabIndex={-1}>
      <section className="ledger-intro" aria-labelledby="ledger-title">
        <div>
          <p className="eyebrow">가입 사실과 근거를 함께 묶은 원장</p>
          <h1 id="ledger-title">우리 가족 보장 원장</h1>
          <p>
            증권에서 확인된 계약과 실제 가입 담보만 본문에 표시합니다. 자동
            분석에서 더 확인할 항목은 원장과 분리해 검토합니다.
          </p>
        </div>
        {data?.familyMembers.length ? (
          <FamilyMemberPicker
            members={data.familyMembers}
            selectedId={data.selectedMember?.id ?? data.familyMembers[0].id}
            onChange={changeMember}
          />
        ) : null}
      </section>

      {loading && !data ? (
        <p className="loading-state" role="status" aria-live="polite">
          보장 원장을 불러오는 중입니다.
        </p>
      ) : null}
      {error ? <p role="alert">{errorCopy(error)}</p> : null}
      {!loading && !error && data?.familyMembers.length === 0 ? (
        <section className="empty-state">
          <h2>등록된 가족 구성원이 없습니다.</h2>
          <p>
            먼저 보험 대상 가족 구성원을 등록하면 보장 원장을 시작할 수
            있습니다.
          </p>
        </section>
      ) : null}

      {data?.selectedMember ? (
        <>
          <section className="ledger-toolbar" aria-label="원장 현황">
            <div>
              <span>현재 대상</span>
              <strong>{data.selectedMember.display_name}</strong>
              <a
                className="event-start-link"
                href={`/app/events/new?member=${encodeURIComponent(data.selectedMember.id)}`}
              >
                사건 기록 시작
              </a>
            </div>
            <div
              className="review-count"
              role="status"
              aria-label="검토 필요 NEEDS_REVIEW"
            >
              <span>추가 확인 필요</span>
              <strong>{data.reviewItems.length}</strong>
            </div>
          </section>

          <InsuranceDocumentInventory memberId={data.selectedMember.id} />

          <div className="ledger-columns">
            <section
              className="policy-ledger"
              aria-labelledby="policy-ledger-title"
            >
              <div className="folio-heading">
                <span>Policy folio</span>
                <h2 id="policy-ledger-title">확인된 계약</h2>
              </div>
              {data.policies.length === 0 ? (
                <div className="empty-state compact">
                  <h3>확인된 계약이 없습니다.</h3>
                  <p>
                    증권 분석이 끝나면 근거가 확인된 계약이 이곳에 표시됩니다.
                  </p>
                </div>
              ) : (
                <div className="policy-stack">
                  {data.policies.map(({ policy, riders }) => (
                    <PolicySummaryCard
                      key={policy.id}
                      policy={policy}
                      riders={riders}
                    />
                  ))}
                </div>
              )}
            </section>
            <CandidateReviewQueue
              items={data.reviewItems}
              memberDisplayName={data.selectedMember.display_name}
              onMutated={reload}
            />
          </div>
        </>
      ) : null}
    </main>
  );
}
