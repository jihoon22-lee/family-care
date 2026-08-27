import { useCallback } from "react";

import type { MemberInsuranceDocumentInventoryResponse } from "../../api/generated";
import { getInsuranceDocumentInventory } from "../../api/insurance-document-inventory";
import { useQueryCache, useResource } from "../../api/query-cache";

export function useInsuranceDocumentInventory(memberId: string | undefined) {
  const cache = useQueryCache();
  const key = `insurance-document-inventory:${memberId ?? "none"}`;
  const resource = useResource<
    MemberInsuranceDocumentInventoryResponse | undefined
  >(key, (signal) =>
    memberId
      ? getInsuranceDocumentInventory(memberId, signal)
      : Promise.resolve(undefined),
  );
  const reload = useCallback(() => {
    if (memberId) cache.invalidate(`insurance-document-inventory:${memberId}`);
  }, [cache, memberId]);
  return { ...resource, reload };
}
