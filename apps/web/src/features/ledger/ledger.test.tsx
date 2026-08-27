import { fireEvent, screen, waitFor } from "@testing-library/react";
import type { PolicyReviewItem } from "../../api/generated";

import { LedgerPage } from "./LedgerPage";
import {
  createMockApi,
  jsonResponse,
  SYNTHETIC_EMPTY_LEDGER,
  SYNTHETIC_LEDGER,
} from "../../test/mockApi";
import { renderWithProviders } from "../../test/renderWithProviders";
import { authStore } from "../identity/authStore";

function installFetch(fixture = SYNTHETIC_LEDGER): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(createMockApi(fixture));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function setViewportWidth(width: number): void {
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    value: width,
  });
  window.dispatchEvent(new Event("resize"));
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setViewportWidth(1024);
});

describe("ledger read projection", () => {
  it("uses same-origin requests with credentials and no-store, without Web Storage writes", async () => {
    const fetchMock = installFetch();
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");

    renderWithProviders(<LedgerPage memberId="synthetic-member-a" />);

    expect(
      await screen.findByRole("heading", { name: "Sample Policy" }),
    ).toBeInTheDocument();

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    for (const [input, init] of fetchMock.mock.calls) {
      expect(String(input)).toMatch(/^\/api\/v1\//);
      expect(init).toMatchObject({
        cache: "no-store",
        credentials: "include",
      });
    }
    expect(storageWrite).not.toHaveBeenCalled();
  });

  it("projects the selected family member's policy and enrolled Riders", async () => {
    installFetch();

    renderWithProviders(<LedgerPage memberId="synthetic-member-a" />);

    expect(
      await screen.findByRole("heading", { name: "Sample Policy" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Sample Hospital Benefit" }),
    ).toBeInTheDocument();

    const picker = screen.getByRole("combobox", {
      name: /family member|가족 구성원/i,
    });
    fireEvent.change(picker, { target: { value: "synthetic-member-b" } });

    expect(
      await screen.findByRole("heading", { name: "Sample Policy B" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Sample Policy" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Sample Travel Benefit" }),
    ).toBeInTheDocument();
  });

  it("requests and labels review candidates for the selected member with every issue", async () => {
    const memberAItem: PolicyReviewItem = {
      ...SYNTHETIC_LEDGER.reviewItems[0],
      review_item_id: "synthetic-review-item-member-a",
      candidate_kind: "policy_contract",
      fields: [
        { field_id: "insurer", value: "Sample Insurer", evidence_ids: [] },
        {
          field_id: "product_name",
          value: "Sample Review Plan",
          evidence_ids: [],
        },
      ],
      issues: [
        { code: "LOW_CONFIDENCE", field_id: "rider_name" },
        { code: "INVALID_DATE", field_id: "coverage_end" },
      ],
    };
    const memberBItem: PolicyReviewItem = {
      ...SYNTHETIC_LEDGER.reviewItems[0],
      review_item_id: "synthetic-review-item-member-b",
      issues: [{ code: "CONFLICTING_EVIDENCE", field_id: "rider_name" }],
    };
    const fetchMock = installFetch({
      ...SYNTHETIC_LEDGER,
      reviewItems: [memberAItem],
      reviewItemsByMember: {
        "synthetic-member-a": [memberAItem],
        "synthetic-member-b": [memberBItem],
      },
    });

    renderWithProviders(<LedgerPage memberId="synthetic-member-a" />);

    expect(await screen.findByText("LOW_CONFIDENCE")).toBeInTheDocument();
    expect(screen.getByText("INVALID_DATE")).toBeInTheDocument();
    expect(screen.getByText("대상: Family Member A")).toBeInTheDocument();
    expect(
      screen.getByText("보험: Sample Insurer · Sample Review Plan"),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) => {
          const url = new URL(String(input), window.location.origin);
          return (
            url.pathname === "/api/v1/review-items" &&
            url.searchParams.get("family_member_id") === "synthetic-member-a"
          );
        }),
      ).toBe(true);
    });

    fireEvent.change(
      screen.getByRole("combobox", { name: /family member|가족 구성원/i }),
      { target: { value: "synthetic-member-b" } },
    );

    expect(await screen.findByText("CONFLICTING_EVIDENCE")).toBeInTheDocument();
    expect(screen.getByText("대상: Family Member B")).toBeInTheDocument();
    expect(screen.queryByText("INVALID_DATE")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) => {
          const url = new URL(String(input), window.location.origin);
          return (
            url.pathname === "/api/v1/review-items" &&
            url.searchParams.get("family_member_id") === "synthetic-member-b"
          );
        }),
      ).toBe(true);
    });

    expect(
      await screen.findByRole("heading", { name: "Sample Policy B" }),
    ).toBeInTheDocument();
  });

  it("shows the NEEDS_REVIEW count without projecting a terms-only candidate as an enrolled Rider", async () => {
    installFetch();

    renderWithProviders(<LedgerPage memberId="synthetic-member-a" />);

    expect(
      await screen.findByRole("heading", { name: "Sample Hospital Benefit" }),
    ).toBeInTheDocument();
    const reviewStatus = await screen.findByRole("status", {
      name: /needs_review|검토/i,
    });
    expect(reviewStatus).toHaveTextContent("1");
    expect(screen.queryByText("Terms-only Rider")).not.toBeInTheDocument();
  });

  it("keeps the core ledger controls semantically reachable at a 320px viewport", async () => {
    setViewportWidth(320);
    installFetch();

    renderWithProviders(<LedgerPage memberId="synthetic-member-a" />);

    expect(
      await screen.findByRole("heading", { name: "Sample Policy" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: /family member|가족 구성원/i }),
    ).toBeVisible();
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Sample Hospital Benefit" }),
    ).toBeVisible();
  });

  it("announces loading before the ledger request resolves", () => {
    const pendingResponse = new Promise<Response>(() => undefined);
    const fetchMock = vi.fn().mockReturnValue(pendingResponse);
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<LedgerPage memberId="synthetic-member-a" />);

    expect(screen.getByRole("status")).toHaveTextContent(
      /loading|불러오는 중/i,
    );
  });

  it("renders a safe empty-family state without a policy projection", async () => {
    installFetch(SYNTHETIC_EMPTY_LEDGER);

    renderWithProviders(<LedgerPage memberId="synthetic-member-a" />);

    expect(
      await screen.findByText(/no family member|가족 구성원.*없/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Sample Policy" }),
    ).not.toBeInTheDocument();
  });

  it("clears authentication on a 401 without echoing the response detail", async () => {
    const privateDetail = "synthetic private upstream detail";
    authStore.setAuthenticated({
      display_name: "Admin A",
      needs_reauthentication: false,
      user_id: "synthetic-user-a",
      username: "admin-a",
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(
          { error_code: "AUTHENTICATION_REQUIRED", message: privateDetail },
          401,
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<LedgerPage memberId="synthetic-member-a" />);

    await waitFor(() =>
      expect(authStore.getSnapshot().status).toBe("unauthenticated"),
    );
    expect(document.body).not.toHaveTextContent(privateDetail);
  });

  it("sanitizes a non-authentication API failure", async () => {
    const privateDetail = "synthetic raw provider response /private/source";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ message: privateDetail }, 500)),
    );

    renderWithProviders(<LedgerPage memberId="synthetic-member-a" />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/could not|오류|불러오지 못/i);
    expect(alert).not.toHaveTextContent(privateDetail);
  });
});
