import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import type { EvidenceDetailResponse } from "../api/generated";
import { EvidenceDrawer } from "./EvidenceDrawer";

const SYNTHETIC_EVIDENCE: EvidenceDetailResponse = {
  bbox: [10.25, 20.5, 120.75, 160.125],
  bounded_excerpt: "Synthetic bounded Evidence excerpt.",
  clause_label: "Sample Clause 3",
  document_label: "Sample Policy",
  document_version_id: "synthetic-document-version-001",
  evidence_id: "synthetic-evidence-001",
  physical_page: 3,
  review_state: "USER_CONFIRMED",
  schema_version: "1",
};

function DrawerHarness({ unavailable = false }: { unavailable?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        근거 보기
      </button>
      <EvidenceDrawer
        evidence={[SYNTHETIC_EVIDENCE]}
        open={open}
        unavailable={unavailable}
        onClose={() => setOpen(false)}
      />
    </>
  );
}

describe("EvidenceDrawer", () => {
  it("opens bounded canonical Evidence and focuses its heading", async () => {
    const user = userEvent.setup();
    render(<DrawerHarness />);

    await user.click(screen.getByRole("button", { name: "근거 보기" }));

    const dialog = screen.getByRole("dialog", {
      name: "증권과 약관 근거",
    });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(
      screen.getByRole("heading", { name: "증권과 약관 근거" }),
    ).toHaveFocus();
    expect(screen.getByText("Sample Policy")).toBeInTheDocument();
    expect(screen.getByText("페이지 3")).toBeInTheDocument();
    expect(screen.getByText("Sample Clause 3")).toBeInTheDocument();
    expect(
      screen.getByText("Synthetic bounded Evidence excerpt."),
    ).toBeInTheDocument();
    expect(screen.getByText(/USER_CONFIRMED/)).toBeInTheDocument();
    expect(screen.getByText(/10\.25/)).toBeInTheDocument();
    expect(dialog).not.toHaveTextContent("synthetic-document-version-001");
    expect(dialog).not.toHaveTextContent("synthetic-evidence-001");
  });

  it("closes on Escape and restores focus to the trigger", async () => {
    const user = userEvent.setup();
    render(<DrawerHarness />);

    const trigger = screen.getByRole("button", { name: "근거 보기" });
    await user.click(trigger);
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("keeps Tab inside the dialog and supports Enter activation", async () => {
    const user = userEvent.setup();
    render(<DrawerHarness />);

    const trigger = screen.getByRole("button", { name: "근거 보기" });
    await user.click(trigger);
    const close = screen.getByRole("button", { name: "닫기" });

    await user.tab();
    expect(close).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();
    await user.keyboard("{Enter}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("closes from the visible button and restores trigger focus", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    function ControlledDrawer() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            근거 보기
          </button>
          <EvidenceDrawer
            evidence={[SYNTHETIC_EVIDENCE]}
            open={open}
            onClose={() => {
              onClose();
              setOpen(false);
            }}
          />
        </>
      );
    }

    render(<ControlledDrawer />);
    await user.click(screen.getByRole("button", { name: "근거 보기" }));
    const close = screen.getByRole("button", { name: "닫기" });
    await user.click(close);

    expect(onClose).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "근거 보기" })).toHaveFocus();
  });

  it("shows a stable unavailable state without raw Evidence values", () => {
    render(
      <EvidenceDrawer
        evidence={[SYNTHETIC_EVIDENCE]}
        open
        unavailable
        onClose={vi.fn()}
      />,
    );

    const dialog = screen.getByRole("dialog", {
      name: "증권과 약관 근거",
    });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("EVIDENCE_UNAVAILABLE");
    expect(dialog).not.toHaveTextContent("Sample Policy");
    expect(dialog).not.toHaveTextContent("Synthetic bounded Evidence excerpt.");
    expect(dialog).not.toHaveTextContent("synthetic-evidence-001");
  });
});
