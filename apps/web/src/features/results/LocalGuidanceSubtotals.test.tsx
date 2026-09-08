import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type {
  GuidanceCandidate,
  GuidanceFixedSubtotal,
  GuidanceSubtotalOmission,
  LocalGuidanceResponse,
} from "../../api/generated";
import { LocalGuidancePanel } from "./LocalGuidancePanel";

function candidate(
  number: number,
  amount = "100",
  currency = "KRW",
): GuidanceCandidate {
  return {
    ref: {
      kind: "OPERATIONAL_RIDER",
      contract_id: `synthetic-contract-${number}`,
      coverage_id: `synthetic-coverage-${number}`,
    },
    contract_label: `Sample Policy ${number}`,
    coverage_label: `Sample Coverage ${number}`,
    benefit_kind: "FIXED",
    group: "PRIMARY",
    condition_result: "MATCH",
    freshness: "DOCUMENT_CONTINUITY",
    reason_codes: ["DOCUMENTED_RELEVANT_COVERAGE"],
    estimate: {
      kind: "POINT",
      amount,
      currency,
      reason_code: "DOCUMENT_BASED_ESTIMATE",
    },
  };
}

const candidates = [candidate(1), candidate(2, "200"), candidate(3)];

function subtotal(
  overrides: Partial<GuidanceFixedSubtotal> = {},
): GuidanceFixedSubtotal {
  const items = candidates.slice(0, 2).map((value) => ({
    ref: value.ref,
    case_key: null,
    scenario_key: null,
    amount: value.estimate.amount!,
    trace_reference: {
      publication_id: "synthetic-publication",
      source_revision: "synthetic-source-v1",
      source_digest_sha256: "a".repeat(64),
      formula_digest_sha256: "b".repeat(64),
      runtime_revision: "synthetic-runtime-v1",
    },
  }));
  return {
    subtotal_key: "c".repeat(64),
    revision: "fixed-subtotals-v2",
    currency: "KRW",
    amount: "300",
    conditional: true,
    basis: "ASSUMED_COMBINATION",
    partial: false,
    items,
    scoped_assumptions: [
      { code: "INDEPENDENT_FIXED_PAYMENTS_ASSUMED", applies_to: items },
    ],
    ...overrides,
  };
}

function omission(
  overrides: Partial<GuidanceSubtotalOmission> = {},
): GuidanceSubtotalOmission {
  return {
    ref: candidates[2]!.ref,
    case_key: null,
    scenario_key: null,
    currency: "KRW",
    benefit_kind: "FIXED",
    reason_code: "POINT_ESTIMATE_UNAVAILABLE",
    ...overrides,
  };
}

function guidance(
  totals: GuidanceFixedSubtotal[] = [],
  omissions: GuidanceSubtotalOmission[] = [],
): LocalGuidanceResponse {
  return {
    candidates,
    event_date: "2026-09-01",
    event_version: 1,
    family_member_id: "synthetic-member",
    medical_event_id: "synthetic-event",
    outcome: "CANDIDATES",
    versions: {},
    support: {
      evaluated_coverages: 3,
      total_coverages: 3,
      unsupported_coverages: 0,
    },
    fixed_subtotals: totals,
    subtotal_omissions: omissions,
  };
}

function show(value: LocalGuidanceResponse) {
  return render(<LocalGuidancePanel guidance={value} onRetry={vi.fn()} />);
}

function planned(): GuidanceFixedSubtotal {
  const value = subtotal();
  const scenarioKey = "d".repeat(64);
  const items = value.items.map((item) => ({
    ...item,
    amount: "300",
    scenario_key: scenarioKey,
  }));
  return {
    ...value,
    subtotal_key: "e".repeat(64),
    scenario_key: scenarioKey,
    amount: "600",
    items,
    hypotheses: [
      {
        field_path: "MedicalEvent.admission_days",
        value: 5,
        spans: [{ start: 0, end: 4 }],
        source_refs: [
          {
            source_kind: "EVENT_SCENARIO",
            source_id: "synthetic-event",
            version: 1,
          },
        ],
      },
    ],
    scoped_assumptions: [
      { code: "INDEPENDENT_FIXED_PAYMENTS_ASSUMED", applies_to: items },
      { code: "PLANNED_CARE_ASSUMED", applies_to: items },
    ],
  };
}

describe("local fixed subtotal presentation", () => {
  it.each([
    ["SCENARIO_KNOWLEDGE_INCOMPLETE", "관련 약관의 해석이 끝나지 않아"],
    ["SCENARIO_ESTIMATE_CONFLICT", "예상액이 서로 달라"],
    ["SCENARIO_HYPOTHESES_CONFLICT", "가정이 서로 맞지 않아"],
    ["SCENARIO_SUBTOTAL_BUDGET_EXCEEDED", "예정 치료 조합이 많아"],
  ])(
    "explains %s without treating missing system support as missing user input",
    (reason, copy) => {
      show(guidance([], [omission({ reason_code: reason })]));
      const region = screen.getByRole("region", {
        name: "소계에 포함하지 않은 항목",
      });
      expect(region).toHaveTextContent(copy);
      expect(region.textContent).not.toContain(reason);
    },
  );

  it("shows the server's partial 300 with component amounts, scoped assumptions and missing items", async () => {
    const value = subtotal({ partial: true });
    value.scoped_assumptions.push({
      code: "DOCUMENT_CONTINUITY_ASSUMED",
      applies_to: [value.items[0]!],
    });
    show(guidance([value], [omission()]));
    const total = within(
      screen.getByRole("region", { name: "현재 사건 기준 소계 · KRW" }),
    );
    expect(total.getByText("300원")).toBeInTheDocument();
    expect(total.getByText(/부분 소계/)).toBeInTheDocument();
    expect(total.getByText(/공통 한도.*중복 지급 감액/)).toBeInTheDocument();
    const summary = total.getByText("포함한 담보와 합산 전제");
    expect(summary.closest("details")).not.toHaveAttribute("open");
    const user = userEvent.setup();
    await user.tab();
    expect(summary).toHaveFocus();
    // Native summary activation with Enter needs a browser check; jsdom
    // exercises keyboard focus and pointer activation here.
    await user.click(summary);
    expect(summary.closest("details")).toHaveAttribute("open");
    expect(total.getByText("100원")).toBeInTheDocument();
    expect(total.getByText("200원")).toBeInTheDocument();
    expect(total.getAllByText(/Sample Coverage 1/).length).toBeGreaterThan(0);
    const missing = within(
      screen.getByRole("region", { name: "소계에 포함하지 않은 항목" }),
    );
    expect(missing.getByText(/Sample Coverage 3/)).toBeInTheDocument();
    expect(missing.getByText(/계산식이나 필요한 입력/)).toBeInTheDocument();
  });

  it("keeps planned 600 separate from actual-context 300 and labels the exact planned hypothesis", () => {
    show(guidance([subtotal(), planned()]));
    const actual = within(
      screen.getByRole("region", { name: "현재 사건 기준 소계 · KRW" }),
    );
    const scenario = within(
      screen.getByRole("region", { name: "예정 치료 가정 1 소계 · KRW" }),
    );
    expect(actual.getByText("300원")).toBeInTheDocument();
    expect(scenario.getByText("600원")).toBeInTheDocument();
    expect(scenario.getByText(/입원 일수.*5일/)).toBeInTheDocument();
    expect(scenario.getByText(/실제 치료 사실/)).toBeInTheDocument();
    expect(screen.queryByText("900원")).not.toBeInTheDocument();
  });

  it("keeps each currency's server subtotal without conversion or a combined amount", () => {
    show(
      guidance([
        subtotal(),
        subtotal({
          subtotal_key: "f".repeat(64),
          currency: "USD",
          amount: "700",
        }),
      ]),
    );
    expect(
      within(
        screen.getByRole("region", { name: "현재 사건 기준 소계 · KRW" }),
      ).getByText("300원"),
    ).toBeInTheDocument();
    expect(
      within(
        screen.getByRole("region", { name: "현재 사건 기준 소계 · USD" }),
      ).getByText("700 USD"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/1,000원|1,000 USD/)).not.toBeInTheDocument();
  });

  it("explains unavailable fixed and indemnity omissions without inventing a zero subtotal", () => {
    show(
      guidance(
        [],
        [
          omission(),
          omission({
            ref: candidates[1]!.ref,
            benefit_kind: "INDEMNITY",
            reason_code: "NON_FIXED_BENEFIT",
          }),
        ],
      ),
    );
    const region = within(
      screen.getByRole("region", { name: "조건부 정액 소계" }),
    );
    expect(
      region.getByText(/함께 묶을 수 있는 정액 소계가 없습니다/),
    ).toBeInTheDocument();
    expect(region.getByText(/실손형.*정액 소계/)).toBeInTheDocument();
    expect(region.queryByText("0원")).not.toBeInTheDocument();
  });

  it("never prints source identifiers, digests or unknown internal reason codes", () => {
    const value = subtotal();
    value.scoped_assumptions.push({
      code: "SYNTHETIC_NEW_ASSUMPTION",
      applies_to: [value.items[0]!],
    });
    show(
      guidance([value], [omission({ reason_code: "SYNTHETIC_NEW_OMISSION" })]),
    );
    const region = screen.getByRole("region", { name: "조건부 정액 소계" });
    expect(region.textContent).not.toMatch(
      /synthetic-|SYNTHETIC_|OPERATIONAL_RIDER|[a-f]{64}|\/calculation/,
    );
    expect(within(region).getByText(/추가 가정/)).toBeInTheDocument();
    expect(within(region).getByText(/별도로 확인할 항목/)).toBeInTheDocument();
  });

  it("labels an omission's planned context instead of attaching it to the actual total", () => {
    const scenario = planned();
    show(
      guidance(
        [subtotal(), scenario],
        [
          omission({
            scenario_key: scenario.scenario_key,
            reason_code: "EVENT_CONDITIONS_UNRESOLVED",
          }),
        ],
      ),
    );
    const omitted = within(
      screen.getByRole("region", { name: "소계에 포함하지 않은 항목" }),
    );
    expect(omitted.getByText(/예정 치료 가정 1/)).toBeInTheDocument();
    expect(omitted.getByText(/사건 조건/)).toBeInTheDocument();
  });

  it("does not add a subtotal panel to older snapshots without subtotal fields", () => {
    const value = guidance();
    delete value.fixed_subtotals;
    delete value.subtotal_omissions;
    show(value);
    expect(
      screen.queryByRole("region", { name: "조건부 정액 소계" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Sample Coverage 1" }),
    ).toBeInTheDocument();
  });
});
