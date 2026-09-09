import { useEffect, useRef, useState } from "react";

import {
  analyzeMedicalEvent,
  createMedicalEvent,
  createReceiptLine,
  deleteReceiptLine,
  getMedicalEvent,
  getStructuringJob,
  listReceiptLines,
  structureMedicalEvent,
  updateMedicalEvent,
  updateReceiptLine,
  type MedicalEvent,
  type ReceiptLine,
} from "../../api/events";
import type { StructuringJobResponse } from "../../api/generated";
import {
  EventComposer,
  type EventDraftView,
  type ReceiptLineView,
} from "./EventComposer";
import styles from "./EventComposer.module.css";
import { useFamilyMemberLabel } from "../ledger/useFamilyMemberLabel";
import { authStore } from "../identity/authStore";

const STRUCTURING_POLL_INTERVAL_MS = 750;
const STRUCTURING_POLL_LIMIT = 80;

function receiptView(line: ReceiptLine): ReceiptLineView {
  return {
    amount: line.amount,
    category: line.category,
    confirmation_level: line.confirmation_level,
    coverage_category: line.coverage_category,
    currency: line.currency,
    id: line.id,
    note_code: line.note_code,
    version: line.version,
  };
}

function createReceiptInput(line: ReceiptLineView) {
  return {
    amount: line.amount,
    category: line.category,
    confirmation_level: line.confirmation_level,
    coverage_category: line.coverage_category,
    currency: line.currency,
    note_code: line.note_code ?? undefined,
  };
}

async function synchronizeReceiptLines(
  eventId: string,
  draftLines: ReceiptLineView[],
  currentLines: ReceiptLineView[],
  signal?: AbortSignal,
  onProgress?: (draft: ReceiptLineView[], persisted: ReceiptLineView[]) => void,
): Promise<ReceiptLineView[]> {
  const synchronized = [...draftLines];
  let persisted = [...currentLines];
  const progress = () => onProgress?.([...synchronized], [...persisted]);
  const retainedIds = new Set(
    draftLines.flatMap((line) => (line.id ? [line.id] : [])),
  );
  for (const current of currentLines) {
    if (current.id && current.version && !retainedIds.has(current.id)) {
      await deleteReceiptLine(eventId, current.id, current.version, signal);
      signal?.throwIfAborted();
      persisted = persisted.filter((line) => line.id !== current.id);
      progress();
    }
  }

  for (const [index, line] of draftLines.entries()) {
    signal?.throwIfAborted();
    const previous = persisted.find(
      (value) => value.id === line.id && value.version === line.version,
    );
    if (
      previous &&
      JSON.stringify(createReceiptInput(previous)) ===
        JSON.stringify(createReceiptInput(line))
    )
      continue;
    const saved =
      line.id && line.version
        ? await updateReceiptLine(
            eventId,
            line.id,
            {
              ...createReceiptInput(line),
              expected_version: line.version,
            },
            signal,
          )
        : await createReceiptLine(eventId, createReceiptInput(line), signal);
    signal?.throwIfAborted();
    const savedLine = receiptView(saved);
    synchronized[index] = savedLine;
    persisted = [
      ...persisted.filter((value) => value.id !== savedLine.id),
      savedLine,
    ];
    progress();
  }
  return synchronized;
}

function updateInput(event: MedicalEvent, draft: EventDraftView) {
  return {
    event_date: draft.event_date,
    expected_version: event.version,
    mode: draft.mode,
    situation: draft.situation,
    ...(draft.facts.length > 0
      ? {
          structured_facts: draft.facts.map((fact) => ({
            field_id: fact.field_id,
            value: fact.value,
          })),
        }
      : {}),
    visit_date: draft.visit_date,
  };
}

function waitForNextPoll(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const cancel = () => {
      window.clearTimeout(timer);
      reject(new DOMException("Cancelled", "AbortError"));
    };
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", cancel);
      resolve();
    }, STRUCTURING_POLL_INTERVAL_MS);
    if (signal.aborted) cancel();
    else signal.addEventListener("abort", cancel, { once: true });
  });
}

function structuringFailed(job: StructuringJobResponse): boolean {
  return ["permanently_failed", "cancelled"].includes(job.state);
}

async function waitForStructuring(statusUrl: string, signal: AbortSignal) {
  for (let attempt = 0; attempt < STRUCTURING_POLL_LIMIT; attempt += 1) {
    const job = await getStructuringJob(statusUrl, signal);
    if (job.state === "succeeded") return job;
    if (structuringFailed(job)) throw new Error("structuring failed");
    await waitForNextPoll(signal);
  }
  throw new Error("structuring timed out");
}

function EventEditor({
  memberId,
  initialEvent,
  initialMode,
  initialReceiptLines = [],
}: {
  memberId: string;
  initialEvent?: MedicalEvent;
  initialMode?: EventDraftView["mode"];
  initialReceiptLines?: ReceiptLineView[];
}) {
  const [medicalEvent, setMedicalEvent] = useState(initialEvent);
  const [receiptLines, setReceiptLines] = useState(initialReceiptLines);
  const persistedReceipts = useRef(initialReceiptLines);
  const [editorRevision, setEditorRevision] = useState(0);
  const memberLabel = useFamilyMemberLabel(memberId);
  const request = useRef<AbortController | null>(null);
  const pending = useRef<Promise<void> | null>(null);
  const expired = useRef(false);
  const [sessionExpired, setSessionExpired] = useState(false);
  useEffect(() => () => request.current?.abort(), []);
  useEffect(
    () =>
      authStore.registerCacheClearer(() => {
        expired.current = true;
        request.current?.abort();
        setMedicalEvent(undefined);
        setReceiptLines([]);
        persistedReceipts.current = [];
        setSessionExpired(true);
      }),
    [],
  );

  function perform(
    action: (signal: AbortSignal) => Promise<void>,
  ): Promise<void> {
    if (pending.current) return pending.current;
    if (expired.current)
      return Promise.reject(new DOMException("Cancelled", "AbortError"));
    const controller = new AbortController();
    request.current = controller;
    const promise = action(controller.signal).finally(() => {
      if (request.current === controller) {
        request.current = null;
        pending.current = null;
      }
    });
    pending.current = promise;
    return promise;
  }

  async function persistDraft(
    draft: EventDraftView,
    signal: AbortSignal,
  ): Promise<MedicalEvent> {
    const progress = (
      draftLines: ReceiptLineView[],
      persisted: ReceiptLineView[],
    ) => {
      persistedReceipts.current = persisted;
      setReceiptLines(draftLines);
    };
    if (!medicalEvent) {
      const created = await createMedicalEvent(
        {
          event_date: draft.event_date,
          facts: {},
          family_member_id: draft.family_member_id,
          mode: draft.mode,
          situation: draft.situation,
          visit_date: draft.visit_date,
        },
        signal,
      );
      signal.throwIfAborted();
      window.history.replaceState(
        {},
        "",
        `/app/events/${encodeURIComponent(created.id)}`,
      );
      let savedLines: ReceiptLineView[];
      try {
        savedLines = await synchronizeReceiptLines(
          created.id,
          draft.receipt_lines,
          persistedReceipts.current,
          signal,
          progress,
        );
      } catch {
        // The server event already exists. Retain its identity so retrying the
        // still-mounted editor updates it instead of creating a duplicate.
        if (!signal.aborted) setMedicalEvent(created);
        throw new Error("receipt synchronization failed");
      }
      signal.throwIfAborted();
      setMedicalEvent(created);
      setReceiptLines(savedLines);
      return created;
    }

    const updated = await updateMedicalEvent(
      medicalEvent.id,
      updateInput(medicalEvent, draft),
      signal,
    );
    signal.throwIfAborted();
    setMedicalEvent(updated);
    const savedLines = await synchronizeReceiptLines(
      updated.id,
      draft.receipt_lines,
      persistedReceipts.current,
      signal,
      progress,
    );
    signal.throwIfAborted();
    setReceiptLines(savedLines);
    return updated;
  }

  async function structure(draft: EventDraftView): Promise<void> {
    return perform(async (signal) => {
      const saved = await persistDraft(draft, signal);
      const accepted = await structureMedicalEvent(
        saved.id,
        saved.version,
        signal,
      );
      await waitForStructuring(accepted.status_url, signal);
      const structured = await getMedicalEvent(saved.id, signal);
      signal.throwIfAborted();
      setMedicalEvent(structured);
      setEditorRevision((current) => current + 1);
    });
  }

  async function analyze(draft: EventDraftView): Promise<void> {
    return perform(async (signal) => {
      const saved = await persistDraft(draft, signal);
      const result = await analyzeMedicalEvent(saved.id, signal);
      signal.throwIfAborted();
      window.location.assign(
        `/app/events/${encodeURIComponent(saved.id)}/result/${result.event_version}`,
      );
    });
  }

  if (sessionExpired)
    return (
      <main className={styles.composer}>
        <p role="alert">다시 로그인한 뒤 저장된 사건을 이어서 확인해 주세요.</p>
      </main>
    );

  return (
    <EventComposer
      key={`${initialEvent?.id ?? "new"}:${editorRevision}`}
      memberId={memberId}
      memberLabel={memberLabel ?? "대상 가족 이름을 확인하지 못했습니다"}
      initialEvent={medicalEvent}
      initialReceiptLines={receiptLines}
      mode={initialMode}
      onAnalyze={analyze}
      onStructure={structure}
      onSubmit={async (draft) => {
        await perform(async (signal) => {
          await persistDraft(draft, signal);
        });
      }}
    />
  );
}

function MissingMemberContext() {
  return (
    <main className={styles.composer} id="main-content">
      <header className={styles.heading}>
        <p className={styles.kicker}>Medical event</p>
        <h1>새 사건 기록</h1>
      </header>
      <p role="alert">
        사건을 기록할 가족 구성원을 먼저 보장 원장에서 선택해 주세요.
      </p>
      <a href="/app/ledger">보장 원장으로 이동</a>
    </main>
  );
}

export function NewEventPage({ memberId }: { memberId?: string }) {
  const search = new URLSearchParams(window.location.search);
  const selectedMemberId = memberId ?? search.get("member") ?? "";
  const initialMode =
    search.get("mode") === "post_treatment" ? "post_treatment" : "pre_visit";
  if (!selectedMemberId) return <MissingMemberContext />;
  return (
    <EventEditor
      key={selectedMemberId}
      initialMode={initialMode}
      memberId={selectedMemberId}
    />
  );
}

export function ExistingEventPage({ eventId }: { eventId: string }) {
  const [event, setEvent] = useState<MedicalEvent>();
  const [lines, setLines] = useState<ReceiptLineView[]>([]);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  const [sessionExpired, setSessionExpired] = useState(false);
  useEffect(
    () =>
      authStore.registerCacheClearer(() => {
        setEvent(undefined);
        setLines([]);
        setSessionExpired(true);
      }),
    [],
  );

  useEffect(() => {
    if (sessionExpired) return;
    const controller = new AbortController();
    setFailed(false);
    Promise.all([
      getMedicalEvent(eventId, controller.signal),
      listReceiptLines(eventId, controller.signal),
    ])
      .then(([loadedEvent, loadedLines]) => {
        if (controller.signal.aborted) return;
        setEvent(loadedEvent);
        setLines(loadedLines.map(receiptView));
      })
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setFailed(true);
        }
      });
    return () => controller.abort();
  }, [eventId, retry, sessionExpired]);

  if (sessionExpired)
    return (
      <main className={styles.composer}>
        <p role="alert">다시 로그인한 뒤 저장된 사건을 이어서 확인해 주세요.</p>
      </main>
    );

  if (failed) {
    return (
      <main className={styles.composer} id="main-content">
        <h1>사건 기록</h1>
        <p role="alert">저장된 사건을 불러오지 못했습니다.</p>
        <button type="button" onClick={() => setRetry((value) => value + 1)}>
          사건 다시 불러오기
        </button>
      </main>
    );
  }
  if (!event || event.id !== eventId) {
    return (
      <main className={styles.composer} id="main-content">
        <h1>사건 기록</h1>
        <p role="status">저장된 사건을 불러오는 중입니다.</p>
      </main>
    );
  }
  return (
    <EventEditor
      key={event.id}
      memberId={event.family_member_id}
      initialEvent={event}
      initialReceiptLines={lines}
    />
  );
}
