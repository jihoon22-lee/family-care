import { useEffect, useState } from "react";

import type {
  KnowledgeContractDetailResponse,
  KnowledgeContractListItemResponse,
  KnowledgeFactResponse,
} from "../../api/generated";
import { getPrivateInsuranceContract } from "../../api/private-insurance-catalog";
import { usePrivateInsuranceCatalog } from "./usePrivateInsuranceCatalog";

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

function dateCopy(value: string): string {
  return value.replaceAll("-", ".");
}

function pageCopy(start: number, end: number): string {
  return start === end ? `${start}쪽` : `${start}–${end}쪽`;
}

function completenessCopy(
  value: KnowledgeContractListItemResponse["contract_document_completeness"],
): string {
  if (value === "CERTIFICATE_AND_TERMS") return "증권+약관";
  if (value === "CERTIFICATE_ONLY") return "증권만";
  if (value === "CERTIFICATE_REVIEW_REQUIRED_AND_TERMS") {
    return "증권 열람 확인 필요+약관";
  }
  return "문서 근거 확인 필요";
}

function currentStatusCopy(
  contract: KnowledgeContractListItemResponse,
): string {
  if (
    contract.current_status_decision === "MATCH" &&
    contract.current_status_authority === "USER_CONFIRMED_CURRENT_ENROLLMENT" &&
    contract.current_status_as_of
  ) {
    return `현재 가입 확인 · ${contract.current_status_as_of}`;
  }
  return "현재 상태 근거 확인 필요";
}

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
  const { data, error, loading } = usePrivateInsuranceCatalog(memberId);
  const [expandedId, setExpandedId] = useState<string>();
  const [details, setDetails] = useState<
    Record<string, KnowledgeContractDetailResponse>
  >({});
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState(false);

  useEffect(() => {
    setExpandedId(undefined);
    setDetails({});
    setDetailError(false);
  }, [memberId]);

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
            증권의 가입 담보, 현재 가입 확인, 약관 조항 근거를 서로 구분해
            정리합니다.
          </p>
        </div>
        <strong>총 {data?.items.length ?? 0}건</strong>
      </header>

      {loading && !data ? (
        <p role="status">전체 보험을 불러오는 중입니다.</p>
      ) : null}
      {error ? (
        <p role="status" aria-live="polite">
          전체 가입 보험 분석을 불러오지 못했습니다. 잠시 후 다시 확인해 주세요.
        </p>
      ) : null}
      {!loading && !error && data?.items.length === 0 ? (
        <p>이 가족 구성원에게 연결된 분석 계약이 없습니다.</p>
      ) : null}

      <div className="private-catalog-contracts">
        {data?.items.map((contract) => {
          const expanded = expandedId === contract.id;
          return (
            <article className="private-catalog-contract" key={contract.id}>
              <header>
                <div>
                  <p>{contract.insurer_display}</p>
                  <h3>{contract.product_display}</h3>
                </div>
                <span>{currentStatusCopy(contract)}</span>
              </header>
              <div className="private-catalog-contract-facts">
                <span>
                  {completenessCopy(contract.contract_document_completeness)}
                </span>
                <span>가입 담보 {contract.enrollment_match_count}개</span>
                <span>직접 근거 사실 {contract.semantic_fact_count}개</span>
                {contract.contract_start ? (
                  <span>계약 시작 {dateCopy(contract.contract_start)}</span>
                ) : null}
              </div>
              <button
                aria-expanded={expanded}
                onClick={() =>
                  setExpandedId(expanded ? undefined : contract.id)
                }
                type="button"
              >
                {contract.product_display} 상세 분석{" "}
                {expanded ? "닫기" : "보기"}
              </button>
              {expanded ? (
                detailLoading && !details[contract.id] ? (
                  <p role="status">담보와 약관 근거를 불러오는 중입니다.</p>
                ) : detailError ? (
                  <p role="alert">상세 분석을 불러오지 못했습니다.</p>
                ) : details[contract.id] ? (
                  <ContractAnalysis detail={details[contract.id]} />
                ) : null
              ) : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}
