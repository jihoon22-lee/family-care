import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useSyncExternalStore,
  type PropsWithChildren,
} from "react";

import type { ApiError } from "./errors";

export interface ResourceSnapshot<T> {
  data: T | undefined;
  error: ApiError | undefined;
  loading: boolean;
}

type Loader<T> = (signal: AbortSignal) => Promise<T>;
type Listener = () => void;

interface Entry<T> {
  controller?: AbortController;
  listeners: Set<Listener>;
  loader?: Loader<T>;
  promise?: Promise<void>;
  snapshot: ResourceSnapshot<T>;
}

const EMPTY_SNAPSHOT: ResourceSnapshot<never> = Object.freeze({
  data: undefined,
  error: undefined,
  loading: false,
});

function isApiError(value: unknown): value is ApiError {
  return (
    value instanceof Error &&
    "code" in value &&
    "status" in value &&
    typeof value.code === "string" &&
    typeof value.status === "number"
  );
}

export class QueryCache {
  private readonly entries = new Map<string, Entry<unknown>>();

  private entry<T>(key: string): Entry<T> {
    let entry = this.entries.get(key) as Entry<T> | undefined;
    if (!entry) {
      entry = {
        listeners: new Set(),
        snapshot: EMPTY_SNAPSHOT as ResourceSnapshot<T>,
      };
      this.entries.set(key, entry as Entry<unknown>);
    }
    return entry;
  }

  subscribe = (key: string, listener: Listener): (() => void) => {
    const entry = this.entry(key);
    entry.listeners.add(listener);
    return () => entry.listeners.delete(listener);
  };

  snapshot = <T>(key: string): ResourceSnapshot<T> =>
    this.entry<T>(key).snapshot;

  load = <T>(key: string, loader: Loader<T>, force = false): Promise<void> => {
    const entry = this.entry<T>(key);
    entry.loader = loader;
    if (entry.promise && !force) return entry.promise;
    if (entry.snapshot.data !== undefined && !force) return Promise.resolve();
    entry.controller?.abort();
    const controller = new AbortController();
    entry.controller = controller;
    entry.snapshot = { ...entry.snapshot, error: undefined, loading: true };
    this.notify(entry);
    entry.promise = loader(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) {
          entry.snapshot = { data, error: undefined, loading: false };
          this.notify(entry);
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          entry.snapshot = {
            data: undefined,
            error: isApiError(error) ? error : undefined,
            loading: false,
          };
          this.notify(entry);
        }
      })
      .finally(() => {
        if (entry.controller === controller) {
          entry.promise = undefined;
          entry.controller = undefined;
        }
      });
    return entry.promise;
  };

  invalidate(prefix: string): void {
    for (const [key, untypedEntry] of this.entries) {
      if (!key.startsWith(prefix)) continue;
      const entry = untypedEntry as Entry<unknown>;
      if (entry.loader) void this.load(key, entry.loader, true);
    }
  }

  clear(): void {
    for (const entry of this.entries.values()) {
      entry.controller?.abort();
      entry.promise = undefined;
      entry.snapshot = EMPTY_SNAPSHOT;
      this.notify(entry);
    }
  }

  private notify(entry: Entry<unknown>): void {
    for (const listener of entry.listeners) listener();
  }
}

const QueryCacheContext = createContext<QueryCache | null>(null);

export function QueryCacheProvider({ children }: PropsWithChildren) {
  const cache = useMemo(() => new QueryCache(), []);
  useEffect(() => () => cache.clear(), [cache]);
  return createElement(QueryCacheContext.Provider, { value: cache }, children);
}

export function useQueryCache(): QueryCache {
  const cache = useContext(QueryCacheContext);
  if (!cache) throw new Error("FamilyCare query cache provider is missing");
  return cache;
}

export function useResource<T>(
  key: string,
  loader: Loader<T>,
): ResourceSnapshot<T> {
  const cache = useQueryCache();
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const stableLoader = useCallback(
    (signal: AbortSignal) => loaderRef.current(signal),
    [],
  );
  const subscribe = useCallback(
    (listener: Listener) => cache.subscribe(key, listener),
    [cache, key],
  );
  const getSnapshot = useCallback(() => cache.snapshot<T>(key), [cache, key]);
  useEffect(() => {
    void cache.load(key, stableLoader);
  }, [cache, key, stableLoader]);
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
