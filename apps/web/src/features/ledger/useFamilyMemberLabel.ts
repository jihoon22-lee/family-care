import { useEffect, useRef, useState } from "react";
import { listFamilyMembers } from "../../api/ledger";
import { authStore } from "../identity/authStore";

/** Resolve the requested member only; a failed lookup never selects another person. */
export function useFamilyMemberLabel(memberId: string | undefined) {
  const [member, setMember] = useState<{ id: string; label: string }>();
  const request = useRef<AbortController | null>(null);
  const expired = useRef(false);
  useEffect(
    () =>
      authStore.registerCacheClearer(() => {
        expired.current = true;
        request.current?.abort();
        setMember(undefined);
      }),
    [],
  );
  useEffect(() => {
    if (!memberId || expired.current) return;
    const controller = new AbortController();
    request.current = controller;
    void listFamilyMembers(controller.signal)
      .then((members) => {
        if (controller.signal.aborted || expired.current) return;
        const selected = Array.isArray(members)
          ? members.find((item) => item.id === memberId && !item.deleted)
          : undefined;
        setMember(
          selected ? { id: memberId, label: selected.display_name } : undefined,
        );
      })
      .catch(() => {
        if (!controller.signal.aborted) setMember(undefined);
      });
    return () => controller.abort();
  }, [memberId]);
  return member && member.id === memberId ? member.label : undefined;
}
