import { useCallback } from "react";

import {
  getBenefitCalculations,
  getEventResult,
  type BenefitCalculations,
  type EventResult,
} from "../../api/results";
import { useQueryCache, useResource } from "../../api/query-cache";

export function useEventResult(eventId: string, version: number) {
  const cache = useQueryCache();
  const key = `medical-event-result:${eventId}:${version}`;
  const resource = useResource<EventResult>(key, (signal) =>
    getEventResult(eventId, version, signal),
  );
  const reload = useCallback(() => {
    cache.invalidate(key);
  }, [cache, key]);
  return { ...resource, reload };
}

export function useBenefitCalculations(eventId: string) {
  const key = `medical-event-calculations:${eventId}`;
  return useResource<BenefitCalculations>(key, (signal) =>
    getBenefitCalculations(eventId, signal),
  );
}
