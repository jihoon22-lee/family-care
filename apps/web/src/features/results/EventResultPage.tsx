import { useEffect, useRef, useState } from "react";

import { getEvidence } from "../../api/results";
import { createClaimCase } from "../../api/claims";
import { ApiError } from "../../api/errors";
import type {
  BenefitCalculationsResponse,
  CanonicalCoverageRef,
  ClaimCreateRequest,
  EvidenceDetailResponse,
} from "../../api/generated";
import { focusHeading } from "../../app/focus";
import { EvidenceDrawer } from "../../components/EvidenceDrawer";
import { useMedicalEvent } from "../events/useMedicalEvent";
import { authStore } from "../identity/authStore";
import { useBenefitCalculations, useEventResult } from "./useEventResult";
import { ActionFirstResult } from "./ActionFirstResult";
import { GuidanceReviewPanel } from "./GuidanceReviewPanel";
import styles from "./Results.module.css";

export function EventResultPage({
  calculations,
  eventId,
  onOpenEvidence,
  onReanalyze,
  onStartClaim,
  onOpenClaim,
  riderLabels,
  version,
}: {
  calculations?: BenefitCalculationsResponse;
  eventId: string;
  onOpenEvidence?: (evidenceIds: string[]) => void;
  onReanalyze?: () => void | Promise<void>;
  onStartClaim?: (riderId: string) => void;
  onOpenClaim?: (claimId: string) => void;
  riderLabels?: Record<string, string>;
  version: number;
}) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const claimStartingRef = useRef(false);
  const claimRequestRef = useRef<AbortController | null>(null);
  const pageEpoch = useRef(0);
  const [evidence, setEvidence] = useState<EvidenceDetailResponse[]>([]);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [evidenceUnavailable, setEvidenceUnavailable] = useState(false);
  const [claimStarting, setClaimStarting] = useState(false);
  const [claimStartError, setClaimStartError] = useState<"request" | "stale">();
  const [sessionExpired, setSessionExpired] = useState(false);
  const eventResource = useMedicalEvent(eventId);
  const resultResource = useEventResult(eventId, version);
  const calculationResource = useBenefitCalculations(eventId);

  useEffect(() => {
    claimStartingRef.current = false;
    setClaimStarting(false);
    setClaimStartError(undefined);
    setEvidence([]);
    setEvidenceOpen(false);
    return () => {
      pageEpoch.current += 1;
      claimRequestRef.current?.abort();
      claimRequestRef.current = null;
    };
  }, [
    eventId,
    version,
    eventResource.data?.version,
    resultResource.data?.run_id,
  ]);

  useEffect(
    () =>
      authStore.registerCacheClearer(() => {
        pageEpoch.current += 1;
        claimRequestRef.current?.abort();
        claimRequestRef.current = null;
        claimStartingRef.current = false;
        setClaimStarting(false);
        setEvidence([]);
        setEvidenceOpen(false);
        setSessionExpired(true);
      }),
    [],
  );

  useEffect(() => {
    if (eventResource.data && resultResource.data) {
      focusHeading(headingRef.current);
    }
  }, [eventResource.data, resultResource.data]);

  if (sessionExpired) {
    return (
      <main className={styles.page}>
        <p className={styles.error} role="alert">
          로그인이 필요합니다. 다시 로그인한 뒤 청구 준비를 이어가세요.
        </p>
      </main>
    );
  }
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
  const resultMatchesCurrentEvent = event.version === result.event_version;
  const guidance = result.local_guidance;
  const guidanceMatchesCurrentEvent = Boolean(
    guidance &&
    resultMatchesCurrentEvent &&
    result.medical_event_id === eventId &&
    guidance.medical_event_id === eventId &&
    guidance.event_version === event.version,
  );
  const visibleCalculations = resultMatchesCurrentEvent
    ? (calculations ?? calculationResource.data)
    : undefined;
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
    const current = pageEpoch.current;
    try {
      const items = await Promise.all(
        evidenceIds.slice(0, 16).map((evidenceId) => getEvidence(evidenceId)),
      );
      if (current !== pageEpoch.current) return;
      setEvidence(items);
    } catch {
      if (current !== pageEpoch.current) return;
      setEvidence([]);
      setEvidenceUnavailable(true);
    }
    setEvidenceOpen(true);
  }

  const submitClaim = (input: ClaimCreateRequest) => {
    if (claimStartingRef.current) return;
    const controller = new AbortController();
    claimRequestRef.current = controller;
    claimStartingRef.current = true;
    setClaimStarting(true);
    setClaimStartError(undefined);
    void createClaimCase(eventId, input, controller.signal)
      .then((claim) => {
        if (controller.signal.aborted || claimRequestRef.current !== controller)
          return;
        if (onOpenClaim) onOpenClaim(claim.id);
        else
          window.location.assign(`/app/claims/${encodeURIComponent(claim.id)}`);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || claimRequestRef.current !== controller)
          return;
        claimStartingRef.current = false;
        setClaimStarting(false);
        setClaimStartError(
          error instanceof ApiError && error.status === 409
            ? "stale"
            : "request",
        );
      });
  };

  const startClaim = (riderId: string) => {
    if (!resultMatchesCurrentEvent || result.stale) {
      setClaimStartError("stale");
      return;
    }
    if (claimStartingRef.current) return;
    if (onStartClaim) onStartClaim(riderId);
    else submitClaim({ rider_id: riderId });
  };

  const startGuidanceClaim = (coverage: CanonicalCoverageRef) => {
    if (
      !guidance ||
      !guidanceMatchesCurrentEvent ||
      (result.local_guidance_stale ?? result.stale)
    ) {
      setClaimStartError("stale");
      return;
    }
    submitClaim({
      guidance: {
        run_id: result.run_id,
        expected_event_version: guidance.event_version,
        coverage,
      },
    });
  };

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
        calculations={visibleCalculations}
        onOpenEvidence={(evidenceIds) => {
          void openEvidence(evidenceIds);
        }}
        onReanalyze={reanalyze}
        onStartClaim={startClaim}
        onStartGuidanceClaim={startGuidanceClaim}
        claimStarting={claimStarting}
        claimStartDisabled={
          !guidanceMatchesCurrentEvent || claimStartError === "stale"
        }
        result={result}
        riderLabels={riderLabels}
      />
      {guidance ? (
        <GuidanceReviewPanel
          key={`${eventId}:${event.version}:${result.run_id}`}
          eventId={eventId}
          eventVersion={event.version}
          decisionRunId={result.run_id}
          disabled={
            !guidanceMatchesCurrentEvent ||
            (result.local_guidance_stale ?? result.stale)
          }
        />
      ) : null}
      {claimStartError ? (
        <p className={styles.error} role="alert">
          {claimStartError === "stale"
            ? "현재 사건과 같은 버전의 결과에서만 청구 기록을 시작할 수 있습니다. 다시 분석해 주세요."
            : "청구 기록을 시작하지 못했습니다. 잠시 후 다시 시도해 주세요."}
        </p>
      ) : null}
      {claimStarting ? (
        <p className={styles.loading} role="status" aria-live="polite">
          청구 기록을 준비하는 중입니다.
        </p>
      ) : null}
      {!resultMatchesCurrentEvent ? (
        <p className={styles.error} role="status">
          현재 사건 버전과 다른 결과이므로 예상액 상세를 함께 표시하지 않습니다.
          다시 분석한 뒤 확인해 주세요.
        </p>
      ) : null}
      {resultMatchesCurrentEvent &&
      calculationResource.error &&
      !calculations ? (
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
