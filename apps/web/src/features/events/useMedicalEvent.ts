import { useCallback } from "react";

import {
  analyzeMedicalEvent,
  getMedicalEvent,
  getStructuringJob,
  structureMedicalEvent,
  updateMedicalEvent,
  type MedicalEvent,
  type UpdateMedicalEventRequest,
} from "../../api/events";
import type {
  StructureAcceptedResponse,
  StructuringJobResponse,
} from "../../api/generated";
import { useQueryCache, useResource } from "../../api/query-cache";

export function useMedicalEvent(eventId: string) {
  const cache = useQueryCache();
  const key = `medical-event:${eventId}`;
  const resource = useResource<MedicalEvent>(key, (signal) =>
    getMedicalEvent(eventId, signal),
  );

  const reload = useCallback(() => {
    cache.invalidate(key);
  }, [cache, key]);

  const update = useCallback(
    async (input: UpdateMedicalEventRequest) => {
      const event = await updateMedicalEvent(eventId, input);
      cache.invalidate(key);
      cache.invalidate(`medical-event-result:${eventId}:`);
      return event;
    },
    [cache, eventId, key],
  );

  const structure = useCallback(
    (expectedVersion: number): Promise<StructureAcceptedResponse> =>
      structureMedicalEvent(eventId, expectedVersion),
    [eventId],
  );

  const loadStructuringJob = useCallback(
    (
      statusUrl: string,
      signal?: AbortSignal,
    ): Promise<StructuringJobResponse> => getStructuringJob(statusUrl, signal),
    [],
  );

  const analyze = useCallback(async () => {
    const result = await analyzeMedicalEvent(eventId);
    cache.invalidate(key);
    cache.invalidate(`medical-event-result:${eventId}:`);
    return result;
  }, [cache, eventId, key]);

  return {
    ...resource,
    analyze,
    loadStructuringJob,
    reload,
    structure,
    update,
  };
}
