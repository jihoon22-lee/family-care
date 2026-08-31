import type { KnowledgeContractPageResponse } from "../../api/generated";
import { getPrivateInsuranceCatalog } from "../../api/private-insurance-catalog";
import { useResource } from "../../api/query-cache";

export function usePrivateInsuranceCatalog(memberId: string | undefined) {
  const key = `private-insurance-catalog:${memberId ?? "none"}`;
  return useResource<KnowledgeContractPageResponse | undefined>(
    key,
    (signal) =>
      memberId
        ? getPrivateInsuranceCatalog(memberId, signal)
        : Promise.resolve(undefined),
  );
}
