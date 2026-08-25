import { useSyncExternalStore } from "react";

import type { ApiError } from "../../api/errors";
import type { AuthUserResponse } from "../../api/generated";

export interface AuthUser extends AuthUserResponse {
  needs_reauthentication: boolean;
}

export type AuthStatus =
  "unknown" | "loading" | "authenticated" | "unauthenticated" | "error";

export interface AuthSnapshot {
  status: AuthStatus;
  user: AuthUser | null;
  error: ApiError | null;
}

type Listener = () => void;
type CacheClearer = () => void;

const INITIAL_SNAPSHOT: AuthSnapshot = Object.freeze({
  status: "unknown",
  user: null,
  error: null,
});

let snapshot: AuthSnapshot = INITIAL_SNAPSHOT;
const listeners = new Set<Listener>();
const cacheClearers = new Set<CacheClearer>();

function notify(): void {
  for (const listener of listeners) listener();
}

function setSnapshot(next: AuthSnapshot): void {
  snapshot = next;
  notify();
}

export const authStore = {
  getSnapshot(): AuthSnapshot {
    return snapshot;
  },

  subscribe(listener: Listener): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },

  setLoading(): void {
    setSnapshot({ status: "loading", user: null, error: null });
  },

  setAuthenticated(user: AuthUser): void {
    setSnapshot({ status: "authenticated", user, error: null });
  },

  setError(error: ApiError): void {
    setSnapshot({ status: "error", user: null, error });
  },

  clear(): void {
    // Query and screen state are memory-only and must be discarded together
    // with the server session. No credential is retained by this store.
    for (const clearCache of cacheClearers) clearCache();
    setSnapshot({ status: "unauthenticated", user: null, error: null });
  },

  registerCacheClearer(clearCache: CacheClearer): () => void {
    cacheClearers.add(clearCache);
    return () => cacheClearers.delete(clearCache);
  },
};

export function useAuthStore(): AuthSnapshot {
  return useSyncExternalStore(
    authStore.subscribe,
    authStore.getSnapshot,
    authStore.getSnapshot,
  );
}

export function resetAuthStore(): void {
  authStore.clear();
}
