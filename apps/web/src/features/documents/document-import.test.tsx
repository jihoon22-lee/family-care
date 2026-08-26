import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  BatchResponse,
  FamilyMemberResponse,
  ImportSourceResponse,
} from "../../api/generated";
import {
  cancelDocumentBatch,
  createDocumentBatch,
  handoffBatchPassword,
  listImportSources,
} from "../../api/document-imports";
import { renderWithProviders } from "../../test/renderWithProviders";
import { ImportPage } from "./ImportPage";

const MEMBER: FamilyMemberResponse = {
  deleted: false,
  display_name: "Family Member A",
  id: "00000000-0000-4000-8000-000000000101",
  internal_alias: "family-member-a",
  version: 1,
};
const SOURCE: ImportSourceResponse = {
  display_label: "Sample Policy A.pdf",
  encrypted: true,
  size_bytes: 1024,
  source_id: "a".repeat(64),
};
const BATCH_ID = "00000000-0000-4000-8000-000000000201";

function batch(
  state: BatchResponse["state"],
  itemState: BatchResponse["items"][number]["state"],
): BatchResponse {
  return {
    batch_id: BATCH_ID,
    family_member_id: MEMBER.id,
    items: [
      {
        attempts: itemState === "queued" ? 0 : 1,
        display_label: SOURCE.display_label,
        error_code:
          itemState === "password_required" ? "PASSWORD_REQUIRED" : null,
        ocr_pages_processed: itemState === "succeeded" ? 1 : 0,
        ocr_state: itemState === "succeeded" ? "completed" : "pending",
        ocr_warning_codes: [],
        source_id: SOURCE.source_id,
        state: itemState,
      },
    ],
    schema_version: "1",
    state,
  };
}

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    headers: {
      "cache-control": "no-store",
      "content-type": "application/json",
    },
    status,
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("document import API", () => {
  it("uses only opaque no-store endpoints and keeps the password out of URLs", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response([SOURCE]))
      .mockResolvedValueOnce(response(batch("created", "queued"), 202))
      .mockResolvedValueOnce(response(batch("succeeded", "succeeded"), 202))
      .mockResolvedValueOnce(response(batch("cancelled", "cancelled"), 202));
    vi.stubGlobal("fetch", fetchMock);

    await listImportSources();
    await createDocumentBatch(MEMBER.id, [SOURCE.source_id]);
    await handoffBatchPassword(BATCH_ID, "synthetic-password");
    await cancelDocumentBatch(BATCH_ID);

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/document-import-sources",
      "/api/v1/document-batches",
      `/api/v1/document-batches/${BATCH_ID}/password`,
      `/api/v1/document-batches/${BATCH_ID}/cancel`,
    ]);
    expect(
      fetchMock.mock.calls.every((call) => call[1]?.cache === "no-store"),
    ).toBe(true);
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      family_member_id: MEMBER.id,
      schema_version: "1",
      source_ids: [SOURCE.source_id],
    });
    expect(String(fetchMock.mock.calls[2]?.[1]?.body)).toBe(
      JSON.stringify({ password: "synthetic-password" }),
    );
    expect(
      fetchMock.mock.calls.map((call) => String(call[0])).join(" "),
    ).not.toMatch(/Sample Policy|\.pdf|synthetic-password/);
  });
});

describe("document import page", () => {
  it("selects one member and opaque sources without upload or path controls", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response([MEMBER]))
      .mockResolvedValueOnce(response([SOURCE]));
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<ImportPage />);

    expect(await screen.findByText(SOURCE.display_label)).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "가족 구성원" })).toHaveValue(
      MEMBER.id,
    );
    expect(screen.queryByLabelText(/경로|폴더/)).not.toBeInTheDocument();
    expect(document.querySelector('input[type="file"]')).toBeNull();
    expect(
      screen.getByRole("button", { name: "가져오기 시작" }),
    ).toBeDisabled();

    fireEvent.click(
      screen.getByRole("checkbox", { name: /^Sample Policy A\.pdf/ }),
    );
    expect(screen.getByRole("button", { name: "가져오기 시작" })).toBeEnabled();
  });

  it("prompts only failed items once, clears the password, and preserves successes", async () => {
    const partial: BatchResponse = {
      ...batch("partial", "password_required"),
      items: [
        {
          attempts: 1,
          display_label: "Sample Policy Completed.pdf",
          error_code: null,
          ocr_pages_processed: 1,
          ocr_state: "completed",
          ocr_warning_codes: [],
          source_id: "b".repeat(64),
          state: "succeeded",
        },
        batch("partial", "password_required").items[0]!,
      ],
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response([MEMBER]))
      .mockResolvedValueOnce(response([SOURCE]))
      .mockResolvedValueOnce(response(partial, 202))
      .mockResolvedValueOnce(
        response(
          {
            ...partial,
            items: partial.items.map((item) => ({
              ...item,
              error_code: null,
              state: "succeeded" as const,
            })),
            state: "succeeded" as const,
          },
          202,
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const storageSet = vi.spyOn(Storage.prototype, "setItem");
    const user = userEvent.setup();

    renderWithProviders(<ImportPage />);
    await user.click(
      await screen.findByRole("checkbox", { name: /^Sample Policy A\.pdf/ }),
    );
    await user.click(screen.getByRole("button", { name: "가져오기 시작" }));

    expect(
      await screen.findByText("Sample Policy Completed.pdf"),
    ).toBeInTheDocument();
    expect(screen.getByText("완료")).toBeInTheDocument();
    const password = screen.getByLabelText("PDF 비밀번호");
    await user.type(password, "synthetic-password");
    await user.click(screen.getByRole("button", { name: "다시 처리" }));

    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(screen.getAllByText("완료")).toHaveLength(2);
    expect(String(fetchMock.mock.calls[3]?.[1]?.body)).toBe(
      JSON.stringify({ password: "synthetic-password" }),
    );
    expect(storageSet).not.toHaveBeenCalled();
  });

  it("clears and closes a dismissed password prompt", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response([MEMBER]))
      .mockResolvedValueOnce(response([SOURCE]))
      .mockResolvedValueOnce(
        response(batch("partial", "password_required"), 202),
      );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<ImportPage />);
    await user.click(
      await screen.findByRole("checkbox", { name: /^Sample Policy A\.pdf/ }),
    );
    await user.click(screen.getByRole("button", { name: "가져오기 시작" }));
    await user.type(
      await screen.findByLabelText("PDF 비밀번호"),
      "temporary-value",
    );
    await user.click(screen.getByRole("button", { name: "닫기" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("temporary-value");
  });

  it("cancels an active batch without persisting state", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response([MEMBER]))
      .mockResolvedValueOnce(response([SOURCE]))
      .mockResolvedValueOnce(response(batch("running", "running"), 202))
      .mockResolvedValueOnce(response(batch("cancelled", "cancelled"), 202));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<ImportPage />);
    await user.click(
      await screen.findByRole("checkbox", { name: /^Sample Policy A\.pdf/ }),
    );
    await user.click(screen.getByRole("button", { name: "가져오기 시작" }));
    await user.click(
      await screen.findByRole("button", { name: "가져오기 취소" }),
    );

    expect(await screen.findByText("취소됨")).toBeInTheDocument();
    expect(fetchMock.mock.calls[3]?.[0]).toBe(
      `/api/v1/document-batches/${BATCH_ID}/cancel`,
    );
  });

  it("polls an active batch until it reaches a terminal state", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response([MEMBER]))
      .mockResolvedValueOnce(response([SOURCE]))
      .mockResolvedValueOnce(response(batch("running", "running"), 202))
      .mockResolvedValueOnce(response(batch("succeeded", "succeeded")));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<ImportPage />);
    await user.click(
      await screen.findByRole("checkbox", { name: /^Sample Policy A\.pdf/ }),
    );
    await user.click(screen.getByRole("button", { name: "가져오기 시작" }));

    expect(
      await screen.findByText("완료", { exact: true }, { timeout: 2500 }),
    ).toBeInTheDocument();
    expect(fetchMock.mock.calls[3]?.[0]).toBe(
      `/api/v1/document-batches/${BATCH_ID}`,
    );
  });

  it("clears local batch selections when authentication expires", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response([MEMBER]))
      .mockResolvedValueOnce(response([SOURCE]))
      .mockResolvedValueOnce(
        response({ error_code: "AUTHENTICATION_REQUIRED" }, 401),
      );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<ImportPage />);
    await user.click(
      await screen.findByRole("checkbox", { name: /^Sample Policy A\.pdf/ }),
    );
    await user.click(screen.getByRole("button", { name: "가져오기 시작" }));

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "가져오기 시작" }),
      ).toBeDisabled(),
    );
    expect(
      screen.getByRole("checkbox", { name: /^Sample Policy A\.pdf/ }),
    ).not.toBeChecked();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
