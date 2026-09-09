import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ExistingEventPage, NewEventPage } from "./NewEventPage";

const baseEvent = {
  id: "synthetic-event",
  family_member_id: "synthetic-member",
  version: 1,
  deleted: false,
  mode: "post_treatment",
  situation: "합성 영수증 사건",
  facts: {},
  event_date: null,
  visit_date: null,
  structured_facts: [],
  optional_questions: [],
};
const line = (id: string, amount: string) => ({
  id,
  event_id: baseEvent.id,
  version: 1,
  amount,
  category: "outpatient",
  coverage_category: "unknown",
  confirmation_level: "user",
  currency: "KRW",
});
const response = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("receipt synchronization recovery", () => {
  it("retains a successful first creation when the second creation fails", async () => {
    window.history.replaceState({}, "", "/app/events/new?mode=post_treatment");
    const creates: string[] = [];
    let eventCreates = 0,
      version = 1,
      failed = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input), location.origin).pathname;
        if (path === "/api/v1/family-members") return response([]);
        if (path === "/api/v1/medical-events") {
          eventCreates += 1;
          return response(baseEvent, 201);
        }
        if (path === `/api/v1/medical-events/${baseEvent.id}`)
          return response({ ...baseEvent, version: ++version });
        if (path.endsWith("/receipt-lines") && init?.method === "POST") {
          const body = JSON.parse(String(init.body));
          creates.push(body.amount);
          if (body.amount === "200" && !failed) {
            failed = true;
            return response({ error_code: "RESOURCE_LIMIT_EXCEEDED" }, 503);
          }
          return response(
            line(`synthetic-line-${body.amount}`, body.amount),
            201,
          );
        }
        return response({ error_code: "NOT_FOUND" }, 404);
      }),
    );
    render(<NewEventPage memberId={baseEvent.family_member_id} />);
    const user = userEvent.setup();
    await user.type(
      screen.getByRole("textbox", { name: "현재 상황" }),
      baseEvent.situation,
    );
    for (const amount of ["100", "200"]) {
      await user.click(
        screen.getByRole("button", { name: "영수증 항목 추가" }),
      );
      await user.type(screen.getByRole("spinbutton", { name: "금액" }), amount);
      await user.click(screen.getByRole("button", { name: "항목 저장" }));
    }
    await user.click(screen.getByRole("button", { name: "현재 후보 보기" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "저장하지 못했습니다",
    );
    await user.click(screen.getByRole("button", { name: "현재 후보 보기" }));
    await screen.findByText(
      "현재 입력을 저장했습니다. 추가 확인 질문은 선택 사항입니다.",
    );
    expect(creates).toEqual(["100", "200", "200"]);
    expect(eventCreates).toBe(1);
  });

  it("does not delete an already removed receipt again when a later deletion fails", async () => {
    const deletes: string[] = [];
    let version = 1,
      failed = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input), location.origin).pathname;
        if (path === "/api/v1/family-members") return response([]);
        if (path === `/api/v1/medical-events/${baseEvent.id}`)
          return response({
            ...baseEvent,
            version: init?.method === "PATCH" ? ++version : version,
          });
        if (path.endsWith("/receipt-lines"))
          return response({
            receipt_lines: [
              line("synthetic-line-1", "100"),
              line("synthetic-line-2", "200"),
            ],
          });
        if (init?.method === "DELETE") {
          deletes.push(path.split("/").at(-1)!);
          if (path.endsWith("synthetic-line-2") && !failed) {
            failed = true;
            return response({ error_code: "RESOURCE_LIMIT_EXCEEDED" }, 503);
          }
          if (deletes.filter((id) => id === "synthetic-line-1").length > 1)
            return response({ error_code: "NOT_FOUND" }, 404);
          return new Response(null, { status: 204 });
        }
        return response({ error_code: "NOT_FOUND" }, 404);
      }),
    );
    render(<ExistingEventPage eventId={baseEvent.id} />);
    const user = userEvent.setup();
    await screen.findByRole("textbox", { name: "현재 상황" });
    await user.click(screen.getAllByRole("button", { name: "삭제" })[0]);
    await user.click(screen.getByRole("button", { name: "삭제" }));
    await user.click(screen.getByRole("button", { name: "현재 후보 보기" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "저장하지 못했습니다",
    );
    await user.click(screen.getByRole("button", { name: "현재 후보 보기" }));
    await screen.findByText(
      "현재 입력을 저장했습니다. 추가 확인 질문은 선택 사항입니다.",
    );
    expect(deletes).toEqual([
      "synthetic-line-1",
      "synthetic-line-2",
      "synthetic-line-2",
    ]);
  });
});
