import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  KnowledgeContractDetailResponse,
  KnowledgeContractPageResponse,
  MemberInsuranceDocumentInventoryResponse,
  MemberInsuranceReconciliationResponse,
} from "../../api/generated";
import { jsonResponse } from "../../test/mockApi";
import { renderWithProviders } from "../../test/renderWithProviders";
import { InsuranceDocumentInventory } from "./InsuranceDocumentInventory";
import { PrivateInsuranceCatalog } from "./PrivateInsuranceCatalog";

const MEMBER_ID = "00000000-0000-4000-8000-000000002001";
const CONTRACT_ID = "00000000-0000-4000-8000-000000002002";
const SUBJECT_ID = "00000000-0000-4000-8000-000000002003";
const COVERAGE_ID = "00000000-0000-4000-8000-000000002004";
const SECTION_ID = "00000000-0000-4000-8000-000000002005";
const POLICY_ID = "00000000-0000-4000-8000-000000002010";
const ORPHAN_POLICY_ID = "00000000-0000-4000-8000-000000002011";
const FAILED_ITEM_ID = "00000000-0000-4000-8000-000000002012";
const REOPENED_RESOLUTION_ID = "00000000-0000-4000-8000-000000002013";

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

const RECONCILIATION: MemberInsuranceReconciliationResponse = {
  contracts: [
    {
      certificate_decision: "MATCH",
      current_status: "active",
      document_readiness: {
        completeness: "CERTIFICATE_AND_TERMS",
        has_application: false,
        has_product_explanation: true,
        policy_contract_id: POLICY_ID,
      },
      insurer_display: "Sample Insurer",
      knowledge_contract_id: CONTRACT_ID,
      operational_link: {
        authority: "USER_CONFIRMED_OPERATIONAL_IDENTITY",
        confirmed_at: "2026-09-01T01:00:00Z",
        conflict: false,
        decision: "MATCH",
        id: "00000000-0000-4000-8000-000000002014",
        policy_contract_id: POLICY_ID,
        reason_code: "USER_CONFIRMED_SAME_CONTRACT",
      },
      product_display: "Sample Complete Policy",
      reconciliation_state: "EVIDENCE_READY",
    },
    {
      certificate_decision: "MATCH",
      current_status: "active",
      document_readiness: null,
      insurer_display: "Sample Insurer B",
      knowledge_contract_id: "00000000-0000-4000-8000-000000002006",
      operational_link: {
        authority: null,
        confirmed_at: null,
        conflict: false,
        decision: "UNKNOWN",
        id: null,
        policy_contract_id: null,
        reason_code: "NO_EXACT_BINDING",
      },
      product_display: "Sample Secondary Policy",
      reconciliation_state: "LINK_REVIEW_REQUIRED",
    },
  ],
  generated_at: "2026-09-01T01:02:03Z",
  knowledge_run_id: "00000000-0000-4000-8000-000000002015",
  member_id: MEMBER_ID,
  orphan_operational_contracts: [
    {
      completeness: "CERTIFICATE_ONLY",
      insurer_display: "Sample Insurer B",
      policy_contract_id: ORPHAN_POLICY_ID,
      product_display: "Sample App-only Policy",
      status: "unknown",
    },
  ],
  schema_version: "1",
  summary: {
    conflict_contracts: 0,
    documents_pending_contracts: 0,
    evidence_ready_contracts: 1,
    link_review_required_contracts: 1,
    orphan_operational_contracts: 1,
    total_contracts: 2,
    unresolved_unreadable_sources: 1,
  },
  unresolved_sources: [
    {
      current_resolution_id: REOPENED_RESOLUTION_ID,
      display_label: "보험증권 문서",
      document_batch_item_id: FAILED_ITEM_ID,
      processing_state: "PASSWORD_REQUIRED",
      source_kind: "policy",
    },
  ],
};

const EMPTY_INVENTORY: MemberInsuranceDocumentInventoryResponse = {
  member_id: MEMBER_ID,
  registered_policies: [],
  schema_version: "1",
  summary: {
    application_documents: 0,
    certificate_and_terms: 0,
    certificate_backed_policies: 0,
    certificate_only: 0,
    pairing_conflicts: 0,
    product_explanation_documents: 0,
    terms_only_documents: 0,
    unreadable_documents: 0,
  },
  unpaired_components: [],
  unreadable_sources: [],
  unregistered_document_sets: [],
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
  it("uses one closed reconciliation summary and expands clause-grounded analysis", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        void init;
        const url = new URL(String(input), window.location.origin);
        if (
          url.pathname ===
          `/api/v1/family-members/${MEMBER_ID}/insurance-reconciliation`
        ) {
          return jsonResponse(RECONCILIATION);
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
    expect(screen.getByText("분석 계약 2건")).toBeInTheDocument();
    expect(screen.getByText("근거 준비")).toBeInTheDocument();
    expect(screen.getByText("문서 보완")).toBeInTheDocument();
    expect(screen.getByText("연결 검토")).toBeInTheDocument();
    expect(screen.getByText("충돌")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Sample Complete Policy" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Sample Secondary Policy" }),
    ).toBeInTheDocument();
    expect(screen.getByText("청구 근거 준비")).toBeInTheDocument();
    expect(screen.getByText("앱 계약 연결 검토")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "앱 원장 단독 계약" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Sample App-only Policy")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "판독·해결 작업" }),
    ).toBeInTheDocument();
    expect(screen.getByText("보험증권 문서")).toBeInTheDocument();

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

  it("links only the explicitly selected orphan policy and reloads both views", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input), window.location.origin);
        if (url.pathname.endsWith("/operational-link")) {
          return jsonResponse({
            authority: "USER_CONFIRMED_OPERATIONAL_IDENTITY",
            confirmed_at: "2026-09-01T01:03:00Z",
            conflict: false,
            decision: "MATCH",
            id: "00000000-0000-4000-8000-000000002016",
            knowledge_contract_id: "00000000-0000-4000-8000-000000002006",
            policy_contract_id: ORPHAN_POLICY_ID,
            reason_code: "USER_CONFIRMED_SAME_CONTRACT",
            schema_version: "1",
          });
        }
        if (
          url.pathname ===
          `/api/v1/family-members/${MEMBER_ID}/insurance-reconciliation`
        ) {
          return jsonResponse(RECONCILIATION);
        }
        if (
          url.pathname ===
          `/api/v1/family-members/${MEMBER_ID}/insurance-document-inventory`
        ) {
          return jsonResponse(EMPTY_INVENTORY);
        }
        void init;
        return jsonResponse({ error_code: "NOT_FOUND" }, 404);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(
      <>
        <PrivateInsuranceCatalog memberId={MEMBER_ID} />
        <InsuranceDocumentInventory memberId={MEMBER_ID} />
      </>,
    );

    const selector = await screen.findByRole("combobox", {
      name: "Sample Secondary Policy 앱 계약 선택",
    });
    await user.selectOptions(selector, ORPHAN_POLICY_ID);
    await user.click(
      screen.getByRole("button", {
        name: "Sample Secondary Policy 선택한 앱 계약과 같은 계약으로 확인",
      }),
    );

    await waitFor(() => {
      const paths = fetchMock.mock.calls.map(([input]) => String(input));
      expect(
        paths.filter((path) => path.includes("insurance-reconciliation")),
      ).toHaveLength(2);
      expect(
        paths.filter((path) => path.includes("insurance-document-inventory")),
      ).toHaveLength(2);
    });
    const mutation = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/operational-link"),
    );
    expect(mutation?.[0]).toBe(
      "/api/v1/private-knowledge/current/contracts/00000000-0000-4000-8000-000000002006/operational-link",
    );
    expect(JSON.parse(String(mutation?.[1]?.body))).toEqual({
      conflict: false,
      decision: "MATCH",
      expected_current_link_id: null,
      policy_contract_id: ORPHAN_POLICY_ID,
      reason_code: "USER_CONFIRMED_SAME_CONTRACT",
    });
  });

  it("dismisses a reviewed stale document task with its exact current resolution", async () => {
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input), window.location.origin);
        if (
          url.pathname ===
          `/api/v1/document-batch-items/${FAILED_ITEM_ID}/resolution`
        ) {
          return jsonResponse({
            authority: "USER_CONFIRMED_DOCUMENT_RESOLUTION",
            confirmed_at: "2026-09-01T01:04:00Z",
            failed_item_id: FAILED_ITEM_ID,
            id: "00000000-0000-4000-8000-000000002017",
            reason_code: "USER_DISMISSED_STALE_FAILURE",
            replacement_item_id: null,
            resolution: "DISMISSED",
            schema_version: "1",
          });
        }
        if (
          url.pathname ===
          `/api/v1/family-members/${MEMBER_ID}/insurance-reconciliation`
        ) {
          return jsonResponse(RECONCILIATION);
        }
        void init;
        return jsonResponse({ error_code: "NOT_FOUND" }, 404);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<PrivateInsuranceCatalog memberId={MEMBER_ID} />);

    await user.click(
      await screen.findByRole("button", {
        name: "보험증권 문서 검토 완료로 작업에서 제외",
      }),
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      expected_current_resolution_id: REOPENED_RESOLUTION_ID,
      reason_code: "USER_DISMISSED_STALE_FAILURE",
      replacement_item_id: null,
      resolution: "DISMISSED",
    });
  });

  it("revalidates the integrated projection on focus and explicit refresh", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(RECONCILIATION));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<PrivateInsuranceCatalog memberId={MEMBER_ID} />);

    await screen.findByText("분석 계약 2건");
    window.dispatchEvent(new Event("focus"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await user.click(
      screen.getByRole("button", { name: "통합 현황 새로고침" }),
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
  });
});
