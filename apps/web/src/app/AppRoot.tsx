import type { PropsWithChildren } from "react";

import { QueryCacheProvider } from "../api/query-cache";

export function AppRoot({ children }: PropsWithChildren) {
  return <QueryCacheProvider>{children}</QueryCacheProvider>;
}
