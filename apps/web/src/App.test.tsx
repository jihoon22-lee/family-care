import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

import { App } from "./App";
import { authStore } from "./features/identity/authStore";

beforeEach(() => {
  authStore.setAuthenticated({
    display_name: "Admin A",
    needs_reauthentication: false,
    user_id: "synthetic-user-a",
    username: "admin-a",
  });
  vi.stubGlobal(
    "fetch",
    vi.fn().mockRejectedValue(new Error("synthetic test")),
  );
});

afterEach(() => {
  authStore.clear();
  vi.unstubAllGlobals();
});

describe("FamilyCare foundation shell", () => {
  it("states the product boundary and ledger purpose", () => {
    render(<App />);

    expect(
      screen.getByRole("heading", { name: "우리 가족 보장 원장" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/보험금 지급을 보장하지 않습니다/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Evidence-bound ledger/)).toBeInTheDocument();
    expect(screen.getByText(/MATCH · UNKNOWN · NO_MATCH/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "청구 기록" })).toHaveAttribute(
      "href",
      "/app/claims",
    );
  });
});
