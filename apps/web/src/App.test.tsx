import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

import { App } from "./App";
import { authStore } from "./features/identity/authStore";

beforeEach(() => {
  window.history.replaceState(null, "", "/app/ledger");
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
  window.history.replaceState(null, "", "/");
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

  it("returns to login when a business request reports an expired session", async () => {
    const privateDetail = "synthetic private upstream detail";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              error_code: "AUTHENTICATION_REQUIRED",
              message: privateDetail,
            }),
            { headers: { "Content-Type": "application/json" }, status: 401 },
          ),
        ),
      ),
    );

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "로그인" }),
    ).toBeInTheDocument();
    expect(window.location.pathname).toBe("/login");
    expect(document.body).not.toHaveTextContent(privateDetail);
  });
});
