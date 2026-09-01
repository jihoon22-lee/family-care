import { useCallback, useEffect } from "react";

import type { MemberInsuranceReconciliationResponse } from "../../api/generated";
import { getInsuranceReconciliation } from "../../api/insurance-reconciliation";
import { useQueryCache, useResource } from "../../api/query-cache";

export function useInsuranceReconciliation(memberId: string | undefined) {
  const cache = useQueryCache();
  const key = `insurance-reconciliation:${memberId ?? "none"}`;
  const resource = useResource<
    MemberInsuranceReconciliationResponse | undefined
  >(key, (signal) =>
    memberId
      ? getInsuranceReconciliation(memberId, signal)
      : Promise.resolve(undefined),
  );
  const reload = useCallback(() => {
    if (!memberId) return;
    cache.invalidate(`insurance-reconciliation:${memberId}`);
    cache.invalidate(`insurance-document-inventory:${memberId}`);
  }, [cache, memberId]);
  useEffect(() => {
    if (!memberId) return;
    const revalidate = () => cache.invalidate(key);
    window.addEventListener("focus", revalidate);
    return () => window.removeEventListener("focus", revalidate);
  }, [cache, key, memberId]);
  return { ...resource, reload };
}
