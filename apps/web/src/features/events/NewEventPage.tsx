import { useEffect, useState } from "react";

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
): Promise<ReceiptLineView[]> {
  const retainedIds = new Set(
    draftLines.flatMap((line) => (line.id ? [line.id] : [])),
  );
  for (const current of currentLines) {
    if (current.id && current.version && !retainedIds.has(current.id)) {
      await deleteReceiptLine(eventId, current.id, current.version);
    }
  }

  const synchronized: ReceiptLineView[] = [];
  for (const line of draftLines) {
    const saved =
      line.id && line.version
        ? await updateReceiptLine(eventId, line.id, {
            ...createReceiptInput(line),
            expected_version: line.version,
          })
        : await createReceiptLine(eventId, createReceiptInput(line));
    synchronized.push(receiptView(saved));
  }
  return synchronized;
}

function updateInput(event: MedicalEvent, draft: EventDraftView) {
  return {
    event_date: draft.event_date,
    expected_version: event.version,
    mode: draft.mode,
    situation: draft.situation,
    structured_facts: draft.facts.map((fact) => ({
      field_id: fact.field_id,
      value: fact.value,
    })),
    visit_date: draft.visit_date,
  };
}

function waitForNextPoll(): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, STRUCTURING_POLL_INTERVAL_MS);
  });
}

function structuringFailed(job: StructuringJobResponse): boolean {
  return ["retryable_failed", "permanently_failed", "cancelled"].includes(
    job.state,
  );
}

async function waitForStructuring(statusUrl: string) {
  for (let attempt = 0; attempt < STRUCTURING_POLL_LIMIT; attempt += 1) {
    const job = await getStructuringJob(statusUrl);
    if (job.state === "succeeded") return job;
    if (structuringFailed(job)) throw new Error("structuring failed");
    await waitForNextPoll();
  }
  throw new Error("structuring timed out");
}

function EventEditor({
  memberId,
  initialEvent,
  initialReceiptLines = [],
}: {
  memberId: string;
  initialEvent?: MedicalEvent;
  initialReceiptLines?: ReceiptLineView[];
}) {
  const [medicalEvent, setMedicalEvent] = useState(initialEvent);
  const [receiptLines, setReceiptLines] = useState(initialReceiptLines);

  async function persistDraft(draft: EventDraftView): Promise<MedicalEvent> {
    if (!medicalEvent) {
      const created = await createMedicalEvent({
        event_date: draft.event_date,
        facts: {},
        family_member_id: draft.family_member_id,
        mode: draft.mode,
        situation: draft.situation,
        visit_date: draft.visit_date,
      });
      const savedLines = await synchronizeReceiptLines(
        created.id,
        draft.receipt_lines,
        [],
      );
      setMedicalEvent(created);
      setReceiptLines(savedLines);
      return created;
    }

    const updated = await updateMedicalEvent(
      medicalEvent.id,
      updateInput(medicalEvent, draft),
    );
    const savedLines = await synchronizeReceiptLines(
      updated.id,
      draft.receipt_lines,
      receiptLines,
    );
    setMedicalEvent(updated);
    setReceiptLines(savedLines);
    return updated;
  }

  async function structure(draft: EventDraftView): Promise<void> {
    const saved = await persistDraft(draft);
    const accepted = await structureMedicalEvent(saved.id, saved.version);
    await waitForStructuring(accepted.status_url);
    const structured = await getMedicalEvent(saved.id);
    setMedicalEvent(structured);
  }

  async function analyze(draft: EventDraftView): Promise<void> {
    const saved = await persistDraft(draft);
    const result = await analyzeMedicalEvent(saved.id);
    window.location.assign(
      `/app/events/${encodeURIComponent(saved.id)}/result/${result.event_version}`,
    );
  }

  return (
    <EventComposer
      key={medicalEvent ? `${medicalEvent.id}:${medicalEvent.version}` : "new"}
      memberId={memberId}
      initialEvent={medicalEvent}
      initialReceiptLines={receiptLines}
      onAnalyze={analyze}
      onStructure={structure}
      onSubmit={async (draft) => {
        await persistDraft(draft);
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
  const selectedMemberId =
    memberId ?? new URLSearchParams(window.location.search).get("member") ?? "";
  if (!selectedMemberId) return <MissingMemberContext />;
  return <EventEditor memberId={selectedMemberId} />;
}

export function ExistingEventPage({ eventId }: { eventId: string }) {
  const [event, setEvent] = useState<MedicalEvent>();
  const [lines, setLines] = useState<ReceiptLineView[]>([]);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      getMedicalEvent(eventId, controller.signal),
      listReceiptLines(eventId, controller.signal),
    ])
      .then(([loadedEvent, loadedLines]) => {
        setEvent(loadedEvent);
        setLines(loadedLines.map(receiptView));
      })
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setFailed(true);
        }
      });
    return () => controller.abort();
  }, [eventId]);

  if (failed) {
    return (
      <main className={styles.composer} id="main-content">
        <h1>사건 기록</h1>
        <p role="alert">저장된 사건을 불러오지 못했습니다.</p>
      </main>
    );
  }
  if (!event) {
    return (
      <main className={styles.composer} id="main-content">
        <h1>사건 기록</h1>
        <p role="status">저장된 사건을 불러오는 중입니다.</p>
      </main>
    );
  }
  return (
    <EventEditor
      memberId={event.family_member_id}
      initialEvent={event}
      initialReceiptLines={lines}
    />
  );
}
