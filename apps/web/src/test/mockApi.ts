import type {
  CandidateEvidenceRef,
  CandidateField,
  FamilyMemberResponse,
  PolicyResponse,
  PolicyReviewItem,
  RiderResponse,
} from "../api/generated";

export interface SyntheticLedgerFixture {
  familyMembers: FamilyMemberResponse[];
  policies: PolicyResponse[];
  ridersByPolicy: Record<string, RiderResponse[]>;
  reviewItems: PolicyReviewItem[];
  reviewItemsByMember?: Record<string, PolicyReviewItem[]>;
}

const SYNTHETIC_HASH = "a".repeat(64);

const policyEvidence = {
  bbox: [72, 120, 480, 180] as [number, number, number, number],
  content_sha256: SYNTHETIC_HASH,
  document_version_id: "synthetic-policy-document-version-001",
  evidence_id: "synthetic-policy-evidence-001",
  physical_page: 1,
  review_state: "AI_VERIFIED" as const,
};

const secondaryPolicyEvidence = {
  ...policyEvidence,
  document_version_id: "synthetic-policy-document-version-002",
  evidence_id: "synthetic-policy-evidence-002",
};

const termsEvidence: CandidateEvidenceRef = {
  bbox: null,
  bounded_excerpt: "A synthetic terms record mentions an optional benefit.",
  document_label: "Sample Terms",
  document_version_id: "synthetic-terms-document-version-001",
  evidence_id: "synthetic-terms-evidence-001",
  page: 1,
};

const termsOnlyFields: CandidateField[] = [
  {
    evidence_ids: [termsEvidence.evidence_id],
    field_id: "rider_name",
    value: "Terms-only Rider",
  },
  {
    evidence_ids: [termsEvidence.evidence_id],
    field_id: "rider_status",
    value: "active",
  },
];

const sampleFamilyMembers: FamilyMemberResponse[] = [
  {
    deleted: false,
    display_name: "Family Member A",
    id: "synthetic-member-a",
    internal_alias: "member-a",
    version: 1,
  },
  {
    deleted: false,
    display_name: "Family Member B",
    id: "synthetic-member-b",
    internal_alias: "member-b",
    version: 1,
  },
];

const samplePolicies: PolicyResponse[] = [
  {
    contract_date: "2026-01-01",
    coverage_end_date: "2026-12-31",
    coverage_start_date: "2026-01-01",
    deleted: false,
    id: "synthetic-policy-001",
    insurer_display: "Sample Insurer",
    insurer_key: "sample-insurer",
    parties: [
      {
        effective_from: "2026-01-01",
        effective_to: null,
        evidence: policyEvidence,
        family_member_id: "synthetic-member-a",
        id: "synthetic-party-001",
        role: "primary_insured",
        version: 1,
      },
    ],
    product_display: "Sample Policy",
    product_key: "sample-policy",
    source_document_version_id: policyEvidence.document_version_id,
    source_evidence: policyEvidence,
    status: "active",
    status_evidence: policyEvidence,
    version: 1,
  },
  {
    contract_date: "2026-02-01",
    coverage_end_date: "2026-12-31",
    coverage_start_date: "2026-02-01",
    deleted: false,
    id: "synthetic-policy-002",
    insurer_display: "Sample Insurer B",
    insurer_key: "sample-insurer-b",
    parties: [
      {
        effective_from: "2026-02-01",
        effective_to: null,
        evidence: secondaryPolicyEvidence,
        family_member_id: "synthetic-member-b",
        id: "synthetic-party-002",
        role: "primary_insured",
        version: 1,
      },
    ],
    product_display: "Sample Policy B",
    product_key: "sample-policy-b",
    source_document_version_id: secondaryPolicyEvidence.document_version_id,
    source_evidence: secondaryPolicyEvidence,
    status: "unknown",
    status_evidence: null,
    version: 1,
  },
];

const sampleRiders: Record<string, RiderResponse[]> = {
  "synthetic-policy-001": [
    {
      benefit_type: "fixed",
      coverage_end_date: "2026-12-31",
      coverage_start_date: "2026-01-01",
      currency: "SYN",
      display_name: "Sample Hospital Benefit",
      id: "synthetic-rider-001",
      insured_amount: "1000",
      normalized_key: "sample-hospital-benefit",
      policy_contract_id: "synthetic-policy-001",
      renewable: false,
      source_evidence: policyEvidence,
      status: "active",
      status_evidence: policyEvidence,
      version: 1,
    },
  ],
  "synthetic-policy-002": [
    {
      benefit_type: "indemnity",
      coverage_end_date: "2026-12-31",
      coverage_start_date: "2026-02-01",
      currency: "SYN",
      display_name: "Sample Travel Benefit",
      id: "synthetic-rider-002",
      insured_amount: null,
      normalized_key: "sample-travel-benefit",
      policy_contract_id: "synthetic-policy-002",
      renewable: null,
      source_evidence: secondaryPolicyEvidence,
      status: "unknown",
      status_evidence: null,
      version: 1,
    },
  ],
};

const sampleReviewItems: PolicyReviewItem[] = [
  {
    aggregate_id: "synthetic-policy-001",
    candidate_kind: "rider",
    candidate_version_id: "synthetic-candidate-version-001",
    evidence: [termsEvidence],
    expected_version: 1,
    fields: termsOnlyFields,
    issues: [{ code: "TERMS_ONLY_RIDER", field_id: "rider_name" }],
    review_item_id: "synthetic-review-item-001",
    status: "NEEDS_REVIEW",
  },
];

export const SYNTHETIC_LEDGER: SyntheticLedgerFixture = {
  familyMembers: sampleFamilyMembers,
  policies: samplePolicies,
  ridersByPolicy: sampleRiders,
  reviewItems: sampleReviewItems,
};

export const SYNTHETIC_EMPTY_LEDGER: SyntheticLedgerFixture = {
  familyMembers: [],
  policies: [],
  ridersByPolicy: {},
  reviewItems: [],
};

export function jsonResponse(
  body: unknown,
  status = 200,
  headers: HeadersInit = {},
): Response {
  return new Response(JSON.stringify(body), {
    headers: {
      "cache-control": "no-store",
      "content-type": "application/json",
      ...headers,
    },
    status,
  });
}

export function createMockApi(
  fixture: SyntheticLedgerFixture = SYNTHETIC_LEDGER,
): (input: RequestInfo | URL, init?: RequestInit) => Promise<Response> {
  return async (input) => {
    const inputUrl =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url;
    const url = new URL(inputUrl, window.location.origin);

    if (url.pathname === "/api/v1/family-members") {
      return jsonResponse(fixture.familyMembers);
    }

    if (url.pathname === "/api/v1/policies") {
      return jsonResponse(fixture.policies);
    }

    const ridersMatch = url.pathname.match(
      /^\/api\/v1\/policies\/([^/]+)\/riders$/,
    );
    if (ridersMatch) {
      return jsonResponse(fixture.ridersByPolicy[ridersMatch[1]] ?? []);
    }

    if (url.pathname === "/api/v1/review-items") {
      const memberId = url.searchParams.get("family_member_id");
      return jsonResponse(
        memberId && fixture.reviewItemsByMember
          ? (fixture.reviewItemsByMember[memberId] ?? [])
          : fixture.reviewItems,
      );
    }

    if (url.pathname === "/api/v1/private-knowledge/current/contracts") {
      return jsonResponse({
        items: [],
        next_cursor: null,
        schema_version: "1",
      });
    }

    const inventoryMatch = url.pathname.match(
      /^\/api\/v1\/family-members\/([^/]+)\/insurance-document-inventory$/,
    );
    if (inventoryMatch) {
      return jsonResponse({
        member_id: inventoryMatch[1],
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
        unregistered_document_sets: [],
        unreadable_sources: [],
      });
    }

    return jsonResponse(
      { error_code: "NOT_FOUND", message: "synthetic route not found" },
      404,
    );
  };
}
