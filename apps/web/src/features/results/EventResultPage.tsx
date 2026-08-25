import { useEffect, useRef, useState } from "react";

import { getEvidence } from "../../api/results";
import type {
  BenefitCalculationsResponse,
  EvidenceDetailResponse,
} from "../../api/generated";
import { focusHeading } from "../../app/focus";
import { EvidenceDrawer } from "../../components/EvidenceDrawer";
import { useMedicalEvent } from "../events/useMedicalEvent";
import { useBenefitCalculations, useEventResult } from "./useEventResult";
import { ActionFirstResult } from "./ActionFirstResult";
import styles from "./Results.module.css";

export function EventResultPage({
  calculations,
  eventId,
  onOpenEvidence,
  onReanalyze,
  onStartClaim,
  riderLabels,
  version,
}: {
  calculations?: BenefitCalculationsResponse;
  eventId: string;
  onOpenEvidence?: (evidenceIds: string[]) => void;
  onReanalyze?: () => void | Promise<void>;
  onStartClaim?: (riderId: string) => void;
  riderLabels?: Record<string, string>;
  version: number;
}) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [evidence, setEvidence] = useState<EvidenceDetailResponse[]>([]);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [evidenceUnavailable, setEvidenceUnavailable] = useState(false);
  const eventResource = useMedicalEvent(eventId);
  const resultResource = useEventResult(eventId, version);
  const calculationResource = useBenefitCalculations(eventId);

  useEffect(() => {
    if (eventResource.data && resultResource.data) {
      focusHeading(headingRef.current);
    }
  }, [eventResource.data, resultResource.data]);

  if (eventResource.loading || resultResource.loading) {
    return (
      <main className={styles.page}>
        <p className={styles.loading} role="status" aria-live="polite">
          사건 결과를 불러오는 중입니다.
        </p>
      </main>
    );
  }
  if (eventResource.error || resultResource.error) {
    return (
      <main className={styles.page}>
        <p className={styles.error} role="alert">
          사건 결과를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.
        </p>
      </main>
    );
  }
  if (!eventResource.data || !resultResource.data) {
    return (
      <main className={styles.page}>
        <p className={styles.loading} role="status">
          사건 결과를 준비하는 중입니다.
        </p>
      </main>
    );
  }

  const event = eventResource.data;
  const result = resultResource.data;
  const reanalyze = () => {
    if (onReanalyze) {
      void onReanalyze();
      return;
    }
    void eventResource.analyze().then((nextResult) => {
      window.location.assign(
        `/app/events/${encodeURIComponent(eventId)}/result/${nextResult.event_version}`,
      );
    });
  };

  async function openEvidence(evidenceIds: string[]): Promise<void> {
    if (onOpenEvidence) {
      onOpenEvidence(evidenceIds);
      return;
    }
    setEvidenceUnavailable(false);
    try {
      const items = await Promise.all(
        evidenceIds.slice(0, 16).map((evidenceId) => getEvidence(evidenceId)),
      );
      setEvidence(items);
    } catch {
      setEvidence([]);
      setEvidenceUnavailable(true);
    }
    setEvidenceOpen(true);
  }

  const startClaim =
    onStartClaim ??
    ((riderId: string) => {
      window.location.assign(
        `/app/claims/new?event=${encodeURIComponent(eventId)}&rider=${encodeURIComponent(riderId)}`,
      );
    });

  return (
    <main className={styles.page} id="main-content">
      <header className={styles.pageHeading}>
        <p className={styles.kicker}>Medical event / result</p>
        <h1 ref={headingRef} tabIndex={-1}>
          현재 사건
        </h1>
        <dl className={styles.eventSummary}>
          <div>
            <dt>대상</dt>
            <dd>선택한 가족 구성원</dd>
          </div>
          <div>
            <dt>유형</dt>
            <dd>{event.mode === "post_treatment" ? "치료 후" : "방문 전"}</dd>
          </div>
          <div>
            <dt>사건 버전</dt>
            <dd>{event.version}</dd>
          </div>
        </dl>
        <p className={styles.situation}>{event.situation}</p>
      </header>
      <ActionFirstResult
        calculations={calculations ?? calculationResource.data}
        onOpenEvidence={(evidenceIds) => {
          void openEvidence(evidenceIds);
        }}
        onReanalyze={reanalyze}
        onStartClaim={startClaim}
        result={result}
        riderLabels={riderLabels}
      />
      {calculationResource.error && !calculations ? (
        <p className={styles.error} role="status">
          예상액 상세를 불러오지 못했습니다. 보장 판정 결과는 계속 확인할 수
          있습니다.
        </p>
      ) : null}
      <EvidenceDrawer
        evidence={evidence}
        onClose={() => setEvidenceOpen(false)}
        open={evidenceOpen}
        unavailable={evidenceUnavailable}
      />
    </main>
  );
}
