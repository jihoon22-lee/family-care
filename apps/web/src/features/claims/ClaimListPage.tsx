import { useState } from "react";

import { useQueryCache, useResource } from "../../api/query-cache";
import {
  listClaimCases,
  listDeletedClaimCases,
  restoreClaimCase,
} from "../../api/claims";
import type { ClaimCaseResponse } from "../../api/generated";
import { ApiError } from "../../api/errors";
import { STATUS_LABELS } from "./ClaimStatusStepper";

function safeErrorMessage(): string {
  return "청구 기록을 불러오지 못했습니다. 잠시 후 다시 확인해 주세요.";
}

function claimTitle(claim: ClaimCaseResponse): string {
  return `${claim.insurer_key} · ${claim.policy_contract_id.slice(0, 8)}`;
}

export function ClaimListPage({
  deletedOnly = false,
}: {
  deletedOnly?: boolean;
}) {
  const cache = useQueryCache();
  const resource = useResource(
    deletedOnly ? "claims:trash" : "claims:list",
    (signal) =>
      deletedOnly ? listDeletedClaimCases(signal) : listClaimCases({}, signal),
  );
  const [busyId, setBusyId] = useState<string>();
  const [restoreError, setRestoreError] = useState<string>();

  function reload() {
    cache.invalidate(deletedOnly ? "claims:trash" : "claims:list");
  }

  async function restore(claim: ClaimCaseResponse): Promise<void> {
    setBusyId(claim.id);
    setRestoreError(undefined);
    try {
      await restoreClaimCase(claim.id, claim.version);
      cache.invalidate("claims:trash");
      cache.invalidate("claims:list");
    } catch (error) {
      setRestoreError(
        error instanceof ApiError && error.code === "VERSION_CONFLICT"
          ? "다른 화면에서 기록이 바뀌었습니다. 목록을 새로 고쳐 주세요."
          : "기록을 복원하지 못했습니다. 잠시 후 다시 시도해 주세요.",
      );
    } finally {
      setBusyId(undefined);
    }
  }

  return (
    <main className="claim-page" id="main-content" tabIndex={-1}>
      <header className="claim-page-heading">
        <p className="claim-kicker">Claim ledger</p>
        <h1>{deletedOnly ? "보관된 청구 기록" : "청구 기록"}</h1>
        <p>
          보험사에 직접 제출한 준비·접수·지급 결과를 보험사별로 기록합니다.
          FamilyCare가 외부 접수를 수행하지는 않습니다.
        </p>
        <p className="claim-page-links">
          {deletedOnly ? (
            <a href="/app/claims">활성 기록으로 돌아가기</a>
          ) : (
            <a href="/app/claims/trash">보관된 기록 보기</a>
          )}
        </p>
      </header>
      {restoreError ? (
        <p className="claim-error" role="alert">
          {restoreError}
        </p>
      ) : null}
      {resource.loading && !resource.data ? (
        <p className="claim-muted" role="status" aria-live="polite">
          청구 기록을 불러오는 중입니다.
        </p>
      ) : null}
      {resource.error ? (
        <div className="claim-error" role="alert">
          <p>{safeErrorMessage()}</p>
          <button
            className="claim-secondary-button"
            onClick={reload}
            type="button"
          >
            다시 시도
          </button>
        </div>
      ) : null}
      {resource.data && resource.data.items.length === 0 ? (
        <section className="claim-empty-state">
          <h2>아직 청구 기록이 없습니다.</h2>
          <p>
            사건 결과에서 청구 검토를 시작하면 보험사별 기록을 만들 수 있습니다.
          </p>
        </section>
      ) : null}
      {resource.data && resource.data.items.length > 0 ? (
        <ul className="claim-list">
          {resource.data.items.map((claim) => (
            <li className="claim-list-item" key={claim.id}>
              {deletedOnly ? (
                <div className="claim-list-summary">
                  <span className="claim-list-title">{claimTitle(claim)}</span>
                  <span className="claim-list-meta">
                    {STATUS_LABELS[claim.status]} · 버전 {claim.version}
                  </span>
                </div>
              ) : (
                <a href={`/app/claims/${encodeURIComponent(claim.id)}`}>
                  <span className="claim-list-title">{claimTitle(claim)}</span>
                  <span className="claim-list-meta">
                    {STATUS_LABELS[claim.status]} · 버전 {claim.version}
                  </span>
                </a>
              )}
              {deletedOnly ? (
                <button
                  className="claim-secondary-button"
                  disabled={busyId === claim.id}
                  onClick={() => void restore(claim)}
                  type="button"
                >
                  복원
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </main>
  );
}
