import { useEffect, useMemo, useState } from "react";

import {
  cancelDocumentBatch,
  createDocumentBatch,
  getDocumentBatch,
  handoffBatchPassword,
  listImportSources,
} from "../../api/document-imports";
import { ApiError } from "../../api/errors";
import type {
  BatchSourceRequest,
  BatchResponse,
  FamilyMemberResponse,
  ImportSourceResponse,
} from "../../api/generated";
import { listFamilyMembers } from "../../api/ledger";
import { FamilyMemberPicker } from "../ledger/FamilyMemberPicker";
import { BatchPasswordDialog } from "./BatchPasswordDialog";
import { BatchProgress } from "./BatchProgress";
import { ImportSourcePicker } from "./ImportSourcePicker";

const TERMINAL_STATES = new Set<BatchResponse["state"]>([
  "cancelled",
  "failed",
  "succeeded",
]);

function safeErrorMessage(): string {
  return "문서 가져오기를 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.";
}

function isRetryablePollingError(reason: unknown): boolean {
  return (
    reason instanceof ApiError && (reason.status === 0 || reason.status >= 500)
  );
}

function requestedMemberId(): string | undefined {
  const value = new URLSearchParams(window.location.search).get("member");
  return value || undefined;
}

export function ImportPage() {
  const [members, setMembers] = useState<FamilyMemberResponse[]>([]);
  const [sources, setSources] = useState<ImportSourceResponse[]>([]);
  const [memberId, setMemberId] = useState(() => requestedMemberId() ?? "");
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(
    new Set(),
  );
  const [selectedKinds, setSelectedKinds] = useState<
    ReadonlyMap<string, BatchSourceRequest["document_kind"]>
  >(new Map());
  const [batch, setBatch] = useState<BatchResponse>();
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [passwordError, setPasswordError] = useState<string>();
  const [passwordDismissed, setPasswordDismissed] = useState(false);

  function clearPrivateState(): void {
    setSelectedIds(new Set());
    setSelectedKinds(new Map());
    setBatch(undefined);
    setPasswordError(undefined);
    setPasswordDismissed(false);
  }

  function handleError(reason: unknown, password = false): void {
    if (
      reason instanceof ApiError &&
      reason.code === "AUTHENTICATION_REQUIRED"
    ) {
      clearPrivateState();
      return;
    }
    if (password) setPasswordError(safeErrorMessage());
    else setError(safeErrorMessage());
  }

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    void Promise.all([
      listFamilyMembers(controller.signal),
      listImportSources(controller.signal),
    ])
      .then(([loadedMembers, loadedSources]) => {
        setMembers(loadedMembers);
        setSources(loadedSources);
        const requestedId = requestedMemberId();
        setMemberId((current) => {
          if (
            requestedId &&
            loadedMembers.some((member) => member.id === requestedId)
          ) {
            return requestedId;
          }
          if (loadedMembers.some((member) => member.id === current))
            return current;
          return loadedMembers[0]?.id || "";
        });
      })
      .catch((reason: unknown) => {
        if (!(reason instanceof DOMException && reason.name === "AbortError")) {
          handleError(reason);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, []);

  const hasActiveItems = useMemo(
    () =>
      batch?.items.some((item) =>
        ["queued", "running", "retryable_failed"].includes(item.state),
      ) ?? false,
    [batch],
  );

  useEffect(() => {
    if (!batch || TERMINAL_STATES.has(batch.state) || !hasActiveItems)
      return undefined;
    const controller = new AbortController();
    let attempts = 0;
    let timer: number | undefined;
    const poll = async (): Promise<void> => {
      if (controller.signal.aborted || attempts >= 300) return;
      attempts += 1;
      try {
        const next = await getDocumentBatch(batch.batch_id, controller.signal);
        setError(undefined);
        setBatch(next);
        if (!TERMINAL_STATES.has(next.state)) {
          timer = window.setTimeout(() => void poll(), 1000);
        }
      } catch (reason) {
        if (!(reason instanceof DOMException && reason.name === "AbortError")) {
          handleError(reason);
          if (isRetryablePollingError(reason)) {
            timer = window.setTimeout(() => void poll(), 1000);
          }
        }
      }
    };
    timer = window.setTimeout(() => void poll(), 1000);
    return () => {
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [batch?.batch_id, batch?.state, hasActiveItems]);

  function toggleSource(sourceId: string, selected: boolean): void {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (selected) next.add(sourceId);
      else next.delete(sourceId);
      return next;
    });
    setSelectedKinds((current) => {
      const next = new Map(current);
      if (selected) next.set(sourceId, current.get(sourceId) ?? "supporting");
      else next.delete(sourceId);
      return next;
    });
  }

  function changeSourceKind(
    sourceId: string,
    documentKind: BatchSourceRequest["document_kind"],
  ): void {
    setSelectedKinds((current) => {
      const next = new Map(current);
      if (selectedIds.has(sourceId)) next.set(sourceId, documentKind);
      return next;
    });
  }

  async function create(): Promise<void> {
    if (!memberId || selectedIds.size === 0) return;
    setBusy(true);
    setError(undefined);
    setPasswordDismissed(false);
    try {
      const selectedSources = [...selectedIds].map((sourceId) => ({
        source_id: sourceId,
        document_kind: selectedKinds.get(sourceId) ?? "supporting",
      }));
      setBatch(await createDocumentBatch(memberId, selectedSources));
    } catch (reason) {
      handleError(reason);
    } finally {
      setBusy(false);
    }
  }

  async function submitPassword(password: string): Promise<void> {
    if (!batch) return;
    setBusy(true);
    setPasswordError(undefined);
    setPasswordDismissed(false);
    try {
      setBatch(await handoffBatchPassword(batch.batch_id, password));
    } catch (reason) {
      handleError(reason, true);
    } finally {
      setBusy(false);
    }
  }

  async function cancel(): Promise<void> {
    if (!batch) return;
    setBusy(true);
    setError(undefined);
    try {
      setBatch(await cancelDocumentBatch(batch.batch_id));
    } catch (reason) {
      handleError(reason);
    } finally {
      setBusy(false);
    }
  }

  const passwordRequired =
    batch?.items.some((item) => item.state === "password_required") ?? false;

  return (
    <main className="import-page" id="main-content" tabIndex={-1}>
      <header className="import-hero">
        <p className="import-eyebrow">Private document intake</p>
        <h1>보험 PDF 가져오기</h1>
        <p>
          PC의 비공개 가져오기 폴더에서 문서를 선택합니다. 브라우저는 파일이나
          폴더 경로를 받거나 보관하지 않습니다.
        </p>
      </header>
      {error ? (
        <p className="import-error" role="alert">
          {error}
        </p>
      ) : null}
      {loading ? (
        <p className="import-muted" aria-live="polite" role="status">
          가져올 문서를 확인하는 중입니다.
        </p>
      ) : null}
      {!loading && !batch ? (
        <section className="import-setup" aria-labelledby="import-setup-title">
          <div className="import-section-heading">
            <div>
              <p className="import-eyebrow">One member per batch</p>
              <h2 id="import-setup-title">대상과 문서 선택</h2>
            </div>
            <span>{selectedIds.size}개 선택</span>
          </div>
          {members.length > 0 ? (
            <FamilyMemberPicker
              members={members}
              onChange={setMemberId}
              selectedId={memberId}
            />
          ) : (
            <p className="import-muted">
              먼저 보장 원장에 가족 구성원을 추가해 주세요.
            </p>
          )}
          <ImportSourcePicker
            disabled={busy}
            onKindChange={changeSourceKind}
            onChange={toggleSource}
            selectedIds={selectedIds}
            selectedKinds={selectedKinds}
            sources={sources}
          />
          <div className="import-actions">
            <button
              className="import-primary-button"
              disabled={busy || !memberId || selectedIds.size === 0}
              onClick={() => void create()}
              type="button"
            >
              {busy ? "배치 만드는 중…" : "가져오기 시작"}
            </button>
          </div>
        </section>
      ) : null}
      {batch ? (
        <>
          <BatchProgress
            batch={batch}
            busy={busy}
            onCancel={() => void cancel()}
          />
          {batch.state === "succeeded" ? (
            <p className="import-complete-link">
              문서 처리가 끝났습니다. 보장 원장에서 확인할 수 있습니다.
              <a
                href={`/app/members/${encodeURIComponent(batch.family_member_id)}/ledger`}
              >
                보장 원장 열기
              </a>
            </p>
          ) : null}
        </>
      ) : null}
      <BatchPasswordDialog
        busy={busy}
        error={passwordError}
        onCancel={() => {
          setPasswordError(undefined);
          setPasswordDismissed(true);
        }}
        onSubmit={submitPassword}
        open={passwordRequired && !passwordDismissed}
      />
    </main>
  );
}
