import { render, screen } from "@testing-library/react";

import { App } from "./App";

describe("FamilyCare foundation shell", () => {
  it("states the product boundary and current phase", () => {
    render(<App />);

    expect(
      screen.getByRole("heading", { name: "FamilyCare" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/보험금 지급을 보장하지 않습니다/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Foundation/)).toBeInTheDocument();
    expect(screen.getByText("MATCH")).toBeInTheDocument();
    expect(screen.getByText("UNKNOWN")).toBeInTheDocument();
    expect(screen.getByText("NO_MATCH")).toBeInTheDocument();
  });
});
