import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  EventComposer,
  type EventFactView,
  type ReceiptLineView,
} from "./EventComposer";
import { NewEventPage } from "./NewEventPage";
import { OptionalQuestionList } from "./OptionalQuestionList";
import { ReceiptLineEditor } from "./ReceiptLineEditor";
import { StructuredFactEditor } from "./StructuredFactEditor";

const SYNTHETIC_FACT: EventFactView = {
  fact_id: "synthetic-fact-001",
  field_id: "treatment_kind",
  value: "synthetic-treatment",
  source: "ai",
  state: "ambiguous",
  confidence: "medium",
  evidence_ids: [],
};

const SYNTHETIC_LINE: ReceiptLineView = {
  id: "synthetic-line-001",
  category: "outpatient",
  coverage_category: "covered",
  amount: "12000.00",
  currency: "KRW",
  confirmation_level: "user",
};

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("hybrid medical event input", () => {
  it("submits a pre-visit situation without requiring optional questions", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");

    render(<EventComposer memberId="synthetic-member-a" onSubmit={onSubmit} />);

    await user.type(
      screen.getByRole("textbox", { name: "현재 상황" }),
      "Synthetic pre-visit situation",
    );
    await user.click(screen.getByRole("button", { name: "현재 후보 보기" }));

    expect(
      screen.getByText("추가 확인 질문은 선택 사항입니다."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "결과 확인" }),
    ).toBeInTheDocument();
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        family_member_id: "synthetic-member-a",
        mode: "pre_visit",
        situation: "Synthetic pre-visit situation",
      }),
    );
    expect(storageWrite).not.toHaveBeenCalled();
  });

  it("creates a server event before exposing structure and analysis actions", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          deleted: false,
          event_date: null,
          facts: {},
          family_member_id: "00000000-0000-4000-8000-000000000202",
          id: "00000000-0000-4000-8000-000000000201",
          mode: "pre_visit",
          optional_questions: [],
          situation: "Synthetic pre-visit situation",
          structured_facts: [],
          version: 1,
          visit_date: null,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<NewEventPage memberId="00000000-0000-4000-8000-000000000202" />);
    await user.type(
      screen.getByRole("textbox", { name: "현재 상황" }),
      "Synthetic pre-visit situation",
    );
    await user.click(screen.getByRole("button", { name: "현재 후보 보기" }));

    expect(
      await screen.findByRole("button", { name: "결과 확인" }),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/medical-events",
      expect.objectContaining({
        cache: "no-store",
        credentials: "include",
        method: "POST",
      }),
    );
    const body = fetchMock.mock.calls[0]?.[1]?.body as string;
    expect(JSON.parse(body)).toEqual(
      expect.objectContaining({
        family_member_id: "00000000-0000-4000-8000-000000000202",
        situation: "Synthetic pre-visit situation",
      }),
    );
  });

  it("requires a situation but does not persist an unsaved draft", async () => {
    const user = userEvent.setup();
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");

    render(<EventComposer memberId="synthetic-member-a" />);
    await user.click(screen.getByRole("button", { name: "현재 후보 보기" }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "현재 상황을 입력해 주세요.",
    );
    expect(storageWrite).not.toHaveBeenCalled();
  });

  it("keeps optional questions dismissible and separate from the result action", async () => {
    const user = userEvent.setup();

    render(
      <OptionalQuestionList
        questions={[
          {
            question_code: "visit_date",
            field_id: "visit_date",
          },
        ]}
      />,
    );

    expect(
      screen.getByText("추가 확인 질문은 선택 사항입니다."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/결과 그룹에 영향을 줄 수 있습니다/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "질문 닫기" }));
    expect(
      screen.queryByText("추가 확인 질문은 선택 사항입니다."),
    ).not.toBeInTheDocument();
  });

  it("marks an edited AI fact as user input and exposes its state text", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    function FactHarness() {
      const [facts, setFacts] = useState<EventFactView[]>([SYNTHETIC_FACT]);
      return (
        <StructuredFactEditor
          facts={facts}
          onChange={(nextFacts) => {
            onChange(nextFacts);
            setFacts(nextFacts);
          }}
        />
      );
    }

    render(<FactHarness />);

    expect(screen.getByText("AI 제안 · 추가 확인 필요")).toBeInTheDocument();
    const input = screen.getByRole("textbox", { name: "치료 종류" });
    await user.clear(input);
    await user.type(input, "synthetic-corrected-treatment");

    expect(screen.getByText("사용자 입력 · 확인됨")).toBeInTheDocument();
    expect(onChange).toHaveBeenLastCalledWith([
      expect.objectContaining({
        field_id: "treatment_kind",
        value: "synthetic-corrected-treatment",
        source: "user",
        state: "confirmed",
      }),
    ]);
  });

  it("blocks a negative, exponent, or currency-mismatched receipt line", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    render(<ReceiptLineEditor lines={[]} currency="KRW" onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: "영수증 항목 추가" }));
    const amount = screen.getByRole("spinbutton", { name: "금액" });
    await user.type(amount, "-1.00");
    await user.click(screen.getByRole("button", { name: "항목 저장" }));
    expect(screen.getByRole("alert")).toHaveTextContent("0 이상");

    await user.clear(amount);
    await user.type(amount, "1e2");
    await user.click(screen.getByRole("button", { name: "항목 저장" }));
    expect(screen.getByRole("alert")).toHaveTextContent("지수 표기");

    await user.clear(amount);
    await user.type(amount, "100.00");
    const currency = screen.getByRole("textbox", { name: "통화" });
    await user.clear(currency);
    await user.type(currency, "USD");
    await user.click(screen.getByRole("button", { name: "항목 저장" }));
    expect(screen.getByRole("alert")).toHaveTextContent("통화가 일치");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("adds, edits, and removes manual receipt lines without an upload control", async () => {
    const user = userEvent.setup();
    function ReceiptHarness() {
      const [lines, setLines] = useState<ReceiptLineView[]>([SYNTHETIC_LINE]);
      return (
        <ReceiptLineEditor lines={lines} currency="KRW" onChange={setLines} />
      );
    }

    render(<ReceiptHarness />);
    expect(screen.queryByLabelText(/파일|PDF|이미지/i)).not.toBeInTheDocument();
    const existing = screen.getByRole("listitem", {
      name: /외래.*12000\.00 KRW/i,
    });
    await user.click(within(existing).getByRole("button", { name: "수정" }));
    const amount = screen.getByRole("spinbutton", { name: "금액" });
    await user.clear(amount);
    await user.type(amount, "13000.00");
    await user.click(screen.getByRole("button", { name: "항목 저장" }));
    expect(
      screen.getByRole("listitem", { name: /13000\.00 KRW/i }),
    ).toBeInTheDocument();

    await user.click(
      within(
        screen.getByRole("listitem", { name: /13000\.00 KRW/i }),
      ).getByRole("button", { name: "삭제" }),
    );
    expect(
      screen.queryByRole("listitem", { name: /13000\.00 KRW/i }),
    ).not.toBeInTheDocument();
  });
});
