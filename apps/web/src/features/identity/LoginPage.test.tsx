import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { clearAuthState } from "./authApi";
import { LoginPage } from "./LoginPage";

const CURRENT_USER = {
  display_name: "Admin A",
  needs_reauthentication: false,
  user_id: "synthetic-user-a",
  username: "admin-a",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "application/json",
    },
    status,
  });
}

describe("LoginPage", () => {
  afterEach(() => {
    clearAuthState();
    vi.unstubAllGlobals();
  });

  it("submits credentials through the cookie session boundary and clears the password", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(CURRENT_USER))
      .mockResolvedValueOnce(jsonResponse({ csrf_token: "synthetic-csrf-a" }));
    vi.stubGlobal("fetch", fetchMock);
    const onAuthenticated = vi.fn();
    const user = userEvent.setup();

    render(<LoginPage onAuthenticated={onAuthenticated} />);
    await user.type(screen.getByLabelText("사용자 이름"), "admin-a");
    await user.type(
      screen.getByLabelText("비밀번호"),
      "synthetic-auth-secret-a",
    );
    await user.click(screen.getByRole("button", { name: "로그인" }));

    await waitFor(() =>
      expect(onAuthenticated).toHaveBeenCalledWith(CURRENT_USER),
    );
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/auth/login");
    expect(screen.getByLabelText("비밀번호")).toHaveValue("");
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(document.cookie).toBe("");
  });

  it("shows a stable failure message and clears the password after a failed attempt", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse({ error_code: "AUTHENTICATION_REQUIRED" }, 401),
        ),
    );
    const user = userEvent.setup();

    render(<LoginPage />);
    await user.type(screen.getByLabelText("사용자 이름"), "admin-a");
    await user.type(
      screen.getByLabelText("비밀번호"),
      "synthetic-auth-secret-a",
    );
    await user.click(screen.getByRole("button", { name: "로그인" }));

    expect(
      await screen.findByRole("alert", {
        name: "로그인에 실패했습니다. 사용자 이름 또는 비밀번호를 확인해 주세요.",
      }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("비밀번호")).toHaveValue("");
  });

  it("clears an in-progress password when the form is abandoned", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<LoginPage />);

    await user.type(
      screen.getByLabelText("비밀번호"),
      "synthetic-auth-secret-a",
    );
    expect(screen.getByLabelText("비밀번호")).toHaveValue(
      "synthetic-auth-secret-a",
    );

    unmount();
    expect(screen.queryByLabelText("비밀번호")).not.toBeInTheDocument();
  });

  it("keeps keyboard focus on the first field when displayed", () => {
    render(<LoginPage />);

    fireEvent.focus(screen.getByLabelText("사용자 이름"));
    expect(screen.getByLabelText("사용자 이름")).toHaveFocus();
  });
});
