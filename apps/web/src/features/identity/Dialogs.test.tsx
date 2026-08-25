import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { ChangePasswordDialog } from "./ChangePasswordDialog";
import { ReauthenticateDialog } from "./ReauthenticateDialog";

function ReauthenticationHarness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)} type="button">
        세션 폐기 시작
      </button>
      <ReauthenticateDialog
        onCancel={() => setOpen(false)}
        onSubmit={vi.fn()}
        open={open}
      />
    </>
  );
}

describe("identity dialogs", () => {
  it("traps keyboard focus and returns it to the invoking control", async () => {
    const user = userEvent.setup();
    render(<ReauthenticationHarness />);
    const opener = screen.getByRole("button", { name: "세션 폐기 시작" });

    await user.click(opener);
    const password = screen.getByLabelText("비밀번호");
    expect(password).toHaveFocus();

    await user.tab({ shift: true });
    expect(screen.getByRole("button", { name: "확인" })).toHaveFocus();
    await user.tab();
    expect(password).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("matches the server minimum password length and clears rejected input", async () => {
    const user = userEvent.setup();
    const submit = vi.fn();
    render(<ChangePasswordDialog onCancel={vi.fn()} onSubmit={submit} open />);

    await user.type(screen.getByLabelText("새 비밀번호"), "short-auth-valu");
    await user.type(
      screen.getByLabelText("새 비밀번호 확인"),
      "short-auth-valu",
    );
    await user.click(screen.getByRole("button", { name: "비밀번호 변경" }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "새 비밀번호는 16자 이상이어야 합니다.",
    );
    expect(submit).not.toHaveBeenCalled();
  });
});
