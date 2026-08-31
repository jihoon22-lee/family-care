import { describe, expect, it } from "vitest";

import { reasonLabel } from "./resultPresentation";

describe("result reason labels", () => {
  it.each([
    [
      "COVERAGE_PUBLICATION_ADVISORY",
      "가입 담보와 관련 약관을 검색할 수 있지만 자동 판정 규칙은 아직 완전하지 않습니다.",
    ],
    [
      "COVERAGE_PUBLICATION_BLOCKED",
      "이전 실행 항목에 예외가 남아 있어 별도 확인이 필요합니다.",
    ],
    ["EVENT_DATE_STATUS_UNCONFIRMED", "사건일의 계약 상태를 확인해야 합니다."],
    [
      "EVENT_DATE_OUTSIDE_CONTRACT_TERM",
      "사건일이 확인된 계약 기간에 포함되지 않습니다.",
    ],
    [
      "CONTRACT_TERMS_TOKEN_OVERLAP",
      "같은 계약의 약관에서 찾은 후보이며, 이 담보에 직접 적용된다는 뜻은 아닙니다.",
    ],
  ])("renders a bounded Korean explanation for %s", (code, label) => {
    expect(reasonLabel(code)).toBe(label);
  });
});
