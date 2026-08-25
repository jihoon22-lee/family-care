import { useEffect, type PropsWithChildren } from "react";

import { QueryCacheProvider, useQueryCache } from "../api/query-cache";
import { authStore } from "../features/identity/authStore";

function AuthCacheBoundary({ children }: PropsWithChildren) {
  const cache = useQueryCache();

  useEffect(() => authStore.registerCacheClearer(() => cache.clear()), [cache]);
  return <>{children}</>;
}

export function AppRoot({ children }: PropsWithChildren) {
  return (
    <QueryCacheProvider>
      <AuthCacheBoundary>{children}</AuthCacheBoundary>
    </QueryCacheProvider>
  );
}
