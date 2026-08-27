import { useCallback } from "react";

import type {
  FamilyMemberResponse,
  PolicyResponse,
  PolicyReviewItem,
  RiderResponse,
} from "../../api/generated";
import {
  listFamilyMembers,
  listPolicies,
  listPolicyRiders,
  listReviewItems,
} from "../../api/ledger";
import { useQueryCache, useResource } from "../../api/query-cache";

export interface PolicyLedgerEntry {
  policy: PolicyResponse;
  riders: RiderResponse[];
}

export interface LedgerSnapshot {
  familyMembers: FamilyMemberResponse[];
  selectedMember: FamilyMemberResponse | undefined;
  policies: PolicyLedgerEntry[];
  reviewItems: PolicyReviewItem[];
}

async function loadLedger(
  memberId: string | undefined,
  signal: AbortSignal,
): Promise<LedgerSnapshot> {
  const familyMembers = await listFamilyMembers(signal);
  const selectedMember =
    familyMembers.find((member) => member.id === memberId) ?? familyMembers[0];
  if (!selectedMember) {
    return {
      familyMembers,
      selectedMember: undefined,
      policies: [],
      reviewItems: [],
    };
  }
  const [allPolicies, reviewItems] = await Promise.all([
    listPolicies(signal),
    listReviewItems(selectedMember.id, signal),
  ]);
  const relevantPolicies = allPolicies.filter(
    (policy) =>
      policy.parties.length === 0 ||
      policy.parties.some(
        (party) => party.family_member_id === selectedMember.id,
      ),
  );
  const riders = await Promise.all(
    relevantPolicies.map((policy) => listPolicyRiders(policy.id, signal)),
  );
  return {
    familyMembers,
    selectedMember,
    policies: relevantPolicies.map((policy, index) => ({
      policy,
      riders: riders[index],
    })),
    reviewItems: reviewItems.filter((item) => item.status === "NEEDS_REVIEW"),
  };
}

export function useLedger(memberId: string | undefined) {
  const cache = useQueryCache();
  const key = `ledger:${memberId ?? "first"}`;
  const resource = useResource(key, (signal) => loadLedger(memberId, signal));
  const reload = useCallback(() => cache.invalidate("ledger:"), [cache]);
  return { ...resource, reload };
}
