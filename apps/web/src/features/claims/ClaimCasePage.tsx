import { useEffect, useState } from "react";

import {
  deleteClaimCase,
  getClaimCase,
  transitionClaimCase,
  updateClaimCase,
  updateClaimChecklist,
  type ClaimCase,
  type ClaimStatus,
} from "../../api/claims";
import type { ClaimChecklistItemResponse } from "../../api/generated";
import { ApiError } from "../../api/errors";
import { useQueryCache, useResource } from "../../api/query-cache";
import {
  ClaimOutcomeForm,
  type ClaimOutcomeMetadata,
} from "./ClaimOutcomeForm";
import { ChecklistEditor } from "./ChecklistEditor";
import { ClaimStatusStepper, STATUS_LABELS } from "./ClaimStatusStepper";

function safeErrorMessage(error: ApiError | undefined): string {
  if (error?.code === "AUTHENTICATION_REQUIRED") {
    return "로그인이 필요합니다. 인증을 확인한 뒤 다시 열어 주세요.";
  }
  if (error?.code === "INVALID_CLAIM_TRANSITION") {
    return "현재 상태에서는 요청한 상태로 변경할 수 없습니다. 최신 상태를 확인해 주세요.";
  }
  if (error?.code === "VERSION_CONFLICT" || error?.status === 409) {
    return "다른 화면에서 기록이 바뀌었습니다. 입력한 내용은 유지했으니 다시 확인해 주세요.";
  }
  return "청구 기록을 저장하지 못했습니다. 입력을 확인한 뒤 다시 시도해 주세요.";
}

function nowIso(): string {
  return new Date().toISOString();
}

function isDecimal(value: string): boolean {
  return /^(?:0|[1-9][0-9]{0,15})(?:\.[0-9]{1,2})?$/.test(value);
}

export function ClaimCasePage({ claimId }: { claimId: string }) {
  const cache = useQueryCache();
  const resource = useResource(`claim:${claimId}`, (signal) =>
    getClaimCase(claimId, signal),
  );
  const [claim, setClaim] = useState<ClaimCase>();
  const [busy, setBusy] = useState(false);
  const [busyChecklist, setBusyChecklist] = useState<string>();
  const [mutationError, setMutationError] = useState<string>();
  const [outcomeTarget, setOutcomeTarget] =
    useState<Extract<ClaimStatus, "paid" | "partially_paid">>();
  const [outcomeResetKey, setOutcomeResetKey] = useState(0);
  const [receiptNumber, setReceiptNumber] = useState("");
  const [claimedAmount, setClaimedAmount] = useState("");
  const [currency, setCurrency] = useState("KRW");
  const [reasonCode, setReasonCode] = useState("");

  useEffect(() => {
    if (!resource.data) return;
    setClaim(resource.data);
    setReceiptNumber(resource.data.receipt_number ?? "");
    setClaimedAmount(resource.data.claimed_amount ?? "");
    setCurrency(resource.data.currency ?? "KRW");
    setReasonCode(resource.data.outcome_reason_code ?? "");
  }, [resource.data]);

  function clearSensitiveDrafts(): void {
    setReceiptNumber("");
    setClaimedAmount("");
    setCurrency("");
    setReasonCode("");
    setOutcomeTarget(undefined);
    setOutcomeResetKey((value) => value + 1);
  }

  async function applyMutation(
    operation: () => Promise<ClaimCase>,
  ): Promise<boolean> {
    setBusy(true);
    setMutationError(undefined);
    try {
      const next = await operation();
      setClaim(next);
      cache.invalidate("claims:list");
      return true;
    } catch (error) {
      const apiError = error instanceof ApiError ? error : undefined;
      setMutationError(safeErrorMessage(apiError));
      if (apiError?.code === "AUTHENTICATION_REQUIRED") {
        clearSensitiveDrafts();
      }
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function transition(
    target: ClaimStatus,
    metadata: Record<string, unknown> = {},
  ): Promise<void> {
    if (!claim) return;
    const succeeded = await applyMutation(() =>
      transitionClaimCase(claim.id, {
        expected_version: claim.version,
        occurred_at: nowIso(),
        target_status: target,
        metadata,
      }),
    );
    if (succeeded) setOutcomeTarget(undefined);
  }

  async function updateMetadata(): Promise<void> {
    if (!claim) return;
    if (claimedAmount && !isDecimal(claimedAmount)) {
      setMutationError("청구 금액은 0 이상 숫자로 입력해 주세요.");
      return;
    }
    if (claimedAmount && !/^[A-Z]{3}$/.test(currency)) {
      setMutationError(
        "청구 금액을 입력할 때 통화도 영문 대문자 3자리로 입력해 주세요.",
      );
      return;
    }
    if (reasonCode && !/^[A-Z][A-Z0-9_]{0,63}$/.test(reasonCode)) {
      setMutationError("결과 사유는 등록된 코드 형식으로 입력해 주세요.");
      return;
    }
    await applyMutation(() =>
      updateClaimCase(claim.id, {
        expected_version: claim.version,
        receipt_number: receiptNumber || null,
        claimed_amount: claimedAmount || null,
        currency: currency || null,
        outcome_reason_code: reasonCode || null,
      }),
    );
  }

  async function updateChecklist(
    item: ClaimChecklistItemResponse,
  ): Promise<void> {
    if (!claim) return;
    setBusyChecklist(item.id);
    setMutationError(undefined);
    try {
      const next = await updateClaimChecklist(claim.id, item.id, {
        expected_version: item.version,
        prepared: !item.prepared,
        note_code: item.note_code,
      });
      setClaim(next);
      cache.invalidate("claims:list");
    } catch (error) {
      const apiError = error instanceof ApiError ? error : undefined;
      setMutationError(safeErrorMessage(apiError));
      if (apiError?.code === "AUTHENTICATION_REQUIRED") {
        clearSensitiveDrafts();
      }
    } finally {
      setBusyChecklist(undefined);
    }
  }

  async function archiveClaim(): Promise<void> {
    if (!claim) return;
    setBusy(true);
    setMutationError(undefined);
    try {
      await deleteClaimCase(claim.id, claim.version);
      window.location.assign("/app/claims");
    } catch (error) {
      const apiError = error instanceof ApiError ? error : undefined;
      setMutationError(safeErrorMessage(apiError));
      if (apiError?.code === "AUTHENTICATION_REQUIRED") {
        clearSensitiveDrafts();
      }
      setBusy(false);
    }
  }

  if (resource.loading && !claim) {
    return (
      <main className="claim-page">
        <p className="claim-muted" role="status">
          청구 기록을 불러오는 중입니다.
        </p>
      </main>
    );
  }
  if (resource.error && !claim) {
    return (
      <main className="claim-page">
        <p className="claim-error" role="alert">
          {safeErrorMessage(resource.error)}
        </p>
      </main>
    );
  }
  if (!claim) {
    return (
      <main className="claim-page">
        <p className="claim-muted">청구 기록을 찾을 수 없습니다.</p>
      </main>
    );
  }

  return (
    <main className="claim-page" id="main-content" tabIndex={-1}>
      <header className="claim-page-heading">
        <a className="claim-back-link" href="/app/claims">
          ← 청구 기록
        </a>
        <p className="claim-kicker">Claim case</p>
        <h1>{claim.insurer_key}</h1>
        <p>
          보험 계약 <code>{claim.policy_contract_id}</code> ·{" "}
          {STATUS_LABELS[claim.status]}
        </p>
      </header>

      {mutationError ? (
        <p className="claim-error" role="alert">
          {mutationError}
        </p>
      ) : null}

      <ClaimStatusStepper
        allowedTransitions={claim.allowed_transitions}
        busy={busy}
        onTransition={(target) => {
          if (target === "paid" || target === "partially_paid") {
            setOutcomeTarget(target);
          } else {
            void transition(
              target,
              target === "denied" && reasonCode
                ? { reason_code: reasonCode }
                : {},
            );
          }
        }}
        status={claim.status}
      />

      {outcomeTarget ? (
        <ClaimOutcomeForm
          busy={busy}
          key={outcomeResetKey}
          onCancel={() => setOutcomeTarget(undefined)}
          resetKey={outcomeResetKey}
          onSubmit={(target, metadata: ClaimOutcomeMetadata) => {
            void transition(target, metadata);
          }}
        />
      ) : null}

      <section className="claim-card" aria-labelledby="claim-metadata-title">
        <div className="claim-section-heading">
          <p className="claim-kicker">Manual record</p>
          <h2 id="claim-metadata-title">접수·금액 기록</h2>
        </div>
        <div className="claim-form-grid">
          <label>
            보험사 접수 번호
            <input
              maxLength={160}
              onChange={(event) => setReceiptNumber(event.target.value)}
              type="text"
              value={receiptNumber}
            />
          </label>
          <label>
            청구 금액
            <input
              inputMode="decimal"
              min="0"
              onChange={(event) => setClaimedAmount(event.target.value)}
              step="0.01"
              type="number"
              value={claimedAmount}
            />
          </label>
          <label>
            통화
            <input
              maxLength={3}
              onChange={(event) =>
                setCurrency(event.target.value.toUpperCase())
              }
              type="text"
              value={currency}
            />
          </label>
          <label>
            결과 사유 코드
            <input
              maxLength={64}
              onChange={(event) =>
                setReasonCode(event.target.value.toUpperCase())
              }
              type="text"
              value={reasonCode}
            />
          </label>
        </div>
        <div className="claim-action-row">
          <button
            className="claim-secondary-button"
            disabled={busy}
            onClick={() => void updateMetadata()}
            type="button"
          >
            기록 저장
          </button>
          <button
            className="claim-quiet-button"
            disabled={busy}
            onClick={() => void archiveClaim()}
            type="button"
          >
            기록 보관
          </button>
        </div>
      </section>

      <ChecklistEditor
        busyItemId={busyChecklist}
        items={claim.checklist}
        onUpdate={(item) => void updateChecklist(item)}
      />

      <section className="claim-card" aria-labelledby="claim-snapshot-title">
        <div className="claim-section-heading">
          <p className="claim-kicker">Immutable evidence</p>
          <h2 id="claim-snapshot-title">접수 시점 근거 스냅샷</h2>
        </div>
        <dl className="claim-summary-grid">
          <div>
            <dt>스냅샷 버전</dt>
            <dd>{claim.snapshot.snapshot_version}</dd>
          </div>
          <div>
            <dt>계산 결과</dt>
            <dd>{claim.snapshot.calculation.statuses?.length ?? 0}건</dd>
          </div>
          <div>
            <dt>규칙 버전</dt>
            <dd>{claim.snapshot.rules.rule_version_ids?.length ?? 0}건</dd>
          </div>
          <div>
            <dt>근거 Evidence</dt>
            <dd>{claim.snapshot.evidence.evidence_ids?.length ?? 0}건</dd>
          </div>
        </dl>
        <p className="claim-hash">SHA-256: {claim.snapshot.snapshot_sha256}</p>
        <p className="claim-boundary-note">
          이후 재분석이 진행되어도 이 스냅샷은 접수 당시 근거로 보존됩니다.
        </p>
      </section>

      <section className="claim-card" aria-labelledby="claim-history-title">
        <div className="claim-section-heading">
          <p className="claim-kicker">Audit trail</p>
          <h2 id="claim-history-title">상태 변경 이력</h2>
        </div>
        <ol className="claim-history-list">
          {claim.status_events.map((event, index) => (
            <li key={`${event.occurred_at}-${index}`}>
              <strong>{STATUS_LABELS[event.to_status]}</strong>
              <time dateTime={event.occurred_at}>{event.occurred_at}</time>
              {event.reason_code ? <span>{event.reason_code}</span> : null}
            </li>
          ))}
        </ol>
      </section>
    </main>
  );
}
