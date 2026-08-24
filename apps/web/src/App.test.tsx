import { render, screen } from "@testing-library/react";

import { App } from "./App";

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
  });
});
