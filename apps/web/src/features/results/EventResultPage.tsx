import { useEffect, useRef, useState } from "react";

import { getEvidence, getGuidanceEvidence } from "../../api/results";
import { createClaimCase } from "../../api/claims";
import { ApiError } from "../../api/errors";
import type {
  BenefitCalculationsResponse,
  CanonicalCoverageRef,
  ClaimCreateRequest,
} from "../../api/generated";
import { focusHeading } from "../../app/focus";
import { EvidenceDrawer } from "../../components/EvidenceDrawer";
import { useMedicalEvent } from "../events/useMedicalEvent";
import { authStore } from "../identity/authStore";
import { useBenefitCalculations, useEventResult } from "./useEventResult";
import { ActionFirstResult } from "./ActionFirstResult";
import { GuidanceReviewPanel } from "./GuidanceReviewPanel";
import { GuidanceQuestions } from "./GuidanceQuestions";
import { useEvidenceDisclosure } from "./useEvidenceDisclosure";
import { useFamilyMemberLabel } from "../ledger/useFamilyMemberLabel";
import type { GuidanceEvidenceReferences } from "./GuidanceEvidenceContext";
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
  const analysisRequestRef = useRef<AbortController | null>(null);
  const [reanalyzing, setReanalyzing] = useState(false);
  const [reanalyzeError, setReanalyzeError] = useState(false);
  const [claimStarting, setClaimStarting] = useState(false);
  const [claimStartError, setClaimStartError] = useState<"request" | "stale">();
  const [sessionExpired, setSessionExpired] = useState(false);
  const eventResource = useMedicalEvent(eventId);
  const resultResource = useEventResult(eventId, version);
  const calculationResource = useBenefitCalculations(eventId);
  const memberLabel = useFamilyMemberLabel(
    eventResource.data?.family_member_id,
  );
  const evidence = useEvidenceDisclosure(
    `${eventId}:${version}:${eventResource.data?.version}:${resultResource.data?.run_id}`,
  );

  useEffect(() => {
    claimStartingRef.current = false;
    setClaimStarting(false);
    setClaimStartError(undefined);
    analysisRequestRef.current = null;
    setReanalyzing(false);
    setReanalyzeError(false);
    return () => {
      claimRequestRef.current?.abort();
      claimRequestRef.current = null;
      analysisRequestRef.current?.abort();
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
        claimRequestRef.current?.abort();
        claimRequestRef.current = null;
        claimStartingRef.current = false;
        setClaimStarting(false);
        analysisRequestRef.current?.abort();
        setSessionExpired(true);
      }),
    [],
  );

  useEffect(() => {
    if (eventResource.data && resultResource.data) {
      focusHeading(headingRef.current);
    }
  }, [
    eventResource.data?.id,
    resultResource.data?.run_id,
    resultResource.data?.event_version,
  ]);

  if (sessionExpired) {
    return (
      <main className={styles.page}>
        <p className={styles.error} role="alert">
          로그인이 필요합니다. 다시 로그인한 뒤 청구 준비를 이어가세요.
        </p>
      </main>
    );
  }
  if (
    (eventResource.loading && !eventResource.data) ||
    (resultResource.loading && !resultResource.data)
  ) {
    return (
      <main className={styles.page}>
        <p className={styles.loading} role="status" aria-live="polite">
          사건 결과를 불러오는 중입니다.
        </p>
      </main>
    );
  }
  if (
    (eventResource.error && !eventResource.data) ||
    (resultResource.error && !resultResource.data)
  ) {
    return (
      <main className={styles.page}>
        <p className={styles.error} role="alert">
          사건 결과를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.
        </p>
        <button
          type="button"
          className={styles.secondaryButton}
          onClick={() => {
            eventResource.reload();
            resultResource.reload();
          }}
        >
          결과 다시 불러오기
        </button>
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
  const recovering = Boolean(eventResource.error || resultResource.error);
  const guidance = result.local_guidance;
  const guidanceMatchesCurrentEvent = Boolean(
    guidance &&
    !recovering &&
    resultMatchesCurrentEvent &&
    result.medical_event_id === eventId &&
    guidance.medical_event_id === eventId &&
    guidance.event_version === event.version,
  );
  const visibleCalculations = resultMatchesCurrentEvent
    ? (calculations ?? calculationResource.data)
    : undefined;
  const analyzeCurrent = async (signal?: AbortSignal) => {
    if (onReanalyze) {
      await onReanalyze();
      return;
    }
    const nextResult = await eventResource.analyze(signal);
    if (!signal?.aborted) {
      window.location.assign(
        `/app/events/${encodeURIComponent(eventId)}/result/${nextResult.event_version}`,
      );
    }
  };
  const reanalyze = () => {
    if (analysisRequestRef.current) return;
    const controller = new AbortController();
    analysisRequestRef.current = controller;
    setReanalyzing(true);
    setReanalyzeError(false);
    void analyzeCurrent(controller.signal)
      .catch(() => {
        if (!controller.signal.aborted) setReanalyzeError(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setReanalyzing(false);
          analysisRequestRef.current = null;
        }
      });
  };

  async function openEvidence(evidenceIds: string[]): Promise<void> {
    if (onOpenEvidence) {
      onOpenEvidence(evidenceIds);
      return;
    }
    evidence.show(
      evidenceIds.map((evidenceId) => ({
        key: evidenceId,
        load: (signal) => getEvidence(evidenceId, signal),
      })),
    );
  }
  function openGuidanceEvidence(
    coverage: CanonicalCoverageRef,
    refs: GuidanceEvidenceReferences,
    reviewJobId?: string,
  ) {
    evidence.show(
      refs.map((ref) => ({
        key: JSON.stringify(ref),
        load: (signal) =>
          getGuidanceEvidence(
            eventId,
            {
              decision_run_id: result.run_id,
              expected_event_version: result.event_version,
              coverage,
              evidence: ref,
              ...(reviewJobId ? { review_job_id: reviewJobId } : {}),
            },
            signal,
          ),
      })),
    );
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
    if (!resultMatchesCurrentEvent || result.stale || recovering) {
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
  const startReviewedClaim = (
    reviewJobId: string,
    coverage: CanonicalCoverageRef,
  ) => {
    if (
      !guidanceMatchesCurrentEvent ||
      claimStarting ||
      claimStartError === "stale"
    )
      return;
    submitClaim({
      guidance: {
        run_id: result.run_id,
        expected_event_version: result.event_version,
        coverage,
        review_job_id: reviewJobId,
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
            <dd>{memberLabel ?? "대상 가족 이름을 확인하지 못했습니다"}</dd>
          </div>
          <div>
            <dt>유형</dt>
            <dd>{event.mode === "post_treatment" ? "치료 후" : "방문 전"}</dd>
          </div>
          <div>
            <dt>사건 날짜</dt>
            <dd>{event.event_date ?? "미입력"}</dd>
          </div>
          <div>
            <dt>방문 날짜</dt>
            <dd>{event.visit_date ?? "미입력"}</dd>
          </div>
          <div>
            <dt>사건 버전</dt>
            <dd>{event.version}</dd>
          </div>
          <div>
            <dt>자료 확인 기준</dt>
            <dd>
              <time dateTime={result.policy_snapshot_at}>
                {new Date(result.policy_snapshot_at).toLocaleString("ko-KR")}
              </time>
            </dd>
          </div>
          <div>
            <dt>결과 기준 버전</dt>
            <dd>{result.event_version}</dd>
          </div>
        </dl>
        <p className={styles.situation}>{event.situation}</p>
        <a href={`/app/events/${encodeURIComponent(eventId)}`}>
          사건 정보 보완
        </a>
      </header>
      {recovering ? (
        <p role="alert" className={styles.error}>
          연결을 확인하지 못해 마지막으로 불러온 결과를 유지합니다.{" "}
          <button
            type="button"
            onClick={() => {
              eventResource.reload();
              resultResource.reload();
            }}
          >
            결과 다시 불러오기
          </button>
        </p>
      ) : null}
      {reanalyzing ? (
        <p role="status">기존 결과를 유지하며 다시 계산하고 있습니다.</p>
      ) : null}
      {reanalyzeError ? (
        <p role="alert" className={styles.error}>
          다시 계산하지 못했습니다. 기존 후보와 금액은 유지됩니다.{" "}
          <button type="button" onClick={reanalyze}>
            다시 계산
          </button>
        </p>
      ) : null}
      {resultResource.pollingPaused ? (
        <p role="status">
          추가 안내의 자동 상태 확인을 잠시 멈췄습니다.{" "}
          <button type="button" onClick={resultResource.reload}>
            추가 안내 상태 다시 확인
          </button>
        </p>
      ) : null}
      <ActionFirstResult
        calculations={visibleCalculations}
        onOpenEvidence={(evidenceIds) => {
          void openEvidence(evidenceIds);
        }}
        onReanalyze={reanalyze}
        onStartClaim={startClaim}
        onStartGuidanceClaim={startGuidanceClaim}
        onOpenGuidanceEvidence={openGuidanceEvidence}
        claimStarting={claimStarting}
        claimStartDisabled={
          !guidanceMatchesCurrentEvent || claimStartError === "stale"
        }
        result={result}
        riderLabels={riderLabels}
      />
      {guidance ? (
        <GuidanceQuestions
          event={event}
          questions={guidance.candidates.flatMap(
            (candidate) => candidate.questions ?? [],
          )}
          disabled={recovering || reanalyzing}
          onSave={eventResource.update}
          onAnalyze={analyzeCurrent}
        />
      ) : null}
      {guidance ? (
        <GuidanceReviewPanel
          key={`${eventId}:${event.version}:${result.run_id}`}
          eventId={eventId}
          eventVersion={event.version}
          decisionRunId={result.run_id}
          claimStarting={claimStarting}
          onStartClaim={startReviewedClaim}
          onOpenEvidence={(reviewJobId, coverage, refs) =>
            openGuidanceEvidence(coverage, refs, reviewJobId)
          }
          disabled={
            !guidanceMatchesCurrentEvent ||
            (result.local_guidance_stale ?? result.stale) ||
            claimStartError === "stale"
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
        evidence={evidence.items}
        onClose={evidence.close}
        open={evidence.open}
        loading={evidence.loading}
        totalCount={evidence.total}
        failedCount={evidence.failed}
        remainingCount={evidence.remaining}
        onRetry={evidence.retry}
        onShowMore={evidence.showMore}
      />
    </main>
  );
}
