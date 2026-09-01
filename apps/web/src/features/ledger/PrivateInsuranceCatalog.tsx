import { useEffect, useState } from "react";

import type {
  ContractReconciliationResponse,
  KnowledgeContractDetailResponse,
  KnowledgeFactResponse,
} from "../../api/generated";
import {
  confirmDocumentResolution,
  confirmOperationalLink,
} from "../../api/insurance-reconciliation";
import { getPrivateInsuranceContract } from "../../api/private-insurance-catalog";
import { useInsuranceReconciliation } from "./useInsuranceReconciliation";

const FACT_LABELS: Record<KnowledgeFactResponse["fact_type"], string> = {
  AMOUNT: "금액 기준",
  CROSS_REFERENCE: "연결 조항",
  DEFINITION: "정의",
  EXCLUSION: "면책·제외",
  FREQUENCY: "횟수 제한",
  OTHER: "기타 조건",
  PAYMENT_TRIGGER: "지급 사유",
  REDUCTION: "감액",
  RENEWAL: "갱신",
  REQUIRED_DOCUMENT: "청구 서류",
  TERMINATION: "보장 종료",
  WAITING_PERIOD: "대기 기간",
};

function pageCopy(start: number, end: number): string {
  return start === end ? `${start}쪽` : `${start}–${end}쪽`;
}

const CONTRACT_STATE_COPY: Record<
  ContractReconciliationResponse["reconciliation_state"],
  string
> = {
  CONFLICT: "연결 충돌",
  DOCUMENTS_PENDING: "필수 문서 보완",
  EVIDENCE_READY: "청구 근거 준비",
  LINK_REVIEW_REQUIRED: "앱 계약 연결 검토",
};

const CURRENT_STATUS_COPY: Record<
  ContractReconciliationResponse["current_status"],
  string
> = {
  active: "현재 유효",
  inactive: "현재 비활성",
  lapsed: "효력 상실",
  terminated: "종료",
  unknown: "현재 상태 확인 필요",
};

const PROCESSING_COPY = {
  FAILED: "판독 실패",
  OCR_REQUIRED: "OCR 필요",
  PASSWORD_REQUIRED: "암호 해제 필요",
} as const;

function factCitationCopy(fact: KnowledgeFactResponse): string {
  const citation = fact.citations[0];
  if (!citation) return "근거 위치 확인 필요";
  const clause = citation.clause_label ?? citation.clause_title ?? "약관 조항";
  return `${clause} · ${pageCopy(citation.page_start, citation.page_end)}`;
}

function ContractAnalysis({
  detail,
}: {
  detail: KnowledgeContractDetailResponse;
}) {
  return (
    <div className="private-catalog-analysis">
      <section aria-label="가입 담보">
        <h4>증권에서 확인한 가입 담보</h4>
        {detail.coverages.length ? (
          <div className="private-catalog-coverages">
            {detail.coverages.map((coverage) => (
              <article key={coverage.id}>
                <h5>{coverage.display_name}</h5>
                <p>
                  {coverage.enrollment_decision === "MATCH"
                    ? "증권 가입 확인"
                    : "가입 근거 추가 확인 필요"}
                  {" · "}
                  {coverage.benefit_type === "INDEMNITY"
                    ? "실손형"
                    : coverage.benefit_type === "FIXED"
                      ? "정액형"
                      : "보장 유형 확인 필요"}
                </p>
                {coverage.insured_amount && coverage.currency ? (
                  <p>
                    가입금액{" "}
                    {Number(coverage.insured_amount).toLocaleString("ko-KR")}
                    {coverage.currency === "KRW"
                      ? "원"
                      : ` ${coverage.currency}`}
                  </p>
                ) : null}
              </article>
            ))}
          </div>
        ) : (
          <p>담보 표를 추가 확인해야 합니다.</p>
        )}
      </section>

      <section aria-label="약관 분석">
        <h4>약관 조항 분석</h4>
        {detail.contract.edition_applicability_decision !== "MATCH" ? (
          <p className="private-catalog-caution">
            약관 판본 적용성은 추가 확인이 필요합니다.
          </p>
        ) : null}
        {detail.terms_sections.length ? (
          <div className="private-catalog-sections">
            {detail.terms_sections.map((section) => (
              <article key={section.id}>
                <header>
                  <h5>{section.heading}</h5>
                  <span>{pageCopy(section.page_start, section.page_end)}</span>
                </header>
                <p>{section.section_summary}</p>
                {section.facts.length ? (
                  <dl>
                    {section.facts.map((fact) => (
                      <div key={fact.id}>
                        <dt>{FACT_LABELS[fact.fact_type]}</dt>
                        <dd>
                          <p>{fact.statement}</p>
                          <small>{factCitationCopy(fact)}</small>
                        </dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <p>직접 근거가 확인된 구조화 사실이 없습니다.</p>
                )}
                {section.warnings.map((warning) => (
                  <p className="private-catalog-caution" key={warning}>
                    {warning}
                  </p>
                ))}
              </article>
            ))}
          </div>
        ) : (
          <p>인용 가능한 약관 조항을 추가 확인해야 합니다.</p>
        )}
      </section>
    </div>
  );
}

export function PrivateInsuranceCatalog({ memberId }: { memberId?: string }) {
  const { data, error, loading, reload } = useInsuranceReconciliation(memberId);
  const [expandedId, setExpandedId] = useState<string>();
  const [details, setDetails] = useState<
    Record<string, KnowledgeContractDetailResponse>
  >({});
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState(false);
  const [selectedPolicies, setSelectedPolicies] = useState<
    Readonly<Record<string, string>>
  >({});
  const [pendingAction, setPendingAction] = useState<string>();
  const [mutationError, setMutationError] = useState(false);

  useEffect(() => {
    setExpandedId(undefined);
    setDetails({});
    setDetailError(false);
    setSelectedPolicies({});
    setMutationError(false);
  }, [memberId]);

  useEffect(() => {
    if (!data || data.orphan_operational_contracts.length === 0) return;
    const policyIds = new Set(
      data.orphan_operational_contracts.map(
        (policy) => policy.policy_contract_id,
      ),
    );
    const firstPolicyId =
      data.orphan_operational_contracts[0]?.policy_contract_id;
    if (!firstPolicyId) return;
    setSelectedPolicies((current) => {
      const next = { ...current };
      let changed = false;
      for (const contract of data.contracts) {
        if (contract.reconciliation_state !== "LINK_REVIEW_REQUIRED") continue;
        if (
          !next[contract.knowledge_contract_id] ||
          !policyIds.has(next[contract.knowledge_contract_id]!)
        ) {
          next[contract.knowledge_contract_id] = firstPolicyId;
          changed = true;
        }
      }
      return changed ? next : current;
    });
  }, [data]);

  useEffect(() => {
    if (!expandedId || details[expandedId]) return;
    const controller = new AbortController();
    setDetailLoading(true);
    setDetailError(false);
    getPrivateInsuranceContract(expandedId, controller.signal)
      .then((detail) => {
        setDetails((current) => ({ ...current, [expandedId]: detail }));
      })
      .catch((reason: unknown) => {
        if (!(reason instanceof DOMException && reason.name === "AbortError"))
          setDetailError(true);
      })
      .finally(() => setDetailLoading(false));
    return () => controller.abort();
  }, [details, expandedId]);

  async function linkSelected(
    contract: ContractReconciliationResponse,
  ): Promise<void> {
    const policyId = selectedPolicies[contract.knowledge_contract_id];
    if (!policyId) return;
    setMutationError(false);
    setPendingAction(`link:${contract.knowledge_contract_id}`);
    try {
      await confirmOperationalLink(contract.knowledge_contract_id, {
        conflict: false,
        decision: "MATCH",
        expected_current_link_id: contract.operational_link.id,
        policy_contract_id: policyId,
        reason_code: "USER_CONFIRMED_SAME_CONTRACT",
      });
      reload();
    } catch {
      setMutationError(true);
    } finally {
      setPendingAction(undefined);
    }
  }

  async function markDistinct(
    contract: ContractReconciliationResponse,
  ): Promise<void> {
    setMutationError(false);
    setPendingAction(`distinct:${contract.knowledge_contract_id}`);
    try {
      await confirmOperationalLink(contract.knowledge_contract_id, {
        conflict: false,
        decision: "NO_MATCH",
        expected_current_link_id: contract.operational_link.id,
        policy_contract_id: null,
        reason_code: "USER_CONFIRMED_DISTINCT_CONTRACT",
      });
      reload();
    } catch {
      setMutationError(true);
    } finally {
      setPendingAction(undefined);
    }
  }

  async function reopenLinkReview(
    contract: ContractReconciliationResponse,
  ): Promise<void> {
    setMutationError(false);
    setPendingAction(`reopen:${contract.knowledge_contract_id}`);
    try {
      await confirmOperationalLink(contract.knowledge_contract_id, {
        conflict: false,
        decision: "UNKNOWN",
        expected_current_link_id: contract.operational_link.id,
        policy_contract_id: null,
        reason_code: "USER_REOPENED_OPERATIONAL_REVIEW",
      });
      reload();
    } catch {
      setMutationError(true);
    } finally {
      setPendingAction(undefined);
    }
  }

  async function dismissDocumentTask(
    source: NonNullable<typeof data>["unresolved_sources"][number],
  ): Promise<void> {
    if (
      !window.confirm(
        "이 실패 이력을 현재 작업 목록에서 제외할까요? 원본 감사 이력은 삭제되지 않습니다.",
      )
    )
      return;
    setMutationError(false);
    setPendingAction(`dismiss:${source.document_batch_item_id}`);
    try {
      await confirmDocumentResolution(source.document_batch_item_id, {
        expected_current_resolution_id: source.current_resolution_id,
        reason_code: "USER_DISMISSED_STALE_FAILURE",
        replacement_item_id: null,
        resolution: "DISMISSED",
      });
      reload();
    } catch {
      setMutationError(true);
    } finally {
      setPendingAction(undefined);
    }
  }

  return (
    <section
      aria-labelledby="private-insurance-catalog-title"
      className="private-insurance-catalog"
    >
      <header className="private-catalog-heading">
        <div>
          <p className="eyebrow">증권·약관 전수 분석</p>
          <h2 id="private-insurance-catalog-title">전체 가입 보험 분석</h2>
          <p>
            전체 분석 계약을 기준으로 앱 원장 연결과 문서 준비 상태를 한 번에
            대사합니다.
          </p>
        </div>
        <div className="reconciliation-heading-actions">
          <strong>분석 계약 {data?.summary.total_contracts ?? 0}건</strong>
          <button disabled={loading} onClick={reload} type="button">
            통합 현황 새로고침
          </button>
        </div>
      </header>

      {loading && !data ? (
        <p role="status">보험 대사 현황을 불러오는 중입니다.</p>
      ) : null}
      {error ? (
        <p role="alert" aria-live="polite">
          통합 보험 현황을 불러오지 못했습니다. 원장 세부 내용은 계속 사용할 수
          있습니다.
        </p>
      ) : null}
      {mutationError ? (
        <p role="alert">
          대사 상태를 변경하지 못했습니다. 현황을 새로고침한 뒤 다시 시도해
          주세요.
        </p>
      ) : null}
      {!loading && !error && data?.contracts.length === 0 ? (
        <p>이 가족 구성원에게 연결된 분석 계약이 없습니다.</p>
      ) : null}

      {data ? (
        <div
          aria-label="통합 보험 대사 요약"
          className="insurance-inventory-summary reconciliation-summary"
          role="group"
        >
          {[
            ["근거 준비", data.summary.evidence_ready_contracts],
            ["문서 보완", data.summary.documents_pending_contracts],
            ["연결 검토", data.summary.link_review_required_contracts],
            ["충돌", data.summary.conflict_contracts],
          ].map(([label, value]) => (
            <div className="insurance-inventory-summary-card" key={label}>
              <span>{label}</span>
              <strong>{value}</strong>
              <small>건</small>
            </div>
          ))}
        </div>
      ) : null}

      <div className="private-catalog-contracts">
        {data?.contracts.map((contract) => {
          const contractId = contract.knowledge_contract_id;
          const expanded = expandedId === contractId;
          const linkPending = pendingAction?.endsWith(contractId) ?? false;
          const selectedPolicyId = selectedPolicies[contractId] ?? "";
          return (
            <article className="private-catalog-contract" key={contractId}>
              <header>
                <div>
                  <p>{contract.insurer_display}</p>
                  <h3>{contract.product_display}</h3>
                </div>
                <span
                  className={`reconciliation-state reconciliation-state-${contract.reconciliation_state.toLowerCase()}`}
                >
                  {CONTRACT_STATE_COPY[contract.reconciliation_state]}
                </span>
              </header>
              <div className="private-catalog-contract-facts">
                <span>
                  {contract.certificate_decision === "MATCH"
                    ? "증권 가입 확인"
                    : "증권 가입 확인 필요"}
                </span>
                <span>{CURRENT_STATUS_COPY[contract.current_status]}</span>
                {contract.document_readiness ? (
                  <span>
                    {contract.document_readiness.completeness ===
                    "CERTIFICATE_AND_TERMS"
                      ? "증권+약관 준비"
                      : "약관 보완 필요"}
                  </span>
                ) : null}
              </div>
              {contract.reconciliation_state === "LINK_REVIEW_REQUIRED" &&
              contract.operational_link.decision !== "NO_MATCH" ? (
                <div className="reconciliation-link-controls">
                  {data.orphan_operational_contracts.length > 0 ? (
                    <label>
                      <span>앱 계약 직접 선택</span>
                      <select
                        aria-label={`${contract.product_display} 앱 계약 선택`}
                        disabled={linkPending}
                        onChange={(event) =>
                          setSelectedPolicies((current) => ({
                            ...current,
                            [contractId]: event.target.value,
                          }))
                        }
                        value={selectedPolicyId}
                      >
                        {data.orphan_operational_contracts.map((policy) => (
                          <option
                            key={policy.policy_contract_id}
                            value={policy.policy_contract_id}
                          >
                            {policy.insurer_display} · {policy.product_display}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : (
                    <p>연결할 앱 원장 계약이 없습니다.</p>
                  )}
                  <div>
                    {selectedPolicyId ? (
                      <button
                        aria-label={`${contract.product_display} 선택한 앱 계약과 같은 계약으로 확인`}
                        disabled={linkPending}
                        onClick={() => void linkSelected(contract)}
                        type="button"
                      >
                        같은 계약으로 확인
                      </button>
                    ) : null}
                    <button
                      aria-label={`${contract.product_display} 앱 계약과 서로 다른 계약으로 확인`}
                      disabled={linkPending}
                      onClick={() => void markDistinct(contract)}
                      type="button"
                    >
                      서로 다른 계약으로 확인
                    </button>
                  </div>
                </div>
              ) : contract.operational_link.decision === "NO_MATCH" ||
                contract.reconciliation_state === "CONFLICT" ? (
                <div className="reconciliation-link-controls compact">
                  <p>
                    {contract.operational_link.decision === "NO_MATCH"
                      ? "앱 원장과 서로 다른 계약으로 확인됨"
                      : "연결 충돌을 다시 검토해야 합니다."}
                  </p>
                  <button
                    disabled={linkPending}
                    onClick={() => void reopenLinkReview(contract)}
                    type="button"
                  >
                    앱 계약 연결 다시 검토
                  </button>
                </div>
              ) : null}
              <button
                aria-expanded={expanded}
                onClick={() => setExpandedId(expanded ? undefined : contractId)}
                type="button"
              >
                {contract.product_display} 상세 분석{" "}
                {expanded ? "닫기" : "보기"}
              </button>
              {expanded ? (
                detailLoading && !details[contractId] ? (
                  <p role="status">담보와 약관 근거를 불러오는 중입니다.</p>
                ) : detailError ? (
                  <p role="alert">상세 분석을 불러오지 못했습니다.</p>
                ) : details[contractId] ? (
                  <ContractAnalysis detail={details[contractId]} />
                ) : null
              ) : null}
            </article>
          );
        })}
      </div>

      {data ? (
        <section
          aria-labelledby="orphan-operational-policy-title"
          className="reconciliation-review-group"
        >
          <div className="insurance-inventory-section-heading compact">
            <div>
              <span>Operational-only</span>
              <h3 id="orphan-operational-policy-title">앱 원장 단독 계약</h3>
            </div>
            <strong>{data.orphan_operational_contracts.length}건</strong>
          </div>
          {data.orphan_operational_contracts.length ? (
            <ul>
              {data.orphan_operational_contracts.map((policy) => (
                <li key={policy.policy_contract_id}>
                  <strong>{policy.product_display}</strong>
                  <span>{policy.insurer_display}</span>
                  <span>
                    {policy.completeness === "CERTIFICATE_AND_TERMS"
                      ? "증권+약관 준비"
                      : "약관 보완 필요"}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p>분석 계약에 연결되지 않은 앱 원장 계약이 없습니다.</p>
          )}
        </section>
      ) : null}

      {data ? (
        <section
          aria-labelledby="reconciliation-document-work-title"
          className="reconciliation-review-group"
        >
          <div className="insurance-inventory-section-heading compact">
            <div>
              <span>Document work queue</span>
              <h3 id="reconciliation-document-work-title">판독·해결 작업</h3>
            </div>
            <strong>{data.unresolved_sources.length}건</strong>
          </div>
          {data.unresolved_sources.length ? (
            <ul>
              {data.unresolved_sources.map((source) => (
                <li key={source.document_batch_item_id}>
                  <strong>{source.display_label}</strong>
                  <span>{PROCESSING_COPY[source.processing_state]}</span>
                  <button
                    aria-label={`${source.display_label} 검토 완료로 작업에서 제외`}
                    disabled={
                      pendingAction ===
                      `dismiss:${source.document_batch_item_id}`
                    }
                    onClick={() => void dismissDocumentTask(source)}
                    type="button"
                  >
                    검토 완료로 제외
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p>현재 판독하거나 해결할 문서 작업이 없습니다.</p>
          )}
        </section>
      ) : null}
    </section>
  );
}
