import { useCallback, useEffect, useRef, useState } from "react";
import type { EvidenceDrawerItem } from "../../components/EvidenceDrawer";
import { authStore } from "../identity/authStore";

export interface EvidenceSource {
  key: string;
  load: (signal: AbortSignal) => Promise<EvidenceDrawerItem>;
}
interface Entry extends EvidenceSource {
  state: "pending" | "loading" | "ready" | "failed";
  item?: EvidenceDrawerItem;
}
const PAGE_SIZE = 16;

export function useEvidenceDisclosure(scopeKey: string) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const current = useRef<Entry[]>([]);
  const request = useRef<AbortController | null>(null);
  const epoch = useRef(0);
  const busy = useRef(false);
  const expired = useRef(false);

  const close = useCallback(() => {
    epoch.current += 1;
    request.current?.abort();
    request.current = null;
    busy.current = false;
    current.current = [];
    setEntries([]);
    setLoading(false);
    setOpen(false);
  }, []);
  useEffect(() => {
    close();
    return close;
  }, [scopeKey, close]);
  useEffect(
    () =>
      authStore.registerCacheClearer(() => {
        expired.current = true;
        close();
      }),
    [close],
  );

  async function load(indices: number[]) {
    if (busy.current || expired.current || !indices.length) return;
    const generation = epoch.current;
    const controller = request.current ?? new AbortController();
    request.current = controller;
    busy.current = true;
    setLoading(true);
    function update(index: number, patch: Partial<Entry>) {
      if (generation !== epoch.current || controller.signal.aborted) return;
      current.current = current.current.map((entry, position) =>
        position === index ? { ...entry, ...patch } : entry,
      );
      setEntries(current.current);
    }
    await Promise.allSettled(
      indices.map(async (index) => {
        const source = current.current[index];
        update(index, { state: "loading" });
        try {
          const item = await source.load(controller.signal);
          update(index, {
            item,
            state:
              "content_kind" in item && item.content_kind === "UNAVAILABLE"
                ? "failed"
                : "ready",
          });
        } catch {
          update(index, { state: "failed" });
        }
      }),
    );
    if (generation === epoch.current && !controller.signal.aborted) {
      busy.current = false;
      setLoading(false);
    }
  }

  function show(sources: EvidenceSource[]) {
    if (expired.current) return;
    close();
    current.current = [
      ...new Map(sources.map((source) => [source.key, source])).values(),
    ].map((source) => ({ ...source, state: "pending" }));
    setEntries(current.current);
    setOpen(true);
    void load(current.current.slice(0, PAGE_SIZE).map((_, index) => index));
  }
  function indices(state: Entry["state"]) {
    return current.current
      .flatMap((entry, index) => (entry.state === state ? [index] : []))
      .slice(0, PAGE_SIZE);
  }
  return {
    open,
    loading,
    close,
    show,
    total: entries.length,
    items: entries.flatMap((entry) => (entry.item ? [entry.item] : [])),
    failed: entries.filter((entry) => entry.state === "failed").length,
    remaining: entries.filter((entry) => entry.state === "pending").length,
    showMore: () => {
      void load(indices("pending"));
    },
    retry: () => {
      void load(indices("failed"));
    },
  };
}
