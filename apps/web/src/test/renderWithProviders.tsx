import type { ReactElement, PropsWithChildren } from "react";
import {
  render,
  type RenderOptions,
  type RenderResult,
} from "@testing-library/react";

import { AppRoot } from "../app/AppRoot";

function TestProviders({ children }: PropsWithChildren) {
  return <AppRoot>{children}</AppRoot>;
}

export function renderWithProviders(
  ui: ReactElement,
  options?: Omit<RenderOptions, "wrapper">,
): RenderResult {
  return render(ui, { wrapper: TestProviders, ...options });
}
