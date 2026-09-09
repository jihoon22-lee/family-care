import { useEffect, useId, useRef, useState } from "react";
import type { GuidanceReviewJob } from "../../api/generated";
import { ApiError } from "../../api/errors";
import {
  cancelGuidanceReview,
  createGuidanceReview,
  getGuidanceReview,
} from "../../api/guidance-reviews";
import { authStore } from "../identity/authStore";
import { GuidanceReviewResult } from "./GuidanceReviewResult";
import styles from "./GuidanceReviewPanel.module.css";

const stateCopy: Record<GuidanceReviewJob["state"], string> = {
  queued: "검수 순서를 기다리고 있습니다.",
  running: "가입 자료와 약관을 검수하고 있습니다.",
  partial: "일부 자료의 검수가 완료되었습니다.",
  completed: "검수가 완료되었습니다.",
  disagreement: "검수에서 서로 다른 해석이 발견되었습니다.",
  failed: "검수를 완료하지 못했습니다. 기존 로컬 안내는 계속 볼 수 있습니다.",
  cancelled: "검수를 취소했습니다.",
};
function active(job?: GuidanceReviewJob) {
  return job?.state === "queued" || job?.state === "running";
}
function belongsToResult(
  job: GuidanceReviewJob,
  eventId: string,
  eventVersion: number,
  decisionRunId: string,
) {
  return (
    !job.stale &&
    job.medical_event_id === eventId &&
    job.event_version === eventVersion &&
    job.decision_run_id === decisionRunId &&
    (!job.result ||
      (job.result.guidance.medical_event_id === eventId &&
        job.result.guidance.event_version === eventVersion))
  );
}
function failureCopy(error: unknown) {
  if (error instanceof ApiError && error.status === 409)
    return "사건이나 자료가 변경되었습니다. 현재 결과를 다시 확인한 뒤 검수해 주세요.";
  if (
    error instanceof ApiError &&
    (error.status === 503 || error.status === 429)
  )
    return "검수를 지금 시작할 수 없습니다. 설정이나 이용 한도를 확인한 뒤 다시 시도해 주세요.";
  return "검수 요청을 처리하지 못했습니다. 기존 로컬 안내는 계속 볼 수 있습니다.";
}

export function GuidanceReviewPanel({
  eventId,
  eventVersion,
  decisionRunId,
  disabled = false,
}: {
  eventId: string;
  eventVersion: number;
  decisionRunId: string;
  disabled?: boolean;
}) {
  const id = useId();
  const [job, setJob] = useState<GuidanceReviewJob>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [expired, setExpired] = useState(false);
  const [now, setNow] = useState(Date.now);
  const request = useRef<AbortController | null>(null);
  const epoch = useRef(0);
  const submitting = useRef(false);

  useEffect(() => {
    setJob(undefined);
    setError(undefined);
    setBusy(false);
    submitting.current = false;
    return () => {
      epoch.current += 1;
      request.current?.abort();
      request.current = null;
    };
  }, [eventId, eventVersion, decisionRunId, disabled]);

  useEffect(
    () =>
      authStore.registerCacheClearer(() => {
        epoch.current += 1;
        request.current?.abort();
        request.current = null;
        submitting.current = false;
        setJob(undefined);
        setError(undefined);
        setBusy(false);
        setExpired(true);
      }),
    [],
  );

  const running = active(job);
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  useEffect(() => {
    if (!job || !active(job) || disabled || expired || busy || error) return;
    const current = epoch.current;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      request.current = controller;
      void getGuidanceReview(job.id, controller.signal)
        .then((next) => {
          if (controller.signal.aborted || current !== epoch.current) return;
          if (
            !belongsToResult(next, eventId, eventVersion, decisionRunId) ||
            next.id !== job.id
          ) {
            setError(
              "현재 사건과 다른 검수 결과입니다. 다시 분석한 뒤 확인해 주세요.",
            );
            return;
          }
          setJob(next);
        })
        .catch((cause: unknown) => {
          if (!controller.signal.aborted && current === epoch.current)
            setError(failureCopy(cause));
        });
    }, 1500);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [
    job,
    eventId,
    eventVersion,
    decisionRunId,
    disabled,
    expired,
    busy,
    error,
  ]);

  async function submit(cancel = false) {
    if (submitting.current || disabled || expired || (cancel && !job)) return;
    submitting.current = true;
    setBusy(true);
    setError(undefined);
    epoch.current += 1;
    request.current?.abort();
    const current = epoch.current;
    const controller = new AbortController();
    request.current = controller;
    try {
      const next =
        cancel && job
          ? await cancelGuidanceReview(job.id, controller.signal)
          : await createGuidanceReview(
              eventId,
              {
                decision_run_id: decisionRunId,
                expected_event_version: eventVersion,
              },
              controller.signal,
            );
      if (controller.signal.aborted || current !== epoch.current) return;
      if (
        !belongsToResult(next, eventId, eventVersion, decisionRunId) ||
        (cancel && next.id !== job?.id)
      ) {
        setError(
          "현재 사건과 다른 검수 결과입니다. 다시 분석한 뒤 확인해 주세요.",
        );
        return;
      }
      setJob(next);
      setNow(Date.now());
    } catch (cause) {
      if (!controller.signal.aborted && current === epoch.current)
        setError(failureCopy(cause));
    } finally {
      if (current === epoch.current) {
        submitting.current = false;
        setBusy(false);
      }
    }
  }

  const elapsed = job
    ? Math.max(
        0,
        Math.floor(
          ((job.completed_at ? Date.parse(job.completed_at) : now) -
            Date.parse(job.created_at)) /
            1000,
        ),
      )
    : 0;
  return (
    <section className={styles.panel} aria-labelledby={`${id}-heading`}>
      <h2 id={`${id}-heading`}>AI 선택 검수</h2>
      <p>
        선택하면 사건 정보와 관련 보험 자료를 외부 AI에 보내 추가로 검수합니다.
        기존 로컬 안내와 검수 후 결과를 따로 확인할 수 있습니다.
      </p>
      {!running ? (
        <button
          type="button"
          disabled={disabled || expired || busy}
          onClick={() => void submit()}
        >
          AI 선택 검수 시작
        </button>
      ) : (
        <>
          <button
            type="button"
            disabled={busy || expired || disabled}
            onClick={() => void submit(true)}
          >
            검수 취소
          </button>
          <p>취소해도 이미 전송된 요청의 비용은 취소되지 않을 수 있습니다.</p>
        </>
      )}
      {disabled ? (
        <p>현재 사건 버전으로 다시 분석한 뒤 선택 검수를 시작할 수 있습니다.</p>
      ) : null}
      {expired ? (
        <p role="alert">다시 로그인한 뒤 검수를 시작해 주세요.</p>
      ) : null}
      {busy && !job ? <p role="status">검수 요청을 보내고 있습니다.</p> : null}
      {job ? (
        <>
          <p role="status">{stateCopy[job.state]}</p>
          <p>경과 {Number.isFinite(elapsed) ? elapsed : 0}초</p>
        </>
      ) : null}
      {error ? <p role="alert">{error}</p> : null}
      {error && running ? (
        <button
          type="button"
          disabled={busy || expired || disabled}
          onClick={() => setError(undefined)}
        >
          상태 다시 확인
        </button>
      ) : null}
      {job?.result ? <GuidanceReviewResult result={job.result} /> : null}
      {job?.usage ? (
        <details>
          <summary>검수 사용량</summary>
          <p>
            {job.usage.usage_complete && job.usage.total_tokens !== null
              ? `사용 토큰 ${job.usage.total_tokens.toLocaleString("ko-KR")}`
              : "전체 사용량은 아직 확인되지 않았습니다."}
          </p>
        </details>
      ) : null}
    </section>
  );
}
