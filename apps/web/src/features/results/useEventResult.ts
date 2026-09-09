import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getBenefitCalculations,
  getEventResult,
  type BenefitCalculations,
  type EventResult,
} from "../../api/results";
import { useQueryCache, useResource } from "../../api/query-cache";
import { authStore } from "../identity/authStore";

const ASSISTANCE_POLL_INTERVAL_MS = 1_000;
const MAX_ASSISTANCE_POLLS = 20;

export function useEventResult(eventId: string, version: number) {
  const cache = useQueryCache();
  const key = `medical-event-result:${eventId}:${version}`;
  const resource = useResource<EventResult>(key, (signal) =>
    getEventResult(eventId, version, signal),
  );
  const [polledAssistance, setPolledAssistance] = useState<{
    key: string;
    value: EventResult["assistance"];
  }>();
  const [pollCount, setPollCount] = useState(0);
  const pollRequest = useRef<AbortController | null>(null);
  const assistance =
    polledAssistance?.key === key
      ? polledAssistance.value
      : resource.data?.assistance;
  const data = useMemo(
    () =>
      resource.data && assistance
        ? { ...resource.data, assistance }
        : resource.data,
    [assistance, resource.data],
  );

  useEffect(() => {
    setPolledAssistance(undefined);
    setPollCount(0);
  }, [key]);
  useEffect(
    () =>
      authStore.registerCacheClearer(() => {
        pollRequest.current?.abort();
        setPolledAssistance(undefined);
        setPollCount(MAX_ASSISTANCE_POLLS);
      }),
    [],
  );

  useEffect(() => {
    if (
      !resource.data ||
      assistance?.state !== "LLM_PENDING" ||
      pollCount >= MAX_ASSISTANCE_POLLS
    ) {
      return;
    }
    let active = true;
    const controller = new AbortController();
    pollRequest.current = controller;
    const timeout = window.setTimeout(() => {
      void getEventResult(eventId, version, controller.signal)
        .then((nextResult) => {
          if (
            !active ||
            controller.signal.aborted ||
            nextResult.run_id !== resource.data?.run_id ||
            nextResult.medical_event_id !== eventId ||
            nextResult.event_version !== version
          )
            return;
          setPolledAssistance({ key, value: nextResult.assistance });
        })
        .catch(() => {
          // The already visible structured-search result remains usable.
        })
        .finally(() => {
          if (active) setPollCount((count) => count + 1);
        });
    }, ASSISTANCE_POLL_INTERVAL_MS);
    return () => {
      active = false;
      controller.abort();
      window.clearTimeout(timeout);
    };
  }, [assistance?.state, eventId, key, pollCount, resource.data, version]);

  const reload = useCallback(() => {
    setPollCount(0);
    setPolledAssistance(undefined);
    cache.invalidate(key);
  }, [cache, key]);
  return {
    ...resource,
    data,
    reload,
    pollingPaused:
      assistance?.state === "LLM_PENDING" && pollCount >= MAX_ASSISTANCE_POLLS,
  };
}

export function useBenefitCalculations(eventId: string) {
  const key = `medical-event-calculations:${eventId}`;
  return useResource<BenefitCalculations>(key, (signal) =>
    getBenefitCalculations(eventId, signal),
  );
}
