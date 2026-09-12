import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SYNTHETIC_LEDGER } from "../../test/mockApi";
import { PolicySummaryCard } from "./PolicySummaryCard";

describe("source-scoped policy identity", () => {
  it("keeps proven enrollment visible when the issuer is unverified", () => {
    const original = SYNTHETIC_LEDGER.policies[0];
    const policy = {
      ...original,
      insurer_display: null,
      insurer_key: null,
      insurer_unresolved_reason: "INSURER_SOURCE_UNVERIFIED" as const,
    };
    const riders = SYNTHETIC_LEDGER.ridersByPolicy[original.id];
    render(<PolicySummaryCard policy={policy} riders={riders} />);
    expect(screen.getByText("보험사 확인 전")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: original.product_display }),
    ).toBeInTheDocument();
    expect(screen.getByText(riders[0].display_name)).toBeInTheDocument();
    expect(policy.insurer_key).toBeNull();
  });
});
