import { useCallback, useEffect, useMemo, useState } from "react";

import {
  getBenefitCalculations,
  getEventResult,
  type BenefitCalculations,
  type EventResult,
} from "../../api/results";
import { useQueryCache, useResource } from "../../api/query-cache";

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
    const timeout = window.setTimeout(() => {
      void getEventResult(eventId, version, controller.signal)
        .then((nextResult) => {
          if (!active) return;
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
    cache.invalidate(key);
  }, [cache, key]);
  return { ...resource, data, reload };
}

export function useBenefitCalculations(eventId: string) {
  const key = `medical-event-calculations:${eventId}`;
  return useResource<BenefitCalculations>(key, (signal) =>
    getBenefitCalculations(eventId, signal),
  );
}
