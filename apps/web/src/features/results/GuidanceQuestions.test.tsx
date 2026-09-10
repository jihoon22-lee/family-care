import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { MedicalEventResponse } from "../../api/generated";
import { GuidanceQuestions } from "./GuidanceQuestions";

const event: MedicalEventResponse = {
  id: "synthetic-event",
  family_member_id: "synthetic-member",
  version: 2,
  deleted: false,
  mode: "post_treatment",
  event_date: null,
  visit_date: null,
  situation: "합성 입원 상황",
  facts: {
    "MedicalEvent.classification": {
      value: "synthetic-classification",
      confirmation: "user",
    },
  },
};
const questions = [
  { field_path: "MedicalEvent.admission_days", reason_code: "MISSING_DAYS" },
];

describe("minimum event questions", () => {
  it.each([
    ["true", true],
    ["false", false],
    ["unknown", null],
  ] as const)(
    "saves reduction answer %s as a user fact without AI structuring",
    async (choice, value) => {
      const save = vi.fn().mockResolvedValue({ ...event, version: 3 });
      render(
        <GuidanceQuestions
          event={event}
          questions={[
            {
              field_path: "MedicalEvent.reduction_applies",
              reason_code: "CALCULATION_INPUT_NEEDED",
            },
          ]}
          onSave={save}
          onAnalyze={vi.fn().mockResolvedValue(undefined)}
        />,
      );
      await userEvent.selectOptions(
        screen.getByLabelText("감액 조건 해당 여부"),
        choice,
      );
      await userEvent.click(
        screen.getByRole("button", { name: "입력 보완 후 다시 계산" }),
      );
      expect(save).toHaveBeenCalledWith(
        {
          expected_version: 2,
          facts: {
            ...event.facts,
            "MedicalEvent.reduction_applies": { value, confirmation: "user" },
          },
        },
        expect.any(AbortSignal),
      );
    },
  );

  it.each([true, false])(
    "preserves both day and reduction answers in question order %s",
    async (reductionFirst) => {
      const save = vi.fn().mockResolvedValue({ ...event, version: 3 });
      const reduction = {
        field_path: "MedicalEvent.reduction_applies",
        reason_code: "CALCULATION_INPUT_NEEDED",
      };
      render(
        <GuidanceQuestions
          event={event}
          questions={
            reductionFirst
              ? [reduction, ...questions]
              : [...questions, reduction]
          }
          onSave={save}
          onAnalyze={vi.fn().mockResolvedValue(undefined)}
        />,
      );
      await userEvent.type(screen.getByLabelText("입원 일수"), "5");
      await userEvent.selectOptions(
        screen.getByLabelText("감액 조건 해당 여부"),
        "false",
      );
      await userEvent.click(
        screen.getByRole("button", { name: "입력 보완 후 다시 계산" }),
      );
      expect(save.mock.calls[0][0]).toEqual({
        expected_version: 2,
        facts: {
          ...event.facts,
          "MedicalEvent.admission_days": { value: 5, confirmation: "user" },
          "MedicalEvent.reduction_applies": {
            value: false,
            confirmation: "user",
          },
        },
      });
    },
  );

  it("keeps an existing Boolean reduction fact while updating only admission days", async () => {
    const current = {
      ...event,
      facts: {
        ...event.facts,
        "MedicalEvent.reduction_applies": {
          value: false,
          confirmation: "user" as const,
        },
      },
    };
    const save = vi.fn().mockResolvedValue({ ...current, version: 3 });
    render(
      <GuidanceQuestions
        event={current}
        questions={questions}
        onSave={save}
        onAnalyze={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    await userEvent.type(screen.getByLabelText("입원 일수"), "5");
    await userEvent.click(
      screen.getByRole("button", { name: "입력 보완 후 다시 계산" }),
    );
    expect(
      save.mock.calls[0][0].facts["MedicalEvent.reduction_applies"],
    ).toEqual({ value: false, confirmation: "user" });
  });

  it.each([true, false])(
    "saves explicit diagnosis confirmation as boolean %s",
    async (value) => {
      const save = vi.fn().mockResolvedValue({ ...event, version: 3 });
      render(
        <GuidanceQuestions
          event={event}
          questions={[
            {
              field_path: "MedicalEvent.diagnosis_confirmed",
              reason_code: "EVENT_FACT_NEEDED",
            },
          ]}
          onSave={save}
          onAnalyze={vi.fn().mockResolvedValue(undefined)}
        />,
      );
      await userEvent.selectOptions(
        screen.getByLabelText("확정 진단 여부"),
        String(value),
      );
      await userEvent.click(
        screen.getByRole("button", { name: "입력 보완 후 다시 계산" }),
      );
      expect(save).toHaveBeenCalledWith(
        expect.objectContaining({
          structured_facts: [{ field_id: "diagnosis_confirmed", value }],
        }),
        expect.any(AbortSignal),
      );
    },
  );

  it("sends both explicit admission and day-count answers without dropping either", async () => {
    const save = vi.fn().mockResolvedValue({ ...event, version: 3 });
    render(
      <GuidanceQuestions
        event={event}
        questions={[
          ...questions,
          {
            field_path: "MedicalEvent.admission",
            reason_code: "MISSING_ADMISSION",
          },
        ]}
        onSave={save}
        onAnalyze={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    await userEvent.type(screen.getByLabelText("입원 일수"), "5");
    await userEvent.selectOptions(screen.getByLabelText("입원 여부"), "true");
    await userEvent.click(
      screen.getByRole("button", { name: "입력 보완 후 다시 계산" }),
    );
    expect(save).toHaveBeenCalledWith(
      expect.objectContaining({
        facts: expect.objectContaining({
          "MedicalEvent.admission_days": { value: 5, confirmation: "user" },
        }),
        structured_facts: [{ field_id: "admission", value: true }],
      }),
      expect.any(AbortSignal),
    );
  });

  it("reapplies the entered answer if the event changed after a saved PATCH and failed analysis", async () => {
    const save = vi
      .fn()
      .mockResolvedValueOnce({ ...event, version: 3 })
      .mockResolvedValueOnce({ ...event, version: 5 });
    const analyze = vi
      .fn()
      .mockRejectedValueOnce(new Error("synthetic offline"))
      .mockResolvedValueOnce(undefined);
    const view = render(
      <GuidanceQuestions
        event={event}
        questions={questions}
        onSave={save}
        onAnalyze={analyze}
      />,
    );
    await userEvent.type(screen.getByLabelText("입원 일수"), "5");
    await userEvent.click(
      screen.getByRole("button", { name: "입력 보완 후 다시 계산" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "입력은 저장했습니다",
    );
    view.rerender(
      <GuidanceQuestions
        event={{ ...event, version: 4 }}
        questions={questions}
        onSave={save}
        onAnalyze={analyze}
      />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "입력 보완 후 다시 계산" }),
    );
    expect(save).toHaveBeenCalledTimes(2);
    expect(save.mock.calls[1][0]).toMatchObject({
      expected_version: 4,
      facts: {
        "MedicalEvent.admission_days": { value: 5, confirmation: "user" },
      },
    });
  });

  it("preserves the current answer and sends the confirmed integer with the expected event version", async () => {
    const save = vi.fn().mockResolvedValue({ ...event, version: 3 });
    const analyze = vi.fn().mockResolvedValue(undefined);
    render(
      <>
        <p>기존 후보 300원</p>
        <GuidanceQuestions
          event={event}
          questions={questions}
          onSave={save}
          onAnalyze={analyze}
        />
      </>,
    );
    await userEvent.type(screen.getByLabelText("입원 일수"), "5");
    expect(screen.getByText("기존 후보 300원")).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "입력 보완 후 다시 계산" }),
    );
    expect(save).toHaveBeenCalledWith(
      {
        expected_version: 2,
        facts: {
          ...event.facts,
          "MedicalEvent.admission_days": { value: 5, confirmation: "user" },
        },
      },
      expect.any(AbortSignal),
    );
    expect(analyze).toHaveBeenCalledTimes(1);
  });

  it("does not submit an invalid day count or turn an unanswered question into zero", async () => {
    const save = vi.fn();
    render(
      <GuidanceQuestions
        event={event}
        questions={questions}
        onSave={save}
        onAnalyze={vi.fn()}
      />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "입력 보완 후 다시 계산" }),
    );
    expect(save).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("입원 일수"), {
      target: { value: "-1" },
    });
    await userEvent.click(
      screen.getByRole("button", { name: "입력 보완 후 다시 계산" }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("0부터 36,500");
    expect(save).not.toHaveBeenCalled();
  });

  it("retries analysis after a network error without repeating the successful PATCH", async () => {
    const save = vi.fn().mockResolvedValue({ ...event, version: 3 });
    const analyze = vi
      .fn()
      .mockRejectedValueOnce(new Error("synthetic offline"))
      .mockResolvedValueOnce(undefined);
    render(
      <GuidanceQuestions
        event={event}
        questions={questions}
        onSave={save}
        onAnalyze={analyze}
      />,
    );
    await userEvent.type(screen.getByLabelText("입원 일수"), "5");
    await userEvent.click(
      screen.getByRole("button", { name: "입력 보완 후 다시 계산" }),
    );
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "입력은 저장했습니다",
      ),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "입력 보완 후 다시 계산" }),
    );
    expect(save).toHaveBeenCalledTimes(1);
    expect(analyze).toHaveBeenCalledTimes(2);
  });
});
