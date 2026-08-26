import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { BatchResponse } from "../../api/generated";
import { renderWithProviders } from "../../test/renderWithProviders";
import { BatchProgress } from "./BatchProgress";

const BATCH: BatchResponse = {
  batch_id: "00000000-0000-4000-8000-000000000201",
  family_member_id: "00000000-0000-4000-8000-000000000101",
  items: [
    {
      attempts: 1,
      display_label: "Sample policy document A.pdf",
      error_code: null,
      ocr_pages_processed: 2,
      ocr_state: "running",
      ocr_warning_codes: [],
      source_id: "a".repeat(64),
      state: "running",
    },
    {
      attempts: 1,
      display_label: "Sample policy document B.pdf",
      error_code: null,
      ocr_pages_processed: 3,
      ocr_state: "warning",
      ocr_warning_codes: ["NO_TEXT_DETECTED"],
      source_id: "b".repeat(64),
      state: "succeeded",
    },
  ],
  schema_version: "1",
  state: "partial",
};

describe("BatchProgress OCR projection", () => {
  it("renders bounded OCR states and page progress as text only", () => {
    renderWithProviders(<BatchProgress batch={BATCH} onCancel={vi.fn()} />);

    expect(screen.getByText("OCR 상태: OCR 처리 중")).toBeInTheDocument();
    expect(screen.getByText("OCR 상태: OCR 확인 필요")).toBeInTheDocument();
    expect(screen.getByText("OCR 처리 페이지 2")).toBeInTheDocument();
    expect(screen.getByText("OCR 처리 페이지 3")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(
      /ocr_text|stderr|bbox|image_path|raw_error/i,
    );
  });
});
