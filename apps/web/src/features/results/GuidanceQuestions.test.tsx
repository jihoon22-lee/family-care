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
