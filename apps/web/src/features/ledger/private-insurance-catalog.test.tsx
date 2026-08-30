import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  KnowledgeContractDetailResponse,
  KnowledgeContractPageResponse,
} from "../../api/generated";
import { jsonResponse } from "../../test/mockApi";
import { renderWithProviders } from "../../test/renderWithProviders";
import { PrivateInsuranceCatalog } from "./PrivateInsuranceCatalog";

const MEMBER_ID = "00000000-0000-4000-8000-000000002001";
const CONTRACT_ID = "00000000-0000-4000-8000-000000002002";
const SUBJECT_ID = "00000000-0000-4000-8000-000000002003";
const COVERAGE_ID = "00000000-0000-4000-8000-000000002004";
const SECTION_ID = "00000000-0000-4000-8000-000000002005";

const PAGE: KnowledgeContractPageResponse = {
  items: [
    {
      certificate_decision: "MATCH",
      contract_document_completeness: "CERTIFICATE_REVIEW_REQUIRED_AND_TERMS",
      contract_end: null,
      contract_start: "2024-01-01",
      coverage_count: 2,
      current_status: "active",
      current_status_as_of: "2026-08-30",
      current_status_authority: "USER_CONFIRMED_CURRENT_ENROLLMENT",
      current_status_decision: "MATCH",
      document_identity_decision: "MATCH",
      edition_applicability_decision: "UNKNOWN",
      enrollment_match_count: 2,
      enrollment_no_match_count: 0,
      enrollment_unknown_count: 0,
      family_alias: "Family Member A",
      family_member_id: MEMBER_ID,
      id: CONTRACT_ID,
      insurer_display: "Sample Insurer",
      product_display: "Sample Complete Policy",
      semantic_fact_count: 1,
      semantic_section_count: 1,
      subject_binding_decision: "MATCH",
      subject_id: SUBJECT_ID,
      terms_overall_decision: "UNKNOWN",
      terms_source_count: 1,
    },
    {
      certificate_decision: "MATCH",
      contract_document_completeness: "CERTIFICATE_AND_TERMS",
      contract_end: null,
      contract_start: "2025-01-01",
      coverage_count: 1,
      current_status: "active",
      current_status_as_of: "2026-08-30",
      current_status_authority: "USER_CONFIRMED_CURRENT_ENROLLMENT",
      current_status_decision: "MATCH",
      document_identity_decision: "MATCH",
      edition_applicability_decision: "MATCH",
      enrollment_match_count: 1,
      enrollment_no_match_count: 0,
      enrollment_unknown_count: 0,
      family_alias: "Family Member A",
      family_member_id: MEMBER_ID,
      id: "00000000-0000-4000-8000-000000002006",
      insurer_display: "Sample Insurer B",
      product_display: "Sample Secondary Policy",
      semantic_fact_count: 0,
      semantic_section_count: 0,
      subject_binding_decision: "MATCH",
      subject_id: SUBJECT_ID,
      terms_overall_decision: "MATCH",
      terms_source_count: 1,
    },
  ],
  next_cursor: null,
  schema_version: "1",
};

const DETAIL: KnowledgeContractDetailResponse = {
  contract: PAGE.items[0],
  coverage_mappings: [
    {
      coverage_id: COVERAGE_ID,
      document_identity_decision: "MATCH",
      edition_applicability_decision: "UNKNOWN",
      enrollment_decision: "MATCH",
      executable: false,
      mapping_applicability: "APPLICABLE",
      overall_decision: "UNKNOWN",
      reason_codes: ["SYNTHETIC_EDITION_UNCONFIRMED"],
      section_mapping_decision: "MATCH",
      terms_section_id: SECTION_ID,
    },
  ],
  coverages: [
    {
      benefit_type: "FIXED",
      component_classification: "BENEFIT_COVERAGE",
      component_role: "RIDER",
      coverage_end: null,
      coverage_start: "2024-01-01",
      currency: "KRW",
      current_status: "unknown",
      display_name: "Sample Hospital Benefit",
      enrollment_decision: "MATCH",
      id: COVERAGE_ID,
      insured_amount: "10000.0000",
      renewal_state: "UNKNOWN",
    },
  ],
  next_section_cursor: null,
  schema_version: "1",
  terms_assignments: [
    {
      document_identity_decision: "MATCH",
      edition_applicability_decision: "UNKNOWN",
      id: "00000000-0000-4000-8000-000000002007",
      overall_decision: "UNKNOWN",
      reason_codes: ["SYNTHETIC_EDITION_UNCONFIRMED"],
      selected_source_count: 1,
    },
  ],
  terms_sections: [
    {
      confidence: "high",
      facts: [
        {
          citations: [
            {
              clause_label: "Synthetic clause 1",
              clause_title: "Sample payment condition",
              page_end: 2,
              page_start: 2,
              source_document_ref: "00000000-0000-4000-8000-000000002008",
            },
          ],
          conditions: {
            confidence: "high",
            decision_impact: "establishes_payment_trigger",
            details_ko: ["Synthetic condition detail."],
            unresolved_reference: false,
          },
          executable: false,
          fact_type: "PAYMENT_TRIGGER",
          id: "00000000-0000-4000-8000-000000002009",
          numeric_terms: [],
          review_state: "DIRECT_REVIEWED",
          statement: "Synthetic clause-grounded payment condition.",
        },
      ],
      found_categories: ["payment_reason"],
      heading: "Sample Benefit Section",
      id: SECTION_ID,
      missing_categories: ["edition_effective_date"],
      page_end: 2,
      page_start: 2,
      review_state: "DIRECT_REVIEWED",
      section_summary: "Synthetic reviewed section summary.",
      warnings: ["Synthetic edition applicability remains unknown."],
    },
  ],
};

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("complete private insurance catalog", () => {
  it("shows every member contract and expands clause-grounded analysis", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        void init;
        const url = new URL(String(input), window.location.origin);
        if (
          url.pathname === "/api/v1/private-knowledge/current/contracts" &&
          url.searchParams.get("family_member_id") === MEMBER_ID
        ) {
          return jsonResponse(PAGE);
        }
        if (
          url.pathname ===
          `/api/v1/private-knowledge/current/contracts/${CONTRACT_ID}`
        ) {
          return jsonResponse(DETAIL);
        }
        return jsonResponse({ error_code: "NOT_FOUND" }, 404);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<PrivateInsuranceCatalog memberId={MEMBER_ID} />);

    expect(
      await screen.findByRole("heading", { name: "전체 가입 보험 분석" }),
    ).toBeInTheDocument();
    expect(screen.getByText("총 2건")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Sample Complete Policy" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Sample Secondary Policy" }),
    ).toBeInTheDocument();
    expect(screen.getByText("증권+약관")).toBeInTheDocument();
    expect(screen.getByText("증권 열람 확인 필요+약관")).toBeInTheDocument();
    expect(screen.getAllByText("현재 가입 확인 · 2026-08-30")).toHaveLength(2);
    expect(screen.getByText("가입 담보 2개")).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", {
        name: "Sample Complete Policy 상세 분석 보기",
      }),
    );

    expect(
      await screen.findByRole("heading", { name: "Sample Hospital Benefit" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Synthetic clause-grounded payment condition."),
    ).toBeInTheDocument();
    expect(screen.getByText("Synthetic clause 1 · 2쪽")).toBeInTheDocument();
    expect(
      screen.getByText("약관 판본 적용성은 추가 확인이 필요합니다."),
    ).toBeInTheDocument();

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    for (const [, init] of fetchMock.mock.calls) {
      expect(init).toMatchObject({
        cache: "no-store",
        credentials: "include",
      });
    }
  });
});
